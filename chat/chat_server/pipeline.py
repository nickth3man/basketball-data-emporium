"""Turn orchestration: agent → template → DB → composer → SSE (PLAN §7.7).

This module owns the **end-to-end sequence** of a single chat turn:

    user message → agent (QueryPlan) → template + params → DB run →
    composer → stream ChatEvents

The public surface is one async generator:

    ``run_turn(session_id, message) -> AsyncIterator[ChatEvent]``

The streaming route (``chat_server.routes.chat.POST /api/chat/stream``)
just wraps it. Everything else — agent wiring, template lookup, DB
execution, logging — happens here.

Design notes
------------
* **Errors after ``turn_started``**: any uncaught exception in a turn
  step is converted into a ``ChatError(code, message)`` event and the
  full traceback is logged. The SSE stream terminates cleanly after
  the error event so the UI never hangs.
* **Query timeout**: ``asyncio.wait_for`` cancels the await on the DB
  thread; the thread itself keeps running until DuckDB finishes
  (acceptable for v1; Phase 7 may wire a cancellation token through
  to ``duckdb.interrupt()``).
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
import datetime as _dt
import json
import logging
import re
import secrets
from collections.abc import AsyncIterator
from dataclasses import asdict as _dc_asdict
from pathlib import Path
from typing import Any

from .agent import get_agent, make_deps  # noqa: F401 — re-exported by tests via pipeline
from .composer import compose, compose_not_answerable
from .config import get_settings
from .db import QueryResult, get_db
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
    QueryStarted,
    Reasoning,
    TableReady,
    TurnStarted,
)
from .sessions import SessionNotFound, get_store
from .templates import TemplateNotFound, get_template
from .validation import validate_template_sql

log = logging.getLogger(__name__)

#: Number of rows sent inline in a ``table_ready`` event. The full result
#: stays in the query log on disk (Phase 2 stubbed the artifact fetch);
#: the preview is what the UI renders immediately.
_TABLE_PREVIEW_ROWS = 200

#: Soft chunk size for ``AnswerDelta`` splitting. Final answers are
#: short (1–3 sentences); we split by sentence first, then by these
#: windows if a sentence is unreasonably long.
_ANSWER_CHUNK_WINDOW = 80

#: Code tokens for the structured ``error`` event. Keep stable — the UI
#: switch is keyed on these.
_ERR_TEMPLATE_NOT_FOUND = "template_not_found"
_ERR_INVALID_PARAMS = "invalid_params"
_ERR_DB_FAILED = "db_execute_failed"
_ERR_QUERY_TIMEOUT = "query_timeout"
_ERR_AGENT_FAILED = "agent_failed"
_ERR_UNEXPECTED = "unexpected_error"


# --- public surface -----------------------------------------------------


async def run_turn(session_id: str, message: str) -> AsyncIterator[ChatEvent]:
    """Async generator yielding one ``ChatEvent`` per pipeline step.

    The first event is always ``TurnStarted``; the last is one of
    ``AnswerFinished`` (happy path), ``ClarificationNeeded``,
    ``AnswerFinished`` carrying the not-answerable note, or ``ChatError``.

    ``session_id`` is required by Phase 4 — the streaming route creates
    one if the client didn't supply it (mirrors the non-streaming
    behaviour). The route is responsible for that step so this generator
    stays pure.

    Failure handling
    ----------------
    Every step is wrapped in try/except; an uncaught exception yields
    one ``ChatError`` event and the generator returns. The traceback is
    logged in full; the wire ``message`` is a redacted summary.
    """
    settings = get_settings()
    turn_id = secrets.token_urlsafe(8)
    ts = _utcnow()

    yield TurnStarted(session_id=session_id, turn_id=turn_id, ts=ts)

    # --- session-store + user-message persistence (non-fatal IO) -------
    store = get_store()
    _safe_append_user(store, session_id, message)

    # --- agent call ----------------------------------------------------
    try:
        agent = get_agent()
        deps = await make_deps()
        result = await agent.run(message, deps=deps)
        plan = result.output
        usage_obj = result.usage  # RunUsage dataclass
    except Exception as exc:  # noqa: BLE001
        log.exception("agent.run failed; sid=%s turn_id=%s", session_id, turn_id)
        yield ChatError(code=_ERR_AGENT_FAILED, message=f"agent failed: {type(exc).__name__}")
        _write_model_log(settings, session_id, turn_id, template_id=None, usage=None, error=exc)
        return

    # --- branch 1: clarification ---------------------------------------
    if plan.clarification is not None:
        clar = plan.clarification
        yield ClarificationNeeded(question=clar)
        _safe_append_assistant(store, session_id, clar)
        _write_model_log(
            settings,
            session_id,
            turn_id,
            template_id=None,
            usage=usage_obj,
            error=None,
        )
        return

    # --- branch 2: not-answerable-note ---------------------------------
    if plan.not_answerable_note is not None:
        note = plan.not_answerable_note
        composed = compose_not_answerable(note)
        async for ev in _stream_composed_answer(
            composed=composed,
            sql=None,
            result=None,
            template_title=plan.template_id or "(no template)",
        ):
            yield ev
        _safe_append_assistant(store, session_id, composed.answer)
        _write_model_log(
            settings,
            session_id,
            turn_id,
            template_id=plan.template_id or None,
            usage=usage_obj,
            error=None,
        )
        return

    # --- branch 3: full template path ----------------------------------
    template_id = plan.template_id
    yield IntentClassified(template_id=template_id, confidence=1.0)

    # 3a. resolve template
    try:
        template = get_template(template_id)
    except TemplateNotFound:
        log.warning("pipeline: unknown template_id=%r sid=%s", template_id, session_id)
        note = f"Template {template_id!r} is not registered."
        composed = compose_not_answerable(note)
        async for ev in _stream_composed_answer(
            composed=composed,
            sql=None,
            result=None,
            template_title=template_id,
        ):
            yield ev
        _safe_append_assistant(store, session_id, composed.answer)
        _write_model_log(
            settings, session_id, turn_id, template_id=template_id, usage=usage_obj, error=None
        )
        return

    # 3b. validate params
    try:
        validated_params = template.params_model(**plan.params).model_dump()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "pipeline: invalid params sid=%s template=%s err=%s",
            session_id,
            template_id,
            exc,
        )
        note = (
            f"Invalid params for {template_id}: {type(exc).__name__}: {exc}. "
            "Rephrase the question with the parameters the template expects."
        )
        composed = compose_not_answerable(note, attempted_sql=template.sql)
        async for ev in _stream_composed_answer(
            composed=composed,
            sql=template.sql,
            result=None,
            template_title=template.title,
        ):
            yield ev
        _safe_append_assistant(store, session_id, composed.answer)
        _write_model_log(
            settings,
            session_id,
            turn_id,
            template_id=template_id,
            usage=usage_obj,
            error=exc,
        )
        return

    # 3c. defense-in-depth re-validation of the template SQL itself
    sql_report = validate_template_sql(template.sql, template.allowed_tables)
    if not sql_report.valid:
        log.error(
            "template %s failed validate_template_sql at request time: %s",
            template_id,
            sql_report.errors,
        )
        note = "Template SQL failed safety validation at request time."
        composed = compose_not_answerable(note, attempted_sql=template.sql)
        async for ev in _stream_composed_answer(
            composed=composed,
            sql=template.sql,
            result=None,
            template_title=template.title,
        ):
            yield ev
        _safe_append_assistant(store, session_id, composed.answer)
        _write_model_log(
            settings,
            session_id,
            turn_id,
            template_id=template_id,
            usage=usage_obj,
            error=ValueError("; ".join(sql_report.errors)),
        )
        return

    # 3d. emit query_started + execute
    query_id = secrets.token_urlsafe(8)
    yield QueryStarted(query_id=query_id, template_id=template_id, sql=template.sql)

    db = get_db()
    timeout_seconds = template.timeout_seconds
    try:
        query_result: QueryResult = await asyncio.wait_for(
            db.execute(template.sql, validated_params, limit=template.default_limit),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        log.warning(
            "pipeline: query timeout sid=%s template=%s timeout=%ds",
            session_id,
            template_id,
            timeout_seconds,
        )
        yield ChatError(
            code=_ERR_QUERY_TIMEOUT,
            message=(
                f"Query exceeded the template's {timeout_seconds}s timeout. "
                "Try a narrower question or a different season/filter."
            ),
        )
        _write_query_log(
            settings,
            session_id,
            turn_id,
            template_id=template_id,
            sql=template.sql,
            params=validated_params,
            result=None,
            error=TimeoutError(f"timeout after {timeout_seconds}s"),
        )
        _write_model_log(
            settings,
            session_id,
            turn_id,
            template_id=template_id,
            usage=usage_obj,
            error=TimeoutError("query timeout"),
        )
        return
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "pipeline: db.execute failed sid=%s template=%s err=%s",
            session_id,
            template_id,
            exc,
        )
        yield ChatError(code=_ERR_DB_FAILED, message=f"db execute failed: {type(exc).__name__}")
        _write_query_log(
            settings,
            session_id,
            turn_id,
            template_id=template_id,
            sql=template.sql,
            params=validated_params,
            result=None,
            error=exc,
        )
        _write_model_log(
            settings,
            session_id,
            turn_id,
            template_id=template_id,
            usage=usage_obj,
            error=exc,
        )
        return

    # 3e. emit query_finished
    yield QueryFinished(
        query_id=query_id,
        duration_ms=query_result.duration_ms,
        row_count=query_result.row_count,
        columns=list(query_result.columns),
        truncated=query_result.truncated,
    )

    # 3f. emit table_ready (preview-capped)
    preview_rows = query_result.rows[:_TABLE_PREVIEW_ROWS]
    truncated = query_result.truncated or len(query_result.rows) > _TABLE_PREVIEW_ROWS
    yield TableReady(
        columns=[ColumnSpec(name=c, dtype=None) for c in query_result.columns],
        rows=preview_rows,
        row_count=query_result.row_count,
        truncated=truncated,
    )

    # 3g. reasoning + composer + answer stream + citations
    composed = compose(template, query_result, template_id)
    yield Reasoning(
        summary=composed.reasoning_summary or f"executed {template_id}",
        execution_plan=template.title,
    )
    for cite in composed.citations:
        yield Citation(
            table_name=cite.table_name,
            metric_key=cite.metric_key,
            gap_key=cite.gap_key,
        )
    async for ev in _stream_composed_answer(
        composed=composed,
        sql=template.sql,
        result=query_result,
        template_title=template.title,
    ):
        yield ev

    # 3h. persist + logs
    _safe_append_assistant(store, session_id, composed.answer)
    _write_query_log(
        settings,
        session_id,
        turn_id,
        template_id=template_id,
        sql=template.sql,
        params=validated_params,
        result=query_result,
        error=None,
    )
    _write_model_log(
        settings,
        session_id,
        turn_id,
        template_id=template_id,
        usage=usage_obj,
        error=None,
    )


# --- answer streaming ---------------------------------------------------


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
    # Split on sentence boundaries; keep the punctuation with the chunk.
    parts = re.split(r"(?<=[.!?])\s+", answer)
    chunks: list[str] = []
    for part in parts:
        if not part:
            continue
        if len(part) <= _ANSWER_CHUNK_WINDOW:
            chunks.append(part)
            continue
        # Fall back to fixed-width windows.
        for i in range(0, len(part), _ANSWER_CHUNK_WINDOW):
            chunks.append(part[i : i + _ANSWER_CHUNK_WINDOW])
    return chunks or [""]


async def _stream_composed_answer(
    composed,  # type: ignore[no-untyped-def]  — composer.ComposedAnswer
    sql: str | None,
    result: QueryResult | None,
    template_title: str,
) -> AsyncIterator[ChatEvent]:
    """Yield ``AnswerDelta``s then a final ``AnswerFinished``.

    ``Reasoning`` and ``Citation`` events are emitted by the caller;
    this helper only handles the answer prose. For very-short answers
    we still emit at least one ``AnswerDelta`` before the
    ``AnswerFinished`` so the reducer's "is streaming" flag works
    uniformly.
    """
    del sql, result, template_title  # kept for API symmetry with future enhancements
    answer = composed.answer
    for chunk in _stream_answer_chunks(answer):
        yield AnswerDelta(delta=chunk)
    yield AnswerFinished(answer=answer)


# --- helpers: session store ---------------------------------------------


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


# --- helpers: log writers -----------------------------------------------


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
    """Persist the rendered SQL + a result preview under logs/queries/.

    Two sibling files (PLAN §6 layout):
        <turn_id>.<template_id>.sql         — the rendered SQL text
        <turn_id>.<template_id>.result.json — columns, row_count,
                                             first ~50 rows, duration
                                             ms, truncated flag, error
                                             (if any)

    Logging IO is non-fatal: a failure is logged once and the turn
    continues.
    """
    base_dir = Path(settings.chat_log_dir) / "queries" / _today_stamp() / session_id
    tid = template_id or "unknown"
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
    bodies are NEVER included here (PLAN §7.10).

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
