"""Turn orchestration: agent → governed SQL → DB → composer → SSE.

This module owns the **end-to-end sequence** of a single chat turn:

    user message → agent (Plan) → governed SQL validation → DB run →
    composer → stream ChatEvents

The public surface is one async generator:

    ``run_turn(session_id, message) -> AsyncIterator[ChatEvent]``

The streaming route (``chat_server.routes.chat.POST /api/chat/stream``)
just wraps it. Everything else — agent wiring, plan dispatch, governed
validation, DB execution, and logging — happens here.

Design notes
------------
* **Errors after ``turn_started``**: any uncaught exception in a turn
  step is converted into a ``ChatError(code, message)`` event and the
  full traceback is logged. The SSE stream terminates cleanly after
  the error event so the UI never hangs.
* **Query timeout**: the DB watchdog interrupts the executing DuckDB
  cursor and surfaces ``QueryTimeoutError`` to the pipeline.
* **Secret redaction**: model log writes never include the
  ``OPENROUTER_API_KEY`` (no live prompt or response bodies). The
  ``usage`` payload is dataclasses.asdict'd from the agent's
  ``RunUsage``; it carries token counts, not secrets.
* **Logging IO is non-fatal**: every ``_write_*`` call is wrapped in
  try/except so a permissions error never breaks the turn. The
  message still streams to the client.
* **Streaming chunks** are split by sentence boundaries, then by fixed
  ~80-char windows. True token-level streaming is a future enhancement
  (Phase 5 may layer it onto a smaller composer model call).
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import logging
import re
import secrets
from collections.abc import AsyncIterator
from dataclasses import asdict as _dc_asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic_ai.messages import ModelMessagesTypeAdapter

from . import otel
from .agent import (  # noqa: F401
    ClarifyPlan,
    NotAnswerablePlan,
    ResultContract,
    SqlPlan,
    get_agent,
    keep_last_messages_with_tools,
    make_deps,
)
from .clarify import ClarificationState, build_clarification_context_prefix
from .composer import ComposedAnswer, compose_governed, compose_not_answerable
from .config import get_settings
from .db import DryRunError, QueryResult, QueryTimeoutError
from .events import (
    AnswerDelta,
    AnswerFinished,
    ChatError,
    ChatEvent,
    Citation,
    ClarificationNeeded,
    IntentClassified,
    QueryFinished,
    QueryRef,
    QueryStarted,
    Reasoning,
    TurnStarted,
)
from .repair import repair_sql
from .sessions import SessionNotFound, SessionStore, get_store
from .sqlgate import ValidationReport, validate_governed_sql

log = logging.getLogger(__name__)

_ANSWER_CHUNK_WINDOW = 80

_ERR_DB_FAILED = "db_execute_failed"
_ERR_QUERY_TIMEOUT = "query_timeout"
_ERR_AGENT_FAILED = "agent_failed"


@dataclass
class TurnResult:
    """Out-of-band facts about a turn that no ``ChatEvent`` carries.

    The JSON route needs ``not_answerable`` / ``not_answerable_note``
    for its ``ChatResponse``, but adding them to the wire events would
    break the frozen SSE schema for information the stream consumer
    never uses. Callers that care pass an instance to :func:`run_turn`;
    the pipeline mutates it as the turn resolves. The SSE route passes
    nothing.
    """

    not_answerable: bool = False
    not_answerable_note: str | None = None
    clarification: bool = False


def _query_ref(report: ValidationReport, catalog) -> QueryRef:  # type: ignore[no-untyped-def]
    """Classify final validated tables as catalog-backed or warehouse access."""
    tables = sorted(report.tables_referenced)
    catalog_tables = set()
    if catalog:
        catalog_tables = {model.base_table.name for model in catalog.models.values()}
    source = "catalog" if tables and set(tables).issubset(catalog_tables) else "warehouse"
    return QueryRef(source=source, tables=tables)


async def run_turn(
    session_id: str,
    message: str,
    *,
    result: TurnResult | None = None,
    store: SessionStore | None = None,
) -> AsyncIterator[ChatEvent]:
    """Async generator yielding one ``ChatEvent`` per pipeline step.

    The first event is always ``TurnStarted``; the last is one of
    ``AnswerFinished`` (happy path), ``ClarificationNeeded``,
    ``AnswerFinished`` carrying the not-answerable note, or ``ChatError``.

    ``session_id`` is required by Phase 4 — the streaming route creates
    one if the client didn't supply it (mirrors the non-streaming
    behaviour). The route is responsible for that step so this generator
    stays pure.

    ``result``, when given, is filled with the out-of-band turn facts
    (see :class:`TurnResult`) so the JSON route can build its response
    without any change to the event schema.

    Failure handling
    ----------------
    Every step is wrapped in try/except; an uncaught exception yields
    one ``ChatError`` event and the generator returns. The traceback is
    logged in full; the wire ``message`` is a redacted summary.
    """
    settings = get_settings()
    turn_id = secrets.token_urlsafe(8)
    ts = _utcnow()

    def _mark_not_answerable(composed: ComposedAnswer) -> ComposedAnswer:
        """Record the not-answerable outcome on the caller's TurnResult."""
        if result is not None:
            result.not_answerable = True
            result.not_answerable_note = composed.not_answerable_note
        return composed

    yield TurnStarted(session_id=session_id, turn_id=turn_id, ts=ts)

    store = store or get_store()
    _safe_append_user(store, session_id, message)

    with otel.span("agent.run", attributes={"session_id": session_id}) as agent_span:
        try:
            agent = get_agent()
            deps = await make_deps()
            history = _load_model_history(store, session_id)
            pending = store.get_pending_clarification(session_id)
            user_message_text = message
            if pending is not None:
                user_message_text = build_clarification_context_prefix(pending, message)
            run_kwargs: dict[str, Any] = {"deps": deps}
            if history:
                run_kwargs["message_history"] = history
            agent_result = await agent.run(user_message_text, **run_kwargs)
            plan = agent_result.output
            usage_obj = agent_result.usage
            if agent_span is not None:
                with contextlib.suppress(Exception):
                    agent_span.set_attribute("answer_mode", plan.answer_mode)
        except Exception as exc:  # noqa: BLE001
            log.exception("agent.run failed; sid=%s turn_id=%s", session_id, turn_id)
            yield ChatError(code=_ERR_AGENT_FAILED, message=f"agent failed: {type(exc).__name__}")
            _write_model_log(settings, session_id, turn_id, template_id=None, usage=None, error=exc)
            return

    _safe_append_model_history(store, session_id, agent_result)

    # Clarify-state side effect: runs in one block above the dispatch so
    # every non-clarify outcome auto-clears stale state.
    if isinstance(plan, ClarifyPlan):
        clar = plan.clarification
        options_list: list[str] | None = list(clar.options) if clar.options else None
        state = ClarificationState(
            original_question=message,
            clarification_question=clar.question,
            options=options_list,
        )
        try:
            store.set_pending_clarification(session_id, state)
        except Exception:  # noqa: BLE001
            log.exception("set_pending_clarification failed; sid=%s", session_id)
    else:
        try:
            store.clear_pending_clarification(session_id)
        except Exception:  # noqa: BLE001
            log.exception("clear_pending_clarification failed; sid=%s", session_id)

    # Dispatch on the plan's type — each branch is exclusive, so there is
    # no ordering contract to document or violate.
    if isinstance(plan, ClarifyPlan):
        clar = plan.clarification
        if result is not None:
            result.clarification = True
        yield ClarificationNeeded(
            question=clar.question,
            options=list(clar.options) if clar.options else None,
        )
        _safe_append_assistant(store, session_id, clar.question)
        _write_model_log(
            settings,
            session_id,
            turn_id,
            template_id=None,
            usage=usage_obj,
            error=None,
        )
        return

    if isinstance(plan, NotAnswerablePlan):
        note = plan.not_answerable_note
        composed = _mark_not_answerable(compose_not_answerable(note))
        async for ev in _stream_composed_answer(
            composed=composed,
        ):
            yield ev
        _safe_append_assistant(store, session_id, composed.answer)
        _write_model_log(
            settings,
            session_id,
            turn_id,
            template_id=None,
            usage=usage_obj,
            error=None,
        )
        return

    if isinstance(plan, SqlPlan):
        db = deps.db
        report = await validate_governed_sql(plan.sql, db, deps.catalog)
        failure: str | None = None
        if not report.valid:
            failure = "; ".join(report.errors) or "SQL validation failed"
            log.info(
                "pipeline: governed gate failed; attempting repair sid=%s err=%s",
                session_id,
                failure,
            )
        else:
            try:
                await db.dry_run(plan.sql)
            except DryRunError as exc:
                failure = str(exc.original)
                log.info(
                    "pipeline: dry-run failed; attempting repair sid=%s err=%s",
                    session_id,
                    failure,
                )

        if failure is not None:
            attempted_sql = plan.sql
            repaired_plan = await repair_sql(
                agent,
                deps,
                question=message,
                broken_sql=attempted_sql,
                error=failure,
                db=db,
            )
            if repaired_plan is None or not repaired_plan.sql:
                composed = _mark_not_answerable(
                    compose_not_answerable(
                        f"I couldn't fix the query: {failure}",
                        attempted_sql=attempted_sql,
                    )
                )
                async for ev in _stream_composed_answer(
                    composed=composed,
                ):
                    yield ev
                _safe_append_assistant(store, session_id, composed.answer)
                _write_query_log(
                    settings,
                    session_id,
                    turn_id,
                    template_id=None,
                    sql=attempted_sql,
                    params=None,
                    result=None,
                    error=RuntimeError(failure),
                )
                _write_model_log(
                    settings,
                    session_id,
                    turn_id,
                    template_id=None,
                    usage=usage_obj,
                    error=None,
                )
                return
            plan = repaired_plan
            # repair_sql only returns plans that passed the gate and dry-run.
            report = await validate_governed_sql(plan.sql, db, deps.catalog)

        query_ref = _query_ref(report, deps.catalog)

        yield IntentClassified(query_ref=query_ref, confidence=1.0)
        query_id = secrets.token_urlsafe(8)
        yield QueryStarted(query_id=query_id, query_ref=query_ref, sql=plan.sql)

        row_limit = plan.result_contract.row_limit if plan.result_contract else None
        try:
            query_result: QueryResult = await db.execute(
                plan.sql,
                limit=row_limit,
                timeout_seconds=settings.query_timeout_seconds,
            )
        except QueryTimeoutError as exc:
            log.warning(
                "pipeline: governed query timeout sid=%s timeout=%ds",
                session_id,
                settings.query_timeout_seconds,
            )
            yield ChatError(
                code=_ERR_QUERY_TIMEOUT,
                message=(
                    f"Query exceeded the {settings.query_timeout_seconds}s limit. "
                    "Try a narrower question (fewer seasons, one player, or a specific team)."
                ),
            )
            _write_query_log(
                settings,
                session_id,
                turn_id,
                template_id=None,
                sql=plan.sql,
                params=None,
                result=None,
                error=exc,
            )
            _write_model_log(
                settings,
                session_id,
                turn_id,
                template_id=None,
                usage=usage_obj,
                error=exc,
            )
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("pipeline: governed db.execute failed sid=%s err=%s", session_id, exc)
            yield ChatError(
                code=_ERR_DB_FAILED,
                message=f"db execute failed: {type(exc).__name__}",
            )
            _write_query_log(
                settings,
                session_id,
                turn_id,
                template_id=None,
                sql=plan.sql,
                params=None,
                result=None,
                error=exc,
            )
            _write_model_log(
                settings,
                session_id,
                turn_id,
                template_id=None,
                usage=usage_obj,
                error=exc,
            )
            return

        yield QueryFinished(
            query_id=query_id,
            duration_ms=query_result.duration_ms,
            row_count=query_result.row_count,
            columns=list(query_result.columns),
            truncated=query_result.truncated,
        )

        composed = compose_governed(
            plan.result_contract or ResultContract(grain="results", answer_style="prose"),
            query_result,
            plan.sql,
            model_name=query_ref.tables[0] if query_ref.tables else None,
            question_interpretation=plan.question_interpretation,
        )
        yield Reasoning(
            summary=composed.reasoning_summary or "executed governed query",
            execution_plan=plan.sql[:200],
        )
        for cite in composed.citations:
            yield Citation(
                table_name=cite.table_name,
                metric_key=cite.metric_key,
                gap_key=cite.gap_key,
            )
        async for ev in _stream_composed_answer(
            composed=composed,
        ):
            yield ev

        _safe_append_assistant(store, session_id, composed.answer)
        _write_query_log(
            settings,
            session_id,
            turn_id,
            template_id=None,
            sql=plan.sql,
            params=None,
            result=query_result,
            error=None,
        )
        _write_model_log(
            settings,
            session_id,
            turn_id,
            template_id=None,
            usage=usage_obj,
            error=None,
        )
        return

    raise AssertionError(f"unhandled plan type: {type(plan).__name__}")


def _stream_answer_chunks(answer: str) -> list[str]:
    """Split ``answer`` into chunks for ``AnswerDelta`` events.

    Strategy: split on sentence boundaries (``. `` / `? ` / `! `) first
    so each chunk is a complete unit; if any sentence is longer than
    ``_ANSWER_CHUNK_WINDOW``, fall back to fixed-window chunks. Empty
    input yields one empty chunk (so the UI gets a delta before the
    finished event and doesn't look frozen on no-content turns).
    """
    if not answer:
        return [""]
    parts = re.split(r"(?<=[.!?])\s+", answer)
    chunks: list[str] = []
    for part in parts:
        if not part:
            continue
        if len(part) <= _ANSWER_CHUNK_WINDOW:
            chunks.append(part)
            continue
        for i in range(0, len(part), _ANSWER_CHUNK_WINDOW):
            chunks.append(part[i : i + _ANSWER_CHUNK_WINDOW])
    return chunks or [""]


async def _stream_composed_answer(
    composed,  # type: ignore[no-untyped-def]
) -> AsyncIterator[ChatEvent]:
    """Yield ``AnswerDelta``s then a final ``AnswerFinished``.

    ``Reasoning`` and ``Citation`` events are emitted by the caller; this
    helper only handles answer prose. For very-short answers
    we still emit at least one ``AnswerDelta`` before the
    ``AnswerFinished`` so the reducer's "is streaming" flag works
    uniformly.
    """
    answer = composed.answer
    for chunk in _stream_answer_chunks(answer):
        yield AnswerDelta(delta=chunk)
    yield AnswerFinished(answer=answer)


def _safe_append_user(store, session_id: str, content: str) -> None:
    """Append the user message; swallow IO errors."""
    try:
        store.append_message(session_id, "user", content)
    except SessionNotFound:
        log.exception("session store missing for sid=%s (user msg)", session_id)
    except Exception:  # noqa: BLE001
        log.exception("session store failed (user msg); sid=%s", session_id)


def _safe_append_assistant(store, session_id: str, content: str) -> None:
    """Append the assistant message; swallow IO errors."""
    try:
        store.append_message(session_id, "assistant", content)
    except SessionNotFound:
        log.exception("session store missing for sid=%s (assistant msg)", session_id)
    except Exception:  # noqa: BLE001
        log.exception("session store failed (assistant msg); sid=%s", session_id)


def _load_model_history(store, session_id: str) -> list:
    """Load the prior ModelMessage history for `session_id`, validated + trimmed.

    Returns an empty list when the snapshot is absent (fresh session) or
    when any step fails. The pipeline/route callers always treat an
    empty list as "no prior context" — a corrupted history file should
    never crash a turn.

    Three failure paths collapse to ``[]``:

    1. ``store.load_model_history`` raises (IO error, corrupt JSON).
    2. ``ModelMessagesTypeAdapter.validate_python`` rejects the parsed
       list (schema drift between pydantic-ai versions).
    3. ``keep_last_messages_with_tools`` raises (defensive — currently
       cannot, but mirrors the surrounding robustness pattern).
    """
    try:
        raw = store.load_model_history(session_id)
    except Exception:  # noqa: BLE001
        log.warning("model history load failed; degrading to empty sid=%s", session_id)
        return []
    if not raw:
        return []
    try:
        messages = ModelMessagesTypeAdapter.validate_python(raw)
        return keep_last_messages_with_tools(list(messages), n=20)
    except Exception:  # noqa: BLE001
        log.warning("model history validation/trim failed; degrading to empty sid=%s", session_id)
        return []


def _safe_append_model_history(store, session_id: str, result) -> None:
    """Persist the post-call `result.all_messages_json()` snapshot.

    Best-effort: a failed write must not break the turn. Mirrors the
    robustness pattern of `_safe_append_user` / `_safe_append_assistant`:
    log the failure, return ``None``, let the turn continue.
    """
    try:
        payload = result.all_messages_json()
    except Exception:  # noqa: BLE001
        log.exception("model history serialization failed sid=%s", session_id)
        return
    try:
        store.append_model_history(session_id, payload)
    except SessionNotFound:
        log.exception("session store missing for sid=%s (model history)", session_id)
    except Exception:  # noqa: BLE001
        log.exception("model history persist failed sid=%s", session_id)


def _utcnow() -> _dt.datetime:
    """Timezone-aware UTC now; centralised for testability."""
    return _dt.datetime.now(tz=_dt.UTC)


def _today_stamp() -> str:
    """YYYY-MM-DD UTC — the daily rotation key for log dirs."""
    return _utcnow().strftime("%Y-%m-%d")


def _safe_write(path: Path, content: str) -> None:
    """Write ``content`` to ``path``; create parents; swallow all IO errors."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except Exception:  # noqa: BLE001
        log.exception("failed to write log file %s", path)


def _safe_append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append one JSON line to a JSONL file; swallow IO errors."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str))
            fh.write("\n")
    except Exception:  # noqa: BLE001
        log.exception("failed to append JSONL line to %s", path)


def _write_query_log(
    settings,
    session_id: str,
    turn_id: str,
    *,
    template_id: str | None,
    sql: str | None,
    params: dict[str, Any] | None,
    result: QueryResult | None,
    error: BaseException | None,
) -> None:
    """Persist governed SQL plus its result preview or execution error.

    Two sibling files:
        <turn_id>.<query-ref>.sql           — the governed SQL text
        <turn_id>.<query-ref>.result.json   — columns, row_count,
                                             first ~50 rows, duration
                                             ms, truncated flag, error
                                             (if any)

    Logging IO is non-fatal: a failure is logged once and the turn
    continues.
    """
    base_dir = Path(settings.chat_log_dir) / "queries" / _today_stamp() / session_id
    # JSONL/query-log payload fields retain their legacy name in this phase.
    tid = re.sub(r"[^A-Za-z0-9_.-]+", "_", template_id or "unknown")
    if sql is not None:
        _safe_write(base_dir / f"{turn_id}.{tid}.sql", sql)
    payload: dict[str, Any] = {
        "turn_id": turn_id,
        "template_id": template_id,
        "ts": _utcnow().isoformat(),
        "params": params or {},
    }
    if result is not None:
        payload.update(
            {
                "columns": list(result.columns),
                "row_count": result.row_count,
                "duration_ms": result.duration_ms,
                "truncated": result.truncated,
                "preview_rows": result.rows[:50],
            }
        )
    if error is not None:
        payload["error"] = f"{type(error).__name__}: {error}"
    _safe_write(base_dir / f"{turn_id}.{tid}.result.json", json.dumps(payload, default=str))


def _write_model_log(
    settings,
    session_id: str,
    turn_id: str,
    *,
    template_id: str | None,
    usage: Any | None,
    error: BaseException | None,
) -> None:
    """Append one JSONL line under logs/model/ — token usage, redacted.

    Carries: turn_id, ts, template_id, usage (RunUsage dataclass → dict),
    and an optional error marker. Full model CoT and request/response
    bodies are NEVER included here.

    `usage` may be a Pydantic AI ``RunUsage`` dataclass (or None);
    ``dataclasses.asdict`` handles both real dataclasses and the
    lightweight Pydantic equivalent.
    """
    record: dict[str, Any] = {
        "turn_id": turn_id,
        "ts": _utcnow().isoformat(),
        "template_id": template_id,
        "usage": _dc_asdict(usage) if usage is not None else None,
    }
    if error is not None:
        record["error"] = f"{type(error).__name__}: {error}"
    path = Path(settings.chat_log_dir) / "model" / _today_stamp() / f"{session_id}.jsonl"
    _safe_append_jsonl(path, record)


__all__ = ["run_turn"]
