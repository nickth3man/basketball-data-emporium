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

import asyncio
import contextlib
import datetime as _dt
import json
import logging
import re
import secrets
from collections.abc import AsyncIterator, Iterator
from dataclasses import asdict as _dc_asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from pydantic_ai.messages import ModelMessagesTypeAdapter

from . import otel
from .agent import (  # noqa: F401
    Clarification,
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
    ColumnSpec,
    IntentClassified,
    QueryFinished,
    QueryRef,
    QueryStarted,
    Reasoning,
    TableReady,
    TurnStarted,
)
from .repair import repair_sql
from .sessions import SessionNotFound, SessionStore, get_store
from .sqlgate import ValidationReport, validate_governed_sql

log = logging.getLogger(__name__)

_ANSWER_CHUNK_WINDOW = 80
_TABLE_PREVIEW_ROWS = 200

_ERR_DB_FAILED = "db_execute_failed"
_ERR_QUERY_TIMEOUT = "query_timeout"
_ERR_AGENT_FAILED = "agent_failed"
_ERR_GATE_FAILED = "gate_failed"
_ERR_COMPOSE_FAILED = "compose_failed"


_session_turn_locks: dict[str, asyncio.Lock] = {}


def _get_turn_lock(session_id: str) -> asyncio.Lock:
    """Return (or lazily create) the per-session asyncio.Lock.
    Called only from within run_turn, which runs on the single uvicorn
    event loop — dict access is atomic between await points, so no
    guard lock is needed.
    """
    if session_id not in _session_turn_locks:
        _session_turn_locks[session_id] = asyncio.Lock()
    return _session_turn_locks[session_id]


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


@dataclass
class _TurnContext:
    session_id: str
    message: str
    turn_id: str
    settings: Any
    store: SessionStore
    result: TurnResult | None

    def mark_not_answerable(self, composed: ComposedAnswer) -> ComposedAnswer:
        if self.result is not None:
            self.result.not_answerable = True
            self.result.not_answerable_note = composed.not_answerable_note
        return composed

    def write_model_log(self, usage: Any | None, error: BaseException | None) -> None:
        _write_model_log(
            self.settings,
            self.session_id,
            self.turn_id,
            template_id=None,
            usage=usage,
            error=error,
        )

    def write_query_log(
        self,
        *,
        sql: str | None,
        result: QueryResult | None,
        error: BaseException | None,
    ) -> None:
        _write_query_log(
            self.settings,
            self.session_id,
            self.turn_id,
            template_id=None,
            sql=sql,
            params=None,
            result=result,
            error=error,
        )


@dataclass
class _StepError:
    event: ChatError
    exception: BaseException


@dataclass
class _AgentStep:
    agent: Any
    deps: Any
    agent_result: Any
    plan: ClarifyPlan | NotAnswerablePlan | SqlPlan
    usage: Any


@dataclass
class _SqlPreparation:
    plan: SqlPlan | None = None
    report: ValidationReport | None = None
    not_answerable: ComposedAnswer | None = None
    attempted_sql: str | None = None
    failure: str | None = None
    error: _StepError | None = None


@dataclass
class _QueryExecution:
    result: QueryResult | None = None
    error: _StepError | None = None


async def _run_agent_step(context: _TurnContext) -> tuple[_AgentStep | None, _StepError | None]:
    with otel.span("agent.run", attributes={"session_id": context.session_id}) as agent_span:
        try:
            agent = get_agent()
            deps = await make_deps()
            history = _load_model_history(context.store, context.session_id)
            pending = context.store.get_pending_clarification(context.session_id)
            user_message = context.message
            if pending is not None:
                user_message = build_clarification_context_prefix(pending, context.message)

            run_kwargs: dict[str, Any] = {"deps": deps}
            if history:
                run_kwargs["message_history"] = history
            agent_result = await agent.run(user_message, **run_kwargs)
            plan = agent_result.output
            usage = agent_result.usage
            if agent_span is not None:
                with contextlib.suppress(Exception):
                    agent_span.set_attribute("answer_mode", plan.answer_mode)
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "agent.run failed; sid=%s turn_id=%s",
                context.session_id,
                context.turn_id,
            )
            return None, _StepError(
                ChatError(code=_ERR_AGENT_FAILED, message=f"agent failed: {type(exc).__name__}"),
                exc,
            )

    return _AgentStep(agent, deps, agent_result, plan, usage), None


def _sync_clarification_state(context: _TurnContext, plan: object) -> None:
    if isinstance(plan, ClarifyPlan):
        clarification = cast(Clarification, plan.clarification)
        options = list(clarification.options) if clarification.options else None
        state = ClarificationState(
            original_question=context.message,
            clarification_question=clarification.question,
            options=options,
        )
        try:
            context.store.set_pending_clarification(context.session_id, state)
        except Exception:  # noqa: BLE001
            log.exception("set_pending_clarification failed; sid=%s", context.session_id)
        return

    try:
        context.store.clear_pending_clarification(context.session_id)
    except Exception:  # noqa: BLE001
        log.exception("clear_pending_clarification failed; sid=%s", context.session_id)


async def _stream_not_answerable_plan(
    context: _TurnContext,
    plan: NotAnswerablePlan,
    usage: Any,
) -> AsyncIterator[ChatEvent]:
    composed = context.mark_not_answerable(compose_not_answerable(plan.not_answerable_note))
    async for event in _stream_composed_answer(composed=composed):
        yield event
    _safe_append_assistant(context.store, context.session_id, composed.answer)
    context.write_model_log(usage, None)


async def _prepare_sql_plan(
    context: _TurnContext,
    plan: SqlPlan,
    agent: Any,
    deps: Any,
) -> _SqlPreparation:
    db = deps.db
    try:
        report = await validate_governed_sql(plan.sql, db, deps.catalog)
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "pipeline: validate_governed_sql failed; sid=%s turn_id=%s",
            context.session_id,
            context.turn_id,
        )
        return _SqlPreparation(
            error=_StepError(
                ChatError(code=_ERR_GATE_FAILED, message=f"SQL gate failed: {type(exc).__name__}"),
                exc,
            )
        )

    failure: str | None = None
    if not report.valid:
        failure = "; ".join(report.errors) or "SQL validation failed"
        log.info(
            "pipeline: governed gate failed; attempting repair sid=%s err=%s",
            context.session_id,
            failure,
        )
    else:
        try:
            await db.dry_run(plan.sql)
        except DryRunError as exc:
            failure = str(exc.original)
            log.info(
                "pipeline: dry-run failed; attempting repair sid=%s err=%s",
                context.session_id,
                failure,
            )

    if failure is None:
        return _SqlPreparation(plan=plan, report=report)

    attempted_sql = plan.sql
    try:
        repaired_plan = await repair_sql(
            agent,
            deps,
            question=context.message,
            broken_sql=attempted_sql,
            error=failure,
            db=db,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "pipeline: repair_sql failed; sid=%s turn_id=%s",
            context.session_id,
            context.turn_id,
        )
        return _SqlPreparation(
            error=_StepError(
                ChatError(
                    code=_ERR_GATE_FAILED,
                    message=f"SQL repair failed: {type(exc).__name__}",
                ),
                exc,
            )
        )

    if repaired_plan is None or not repaired_plan.sql:
        return _SqlPreparation(
            not_answerable=compose_not_answerable(
                f"I couldn't fix the query: {failure}",
                attempted_sql=attempted_sql,
            ),
            attempted_sql=attempted_sql,
            failure=failure,
        )

    try:
        report = await validate_governed_sql(repaired_plan.sql, db, deps.catalog)
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "pipeline: post-repair validate_governed_sql failed; sid=%s turn_id=%s",
            context.session_id,
            context.turn_id,
        )
        return _SqlPreparation(
            error=_StepError(
                ChatError(code=_ERR_GATE_FAILED, message=f"SQL gate failed: {type(exc).__name__}"),
                exc,
            )
        )
    return _SqlPreparation(plan=repaired_plan, report=report)


async def _execute_sql_plan(
    context: _TurnContext,
    plan: SqlPlan,
    db: Any,
) -> _QueryExecution:
    row_limit = plan.result_contract.row_limit if plan.result_contract else None
    try:
        result = await db.execute(
            plan.sql,
            limit=row_limit,
            timeout_seconds=context.settings.query_timeout_seconds,
        )
        return _QueryExecution(result=result)
    except QueryTimeoutError as exc:
        log.warning(
            "pipeline: governed query timeout sid=%s timeout=%ds",
            context.session_id,
            context.settings.query_timeout_seconds,
        )
        message = (
            f"Query exceeded the {context.settings.query_timeout_seconds}s limit. "
            "Try a narrower question (fewer seasons, one player, or a specific team)."
        )
        return _QueryExecution(
            error=_StepError(ChatError(code=_ERR_QUERY_TIMEOUT, message=message), exc)
        )
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "pipeline: governed db.execute failed sid=%s err=%s",
            context.session_id,
            exc,
        )
        return _QueryExecution(
            error=_StepError(
                ChatError(code=_ERR_DB_FAILED, message=f"db execute failed: {type(exc).__name__}"),
                exc,
            )
        )


def _compose_sql_result(
    context: _TurnContext,
    plan: SqlPlan,
    query_ref: QueryRef,
    query_result: QueryResult,
) -> tuple[ComposedAnswer | None, _StepError | None]:
    try:
        composed = compose_governed(
            plan.result_contract or ResultContract(grain="results", answer_style="prose"),
            query_result,
            plan.sql,
            model_name=query_ref.tables[0] if query_ref.tables else None,
            question_interpretation=plan.question_interpretation,
        )
        return composed, None
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "pipeline: compose_governed failed; sid=%s turn_id=%s",
            context.session_id,
            context.turn_id,
        )
        return None, _StepError(
            ChatError(code=_ERR_COMPOSE_FAILED, message=f"compose failed: {type(exc).__name__}"),
            exc,
        )


def _query_execution_events(
    query_id: str,
    query_result: QueryResult,
) -> Iterator[ChatEvent]:
    yield QueryFinished(
        query_id=query_id,
        duration_ms=query_result.duration_ms,
        row_count=query_result.row_count,
        columns=list(query_result.columns),
        truncated=query_result.truncated,
    )
    yield TableReady(
        columns=[ColumnSpec(name=column, dtype=None) for column in query_result.columns],
        rows=query_result.rows[:_TABLE_PREVIEW_ROWS],
        row_count=query_result.row_count,
        truncated=len(query_result.rows) > _TABLE_PREVIEW_ROWS,
    )


def _composed_query_events(
    plan: SqlPlan,
    composed: ComposedAnswer,
) -> Iterator[ChatEvent]:
    yield Reasoning(
        summary=composed.reasoning_summary or "executed governed query",
        execution_plan=plan.sql[:200],
    )
    for citation in composed.citations:
        yield Citation(
            table_name=citation.table_name,
            metric_key=citation.metric_key,
            gap_key=citation.gap_key,
        )


async def _stream_sql_plan(
    context: _TurnContext,
    initial_plan: SqlPlan,
    agent: Any,
    deps: Any,
    usage: Any,
) -> AsyncIterator[ChatEvent]:
    preparation = await _prepare_sql_plan(context, initial_plan, agent, deps)
    if preparation.error is not None:
        yield preparation.error.event
        context.write_model_log(usage, preparation.error.exception)
        return

    if preparation.not_answerable is not None:
        composed = context.mark_not_answerable(preparation.not_answerable)
        async for event in _stream_composed_answer(composed=composed):
            yield event
        _safe_append_assistant(context.store, context.session_id, composed.answer)
        context.write_query_log(
            sql=preparation.attempted_sql,
            result=None,
            error=RuntimeError(preparation.failure or "SQL validation failed"),
        )
        context.write_model_log(usage, None)
        return

    if preparation.plan is None or preparation.report is None:
        raise AssertionError("SQL preparation completed without a plan and report")
    plan = preparation.plan
    query_ref = _query_ref(preparation.report, deps.catalog)

    yield IntentClassified(query_ref=query_ref, confidence=1.0)
    query_id = secrets.token_urlsafe(8)
    yield QueryStarted(query_id=query_id, query_ref=query_ref, sql=plan.sql)

    execution = await _execute_sql_plan(context, plan, deps.db)
    if execution.error is not None:
        yield execution.error.event
        context.write_query_log(sql=plan.sql, result=None, error=execution.error.exception)
        context.write_model_log(usage, execution.error.exception)
        return
    if execution.result is None:
        raise AssertionError("SQL execution completed without a result")
    query_result = execution.result

    for event in _query_execution_events(query_id, query_result):
        yield event

    composed, compose_error = _compose_sql_result(context, plan, query_ref, query_result)
    if compose_error is not None:
        yield compose_error.event
        context.write_query_log(sql=plan.sql, result=query_result, error=compose_error.exception)
        context.write_model_log(usage, compose_error.exception)
        return
    if composed is None:
        raise AssertionError("SQL composition completed without an answer")

    for event in _composed_query_events(plan, composed):
        yield event
    async for event in _stream_composed_answer(composed=composed):
        yield event

    _safe_append_assistant(context.store, context.session_id, composed.answer)
    context.write_query_log(sql=plan.sql, result=query_result, error=None)
    context.write_model_log(usage, None)


async def run_turn(
    session_id: str,
    message: str,
    *,
    result: TurnResult | None = None,
    store: SessionStore | None = None,
) -> AsyncIterator[ChatEvent]:
    """Yield the ordered event stream for one complete chat turn.

    The public generator keeps transport concerns out of the pipeline while
    delegating each plan mode to a focused handler. The first event is always
    :class:`TurnStarted`; every handled failure ends with one
    :class:`ChatError`.
    """
    lock = _get_turn_lock(session_id)
    async with lock:
        settings = get_settings()
        turn_id = secrets.token_urlsafe(8)
        yield TurnStarted(session_id=session_id, turn_id=turn_id, ts=_utcnow())

        actual_store = store or get_store()
        context = _TurnContext(
            session_id=session_id,
            message=message,
            turn_id=turn_id,
            settings=settings,
            store=actual_store,
            result=result,
        )
        _safe_append_user(actual_store, session_id, message)

        agent_step, agent_error = await _run_agent_step(context)
        if agent_error is not None:
            yield agent_error.event
            context.write_model_log(None, agent_error.exception)
            return
        if agent_step is None:
            raise AssertionError("agent step completed without a result")

        _safe_append_model_history(actual_store, session_id, agent_step.agent_result)
        plan = agent_step.plan
        _sync_clarification_state(context, plan)

        if isinstance(plan, ClarifyPlan):
            clarification = cast(Clarification, plan.clarification)
            if result is not None:
                result.clarification = True
            yield ClarificationNeeded(
                question=clarification.question,
                options=list(clarification.options) if clarification.options else None,
            )
            _safe_append_assistant(actual_store, session_id, clarification.question)
            context.write_model_log(agent_step.usage, None)
            return

        if isinstance(plan, NotAnswerablePlan):
            async for event in _stream_not_answerable_plan(context, plan, agent_step.usage):
                yield event
            return

        if isinstance(plan, SqlPlan):
            async for event in _stream_sql_plan(
                context,
                plan,
                agent_step.agent,
                agent_step.deps,
                agent_step.usage,
            ):
                yield event
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
