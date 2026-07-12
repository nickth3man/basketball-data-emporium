"""Multi-turn replay harness (EVALS.md §4).

The eval suite MUST exercise the real session-store + pipeline machinery
because that is where multi-turn bugs actually live: model-history
trimming, clarification-state set/clear/TTL, prompt enrichment via the
clarification prefix. Concatenating turns into one prompt would silently
mask all of them. So per ``EvalRow``:

1. Create a fresh ``SessionStore`` rooted at a temp directory the
   caller provides (one store per row).
2. Call ``run_turn`` once per scripted user message (initial question
   for both ``single`` and ``multi`` rows; then ``user_followup_1`` and
   ``user_followup_2`` for ``multi``).
3. Collect every ``ChatEvent`` yielded. The turn-1 events drive Layer-1
   (plan grading). The final-turn events drive Layer-2 (result
   grading) where gold is present.

The harness NEVER shortcuts: the agent really runs, the store really
writes ``.model.jsonl`` and ``.clarify.json``, the clarification state
machine really cycles. The only thing the harness skips is the SSE wire
format -- it consumes the ``ChatEvent`` objects directly.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from chat_server.events import (
    ChatEvent,
    ClarificationNeeded,
    QueryFinished,
    QueryStarted,
    TableReady,
)
from chat_server.pipeline import run_turn
from chat_server.sessions import SessionStore

from .loader import EvalRow


@dataclass
class TurnTrace:
    """One turn's collected events plus the plan-equivalent derived facts.

    ``mode`` / ``sql`` / ``tables_referenced`` / ``gate_pass`` are
    derived from the event stream (the typed ``QueryPlan`` is internal
    to ``run_turn``); see ``_derive_plan`` for the mapping.
    """

    events: list[ChatEvent] = field(default_factory=list)
    mode: str = "not_answerable"
    sql: str | None = None
    gate_pass: bool | None = None
    tables_referenced: set[str] = field(default_factory=set)


@dataclass
class ReplayResult:
    """Outcome of replaying one eval row.

    ``turns`` lists each scripted turn in order (length 1 for
    ``single`` rows; 2-3 for ``multi``). The first entry is always the
    initial-question turn; Layer-1 grades that entry. ``final_columns``
    + ``final_rows`` carry the warehouse result from the LAST turn
    whose plan was an execute_sql -- Layer-2 grades against gold if
    present.
    """

    session_id: str
    turns: list[TurnTrace] = field(default_factory=list)
    final_columns: list[str] = field(default_factory=list)
    final_rows: list[dict] = field(default_factory=list)
    final_sql: str | None = None
    infrastructure_error: str | None = None


# --- plan extraction from events -----------------------------------------


def _derive_plan(events: list[ChatEvent]) -> TurnTrace:
    """Inspect ``events`` and produce the plan-equivalent facts.

    Mapping:

    * ``ClarificationNeeded`` present -> ``"clarify"``.
    * ``QueryStarted`` present -> ``"execute_sql"``.
    * ``ChatError`` with no other mode-bearing events -> ``"not_answerable"``.
    * Default (no recognised mode-bearing event) -> ``"not_answerable"``.

    The SQL and tables are taken from ``QueryStarted``. Gate facts are populated by
    the caller via ``run_governed_gate`` -- the trace records them so
    Layer-1 can grade in a single pass.
    """
    trace = TurnTrace(events=list(events))

    has_clarification = any(isinstance(ev, ClarificationNeeded) for ev in events)
    if has_clarification:
        trace.mode = "clarify"
        return trace

    qs = next((ev for ev in events if isinstance(ev, QueryStarted)), None)
    if qs is not None:
        trace.mode = "execute_sql"
        trace.sql = qs.sql
        trace.tables_referenced = set(qs.query_ref.tables)
        return trace

    # No recogniseable mode-bearing event -> not_answerable.
    trace.mode = "not_answerable"
    return trace


# --- session-store plumbing ----------------------------------------------


def _build_session_store(sessions_root: Path) -> SessionStore:
    """Construct a fresh ``SessionStore`` rooted at ``sessions_root``."""
    sessions_root.mkdir(parents=True, exist_ok=True)
    return SessionStore(sessions_root)


# --- replay driver -------------------------------------------------------


async def _drain(session_id: str, message: str, store: SessionStore) -> list[ChatEvent]:
    """Drain one ``run_turn`` call into a list of ``ChatEvent``."""
    return [ev async for ev in run_turn(session_id, message, store=store)]


async def _gate_sql(sql: str) -> tuple[bool, set[str]]:
    """Run ``validate_governed_sql`` against the live catalog.

    Returns ``(gate_pass, tables_referenced)``. Used only when the
    replayed plan was an execute_sql; for clarify / not_answerable /
    template plans the caller skips the gate.
    """
    try:
        from chat_server.db import get_db
        from chat_server.semantic_catalog import load_catalog
        from chat_server.sqlgate import validate_governed_sql
    except Exception:
        # Catalog loader or gate failure -> treat as gate-fail; the
        # grader will see ``gate_pass=False`` and record tables_check.
        return False, set()
    try:
        catalog = load_catalog()
    except Exception:
        return False, set()
    if catalog is None:
        return False, set()
    try:
        report = await validate_governed_sql(sql, get_db(), catalog)
    except Exception:
        return False, set()
    return bool(report.valid), set(report.tables_referenced)


async def _apply_gate_result(trace: TurnTrace) -> None:
    if trace.mode != "execute_sql" or not trace.sql:
        return
    trace.gate_pass, trace.tables_referenced = await _gate_sql(trace.sql)


def _capture_final_result(
    result: ReplayResult,
    trace: TurnTrace,
    events: list[ChatEvent],
) -> None:
    """Copy the last governed query's SQL and preview rows into ``result``."""
    if trace.mode != "execute_sql" or not trace.sql:
        return

    result.final_sql = trace.sql
    query_finished = next((event for event in events if isinstance(event, QueryFinished)), None)
    table_ready = next((event for event in events if isinstance(event, TableReady)), None)
    if query_finished is not None:
        result.final_columns = list(query_finished.columns)
    if table_ready is not None:
        result.final_rows = list(table_ready.rows)


async def replay_row(row: EvalRow, sessions_root: Path) -> ReplayResult:
    """Replay one eval row end-to-end through the real pipeline.

    Parameters
    ----------
    row
        The CSV row to replay.
    sessions_root
        Directory under which a fresh ``sessions/`` tree will be created
        (one per row, so a 70-row run never collides).

    Returns
    -------
    ReplayResult
        Per-turn event traces + the final execute_sql result (if any).
    """
    store = _build_session_store(sessions_root)
    meta = store.create(title=f"eval:{row.conversation_id}")
    session_id = meta.id

    result = ReplayResult(session_id=session_id)

    for message in row.scripted_turns:
        events = await _drain(session_id, message, store)
        trace = _derive_plan(events)
        await _apply_gate_result(trace)
        result.turns.append(trace)
        _capture_final_result(result, trace, events)

    return result


# --- sync wrapper for tests ----------------------------------------------


def replay_row_sync(row: EvalRow, sessions_root: Path) -> ReplayResult:
    """Synchronous wrapper around :func:`replay_row` for tests.

    pytest-asyncio in auto mode wraps coroutine test functions, so
    ``replay_row`` itself can be awaited directly; this wrapper exists
    so non-asyncio callers (e.g. the snapshot script, ad-hoc CLI
    inspection) can drive the harness without writing ``asyncio.run``.
    """
    return asyncio.run(replay_row(row, sessions_root))


__all__ = ["ReplayResult", "TurnTrace", "replay_row", "replay_row_sync"]


def _iter_events(traces: Iterator[TurnTrace]) -> Iterator[ChatEvent]:  # pragma: no cover
    """Yield every event across every turn (diagnostic helper)."""
    for trace in traces:
        yield from trace.events
