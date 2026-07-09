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
    IntentClassified,
    QueryStarted,
)
from chat_server.pipeline import run_turn
from chat_server.sessions import SessionStore, reset_store_for_tests

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


# --- plan extraction from events -----------------------------------------

# Sentinel prefix used by the governed-SQL pipeline when it emits
# IntentClassified (see pipeline.py around the execute_sql branch).
_GOVERNED_SENTINEL_PREFIX = "semantic:"


def _derive_plan(events: list[ChatEvent]) -> TurnTrace:
    """Inspect ``events`` and produce the plan-equivalent facts.

    Mapping:

    * ``ClarificationNeeded`` present -> ``"clarify"``.
    * ``IntentClassified`` present with a sentinel-prefixed template id
      -> ``"execute_sql"`` (governed path).
    * ``IntentClassified`` present with a non-sentinel template id
      -> ``"template"`` (legacy path).
    * ``ChatError`` with no other mode-bearing events -> ``"not_answerable"``.
    * Default (no recognised mode-bearing event) -> ``"not_answerable"``.

    The SQL is taken from ``QueryStarted.sql`` (always emitted on the
    execute_sql + template paths). Gate / table facts are populated by
    the caller via ``run_governed_gate`` -- the trace records them so
    Layer-1 can grade in a single pass.
    """
    trace = TurnTrace(events=list(events))

    has_clarification = any(isinstance(ev, ClarificationNeeded) for ev in events)
    if has_clarification:
        trace.mode = "clarify"
        return trace

    intent = next((ev for ev in events if isinstance(ev, IntentClassified)), None)
    if intent is not None:
        tid = getattr(intent, "template_id", "") or ""
        if tid.startswith(_GOVERNED_SENTINEL_PREFIX):
            trace.mode = "execute_sql"
        else:
            trace.mode = "template"
        # SQL is best-effort: QueryStarted is emitted on both execute_sql
        # and template paths, but we tolerate its absence (e.g. a
        # not_answerable plan that nonetheless emitted IntentClassified
        # by accident).
        qs = next((ev for ev in events if isinstance(ev, QueryStarted)), None)
        if qs is not None:
            trace.sql = qs.sql
        return trace

    # No recogniseable mode-bearing event -> not_answerable.
    trace.mode = "not_answerable"
    return trace


# --- session-store plumbing ----------------------------------------------


def _build_session_store(sessions_root: Path) -> SessionStore:
    """Construct a fresh ``SessionStore`` rooted at ``sessions_root``.

    Also clears the process-wide store singleton so any subsequent
    ``run_turn`` call (which fetches the store via ``get_store()``)
    picks up this fresh instance. We pass it explicitly into ``run_turn``
    by monkey-patching the module-level ``get_store`` reference for the
    duration of the replay.
    """
    sessions_root.mkdir(parents=True, exist_ok=True)
    store = SessionStore(sessions_root)
    reset_store_for_tests()
    # ``get_store()`` is referenced inside ``run_turn`` as a module-level
    # lookup; we patch the symbol in the pipeline's namespace so the
    # call resolves to our temp store without touching the real one.
    import chat_server.pipeline as pipeline_module

    pipeline_module.get_store = lambda: store  # type: ignore[assignment]
    return store


# --- replay driver -------------------------------------------------------


async def _drain(session_id: str, message: str) -> list[ChatEvent]:
    """Drain one ``run_turn`` call into a list of ``ChatEvent``."""
    return [ev async for ev in run_turn(session_id, message)]


def _gate_sql(sql: str) -> tuple[bool, set[str]]:
    """Run ``validate_governed_sql`` against the live catalog.

    Returns ``(gate_pass, tables_referenced)``. Used only when the
    replayed plan was an execute_sql; for clarify / not_answerable /
    template plans the caller skips the gate.
    """
    try:
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
        report = validate_governed_sql(sql, catalog)
    except Exception:
        return False, set()
    return bool(report.valid), set(report.tables_referenced)


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

    for _turn_index, message in enumerate(row.scripted_turns):
        events = await _drain(session_id, message)
        trace = _derive_plan(events)
        if trace.mode == "execute_sql" and trace.sql:
            gate_pass, tables = _gate_sql(trace.sql)
            trace.gate_pass = gate_pass
            trace.tables_referenced = tables
        result.turns.append(trace)

        # Capture the final execute_sql result for Layer-2. If the last
        # turn was clarify / not_answerable / template we leave the
        # final_columns/final_rows empty -- Layer-2 will skip.
        if trace.mode == "execute_sql" and trace.sql:
            qs = next(
                (ev for ev in events if isinstance(ev, QueryStarted)),
                None,
            )
            if qs is not None and qs.sql:
                result.final_sql = qs.sql
            # QueryFinished carries the columns + row_count; rows are
            # not surfaced in events today (the pipeline streams a
            # preview-capped TableReady but the full result lives on
            # disk). We populate final_rows from TableReady if present
            # (good enough for Layer-2's name/number matching; a
            # tighter match would require an extra execute call, which
            # the Layer-2 helper ``execute_plan_sql`` provides).
            from chat_server.events import QueryFinished, TableReady

            qf = next((ev for ev in events if isinstance(ev, QueryFinished)), None)
            tr = next((ev for ev in events if isinstance(ev, TableReady)), None)
            if qf is not None:
                result.final_columns = list(qf.columns)
            if tr is not None:
                result.final_rows = list(tr.rows)

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
