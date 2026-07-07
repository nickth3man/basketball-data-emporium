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
import contextlib
import datetime as _dt
import json
import logging
import re
import secrets
from collections.abc import AsyncIterator
from dataclasses import asdict as _dc_asdict
from pathlib import Path
from typing import Any

from pydantic_ai.messages import ModelMessagesTypeAdapter

from . import otel
from .agent import (  # noqa: F401 — re-exported by tests via pipeline
    AnswerMode,
    Clarification,
    ResultContract,
    get_agent,
    keep_last_messages_with_tools,
    make_deps,
)
from .clarify import ClarificationState, build_clarification_context_prefix
from .composer import compose, compose_governed, compose_not_answerable
from .config import get_settings
from .db import DryRunError, QueryResult, get_db
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
from .repair import repair_sql
from .sessions import SessionNotFound, get_store
from .sqlgate import validate_governed_sql
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
    # Optional OTel span around the LLM call (PLAN §4.1#9 / §15 Phase 7).
    # No-op when OTel is disabled (the default); event sequence unchanged.
    with otel.span("agent.run", attributes={"session_id": session_id}) as agent_span:
        try:
            agent = get_agent()
            deps = await make_deps()
            # --- Stage 3.5: load + trim prior ModelMessage history -------
            # Best-effort: a missing file is normal (fresh session); a
            # corrupt file or schema-drift validation failure must not
            # break the turn — we fall back to an empty history.
            history = _load_model_history(store, session_id)
            # --- Stage 3.6: enrich prompt if a clarification is pending -
            # Complements the model-history snapshot. The pending state
            # is independent of the snapshot — even if the snapshot is
            # absent / corrupt / trimmed, a pending clarification still
            # reaches the agent via this enriched prompt. The raw user
            # message still flows to the visible-JSONL store unchanged
            # (so the user's literal reply is what the UI shows).
            pending = store.get_pending_clarification(session_id)
            user_message_text = message
            if pending is not None:
                user_message_text = build_clarification_context_prefix(pending, message)
            # Pass `message_history` ONLY when there is real history to
            # forward — an empty list is semantically equivalent to
            # omitting the kwarg (Pydantic AI treats both as "no prior
            # context"), and conditional kwargs let test fakes with
            # narrower signatures continue to work.
            run_kwargs: dict[str, Any] = {"deps": deps}
            if history:
                run_kwargs["message_history"] = history
            result = await agent.run(user_message_text, **run_kwargs)
            plan = result.output
            usage_obj = result.usage  # RunUsage dataclass
            if agent_span is not None:
                # Never crash on a bad attribute value.
                with contextlib.suppress(Exception):
                    agent_span.set_attribute("template_id", plan.template_id or "")
        except Exception as exc:  # noqa: BLE001
            log.exception("agent.run failed; sid=%s turn_id=%s", session_id, turn_id)
            yield ChatError(code=_ERR_AGENT_FAILED, message=f"agent failed: {type(exc).__name__}")
            _write_model_log(settings, session_id, turn_id, template_id=None, usage=None, error=exc)
            return

    # --- Stage 3.5: persist the post-call history snapshot --------------
    # Best-effort: a write failure must not break the turn.
    _safe_append_model_history(store, session_id, result)

    # --- Stage 3.6: persist / clear the pending-clarification state ------
    # Single block, runs for EVERY plan outcome (clarify / not-answerable
    # / execute_sql / template). The legacy template branch is therefore
    # auto-cleared too without any invasive branch-local wiring — the
    # spec's "byte-for-byte unchanged legacy branch" is preserved by
    # keeping all state-machine side-effects in this one block above
    # the branching cascade.
    if plan.answer_mode == AnswerMode.CLARIFY and plan.clarification is not None:
        clar = plan.clarification
        if isinstance(clar, Clarification):
            # Normalize the empty options list to ``None`` so the
            # enrichment prefix omits the options clause for free-form
            # clarifications.
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

    # --- branch 3: governed SQL (EXECUTE_SQL) --------------------------
    if plan.answer_mode == AnswerMode.EXECUTE_SQL and plan.sql:
        catalog = deps.catalog
        if catalog is None:
            note = "The semantic catalog is not loaded, so I can't run governed queries yet."
            composed = compose_not_answerable(note)
            async for ev in _stream_composed_answer(
                composed=composed,
                sql=None,
                result=None,
                template_title="(governed)",
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

        report = validate_governed_sql(plan.sql, catalog)
        if not report.valid:
            errors_note = "; ".join(report.errors) or "SQL validation failed."
            composed = compose_not_answerable(errors_note, attempted_sql=plan.sql)
            async for ev in _stream_composed_answer(
                composed=composed,
                sql=plan.sql,
                result=None,
                template_title="(governed)",
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

        # --- Stage 3.4: dry-run + single-shot repair ---------------------
        # The catalog gate (above) only checks that the SQL is structurally
        # legal and references allowlisted tables. A second class of bugs --
        # stale column references after a warehouse rebuild, join keys that
        # don't actually exist in the data, fabricated identifiers that
        # slipped through the sqlglot optimizer's best-effort column
        # extraction -- only surfaces when DuckDB tries to bind the query.
        # We catch that with `EXPLAIN` (a planner walk that doesn't read
        # rows) and give the agent one bounded re-prompt to fix it. This
        # is the MAC-SQL Refiner pattern under MAX_ROUND=1.
        db = get_db()
        try:
            await db.dry_run(plan.sql)
        except DryRunError as exc:
            log.info(
                "pipeline: dry-run failed; attempting repair sid=%s err=%s",
                session_id,
                exc.original,
            )
            repaired_plan = await repair_sql(
                agent,
                deps,
                question=message,
                broken_sql=plan.sql,
                error=str(exc.original),
            )
            if repaired_plan is None or not repaired_plan.sql:
                # Repair declined (clarify / not_answerable / empty SQL) ->
                # not-answerable, surface the original dry-run error.
                composed = compose_not_answerable(
                    f"I couldn't fix the query: {exc.original}",
                    attempted_sql=plan.sql,
                )
                async for ev in _stream_composed_answer(
                    composed=composed,
                    sql=plan.sql,
                    result=None,
                    template_title="(governed)",
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
            # Re-validate the repaired SQL against the catalog (it must
            # still be in the allowlist + free of fan-traps etc.).
            repaired_report = validate_governed_sql(repaired_plan.sql, catalog)
            if not repaired_report.valid:
                repaired_errors = "; ".join(repaired_report.errors) or (
                    "repaired SQL failed validation"
                )
                composed = compose_not_answerable(
                    f"I couldn't fix the query: {repaired_errors}",
                    attempted_sql=plan.sql,
                )
                async for ev in _stream_composed_answer(
                    composed=composed,
                    sql=plan.sql,
                    result=None,
                    template_title="(governed)",
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
            # Adopt the repaired SQL: update plan + report so the rest of
            # this branch (sentinel, execute, compose) operates on the
            # fixed query.
            plan = repaired_plan
            report = repaired_report
            # Narrow `plan.sql` for the type checker: we already proved
            # `repaired_plan.sql` is truthy above.
            assert plan.sql is not None, "repair_sql returned a plan with no SQL"

        model_sentinel = f"semantic:{next(iter(report.tables_referenced), 'unknown')}"

        yield IntentClassified(template_id=model_sentinel, confidence=1.0)
        query_id = secrets.token_urlsafe(8)
        yield QueryStarted(query_id=query_id, template_id=model_sentinel, sql=plan.sql)

        row_limit = plan.result_contract.row_limit if plan.result_contract else None
        try:
            query_result: QueryResult = await db.execute(plan.sql, limit=row_limit)
        except Exception as exc:  # noqa: BLE001
            log.exception("pipeline: governed db.execute failed sid=%s err=%s", session_id, exc)
            yield ChatError(
                code=_ERR_DB_FAILED,
                message=f"db execute failed: {type(exc).__name__}",
            )
            _write_model_log(
                settings,
                session_id,
                turn_id,
                template_id=model_sentinel,
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
            model_name=model_sentinel,
            question_interpretation=plan.question_interpretation,
        )
        yield Reasoning(
            summary=composed.reasoning_summary or f"executed {model_sentinel}",
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
            sql=plan.sql,
            result=query_result,
            template_title=model_sentinel,
        ):
            yield ev

        _safe_append_assistant(store, session_id, composed.answer)
        _write_model_log(
            settings,
            session_id,
            turn_id,
            template_id=model_sentinel,
            usage=usage_obj,
            error=None,
        )
        return

    # --- branch 4: full template path ----------------------------------
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
    # Optional OTel span around the DB execution (PLAN §4.1#9 / §15 Phase 7).
    # The span stays open through the try/except so a timeout / exception
    # still records its end time correctly. No-op when OTel is off.
    with otel.span(
        "db.execute",
        attributes={"template_id": template_id, "timeout_seconds": timeout_seconds},
    ) as db_span:
        try:
            query_result: QueryResult = await asyncio.wait_for(
                db.execute(template.sql, validated_params, limit=template.default_limit),
                timeout=timeout_seconds,
            )
            if db_span is not None:
                # Never crash on a bad attribute value.
                with contextlib.suppress(Exception):
                    db_span.set_attribute("row_count", query_result.row_count)
                    db_span.set_attribute("duration_ms", query_result.duration_ms)
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
