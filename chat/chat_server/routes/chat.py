"""Chat routes — non-streaming ``POST /api/chat`` (Phase 3) and
streaming ``POST /api/chat/stream`` (Phase 4, PLAN §7.9).

The non-streaming handler is a thin orchestrator whose substantive work
lives in:

* ``chat_server.agent`` — intent classification + param extraction
  (Pydantic AI agent).
* ``chat_server.templates`` — registry lookup + parameter validation.
* ``chat_server.db`` — read-only DuckDB execution.
* ``chat_server.composer`` — rows → grounded answer + citations.

The streaming handler (``/api/chat/stream``) is a thin SSE shim that
wraps ``chat_server.pipeline.run_turn`` — it owns the route shape and
session-id resolution but delegates everything else to the pipeline.

Failure handling (non-streaming)
-------------------------------
The non-streaming route is wrapped to keep the response shape stable
even when internal steps blow up. Any exception inside the agent / DB /
composer is caught, logged in full, and translated into either a
``not_answerable=True`` response (preferred — keeps the UI flow intact)
or, as a last resort, an HTTP 500. The persisted JSONL history never
contains a stack trace.

Failure handling (streaming)
---------------------------
The pipeline yields a ``ChatError`` event for any uncaught exception;
the route just passes the events through. The SSE generator returning
terminates the response cleanly without leaking a 500 to the client.

Session store
-------------
The session store is append-only; both the user message and the
assistant response are recorded before the route returns so the
history endpoint can replay the turn without gaps. The streaming route
persists the same way, but the assistant message is appended *after*
the last event is yielded (so the visible history matches what the UI
actually rendered).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from fastapi.sse import EventSourceResponse
from pydantic import BaseModel, Field

# NOTE: these imports come from the parallel fixer's ``chat_server.agent``
# module. If that module is missing (parallel PR not yet landed) the
# application will fail to start; that is the intended fail-fast behaviour
# because the chat route is unusable without an agent to classify intent.
from chat_server.agent import (  # noqa: E402, F401 — used inside the handler.
    AnswerMode,
    Clarification,
    ResultContract,
    get_agent,
    make_deps,
)
from chat_server.clarify import ClarificationState, build_clarification_context_prefix
from chat_server.composer import compose, compose_governed, compose_not_answerable
from chat_server.db import DryRunError, get_db
from chat_server.events import ChatError, to_sse_dict
from chat_server.pipeline import _load_model_history, _safe_append_model_history, run_turn
from chat_server.repair import repair_sql
from chat_server.sessions import SessionNotFound, get_store
from chat_server.sqlgate import validate_governed_sql
from chat_server.templates import TemplateNotFound, get_template
from chat_server.validation import validate_template_sql

router = APIRouter(tags=["chat"])
log = logging.getLogger(__name__)


# --- request / response models ------------------------------------------


class ChatRequest(BaseModel):
    """Body for ``POST /api/chat``.

    Attributes
    ----------
    session_id
        Optional. When omitted (or stale) a new session is created with
        the first 40 characters of ``message`` as its title.
    message
        The user's question. Bounded 1..4000 characters by FastAPI.
    """

    session_id: str | None = None
    message: str = Field(min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    """Response for ``POST /api/chat``.

    Mirrors the canonical turn response (PLAN §7.7): answer + citations +
    provenance fields. ``sql`` is non-null only on the happy path
    (``template_id`` resolved, query executed). ``not_answerable=True``
    short-circuits the SQL + row_count fields.
    """

    session_id: str
    answer: str
    citations: list[dict] = Field(default_factory=list)
    not_answerable: bool = False
    not_answerable_note: str | None = None
    template_id: str | None = None
    sql: str | None = None
    row_count: int | None = None
    reasoning_summary: str | None = None
    duration_ms: float | None = None


# --- helpers -------------------------------------------------------------


def _resolve_session_id(store, requested: str | None, fallback_title: str) -> str:
    """Return a usable session id, creating one when needed.

    Mirrors the ``POST /api/sessions`` behaviour: an unknown id is
    treated as "create a new session with this title". The fallback
    title is the first 40 characters of the incoming message.
    """
    if requested:
        try:
            return store.get(requested).id
        except SessionNotFound:
            pass
    return store.create(title=fallback_title).id


def _title_from(message: str) -> str:
    """First 40 chars of the message, with trailing whitespace stripped."""
    return message[:40].strip() or "New chat"


def _citations_to_dicts(composed_citations) -> list[dict]:
    """Flat serialisable view of the composer's Citation list."""
    return [
        {
            "table_name": c.table_name,
            "metric_key": c.metric_key,
            "gap_key": c.gap_key,
        }
        for c in composed_citations
    ]


# --- route ---------------------------------------------------------------


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Run one chat turn end-to-end and return the final answer.

    The flow mirrors PLAN §7.7 (non-streaming subset) with explicit
    not-answerable fallbacks so a bad plan / bad params / failed DB
    call never surfaces as a 500 stack trace to the UI.
    """
    store = get_store()
    sid = _resolve_session_id(store, req.session_id, _title_from(req.message))

    # Persist the user message BEFORE running anything so a slow turn
    # never loses the user input from the visible history.
    try:
        store.append_message(sid, "user", req.message)
    except SessionNotFound:
        # Defensive: store.create succeeded so this shouldn't happen, but
        # if the disk vanished mid-write we surface a clean 500 instead of
        # a half-populated session.
        log.exception("session store failed to append user message; sid=%s", sid)
        raise HTTPException(status_code=500, detail="session store error") from None

    # --- 1. Run the agent ------------------------------------------------
    try:
        agent = get_agent()
        # `make_deps` is async (it builds the schema context against the
        # warehouse) — must be awaited, not passed as a coroutine.
        deps = await make_deps()
        # --- Stage 3.5: load + trim prior ModelMessage history ---------
        # Best-effort — a missing or corrupt history file is expected on
        # the first turn and must not crash the response.
        history = _load_model_history(store, sid)
        # --- Stage 3.6: enrich prompt if a clarification is pending ----
        # Complements the model-history snapshot. The pending state is
        # independent of the snapshot — even if the snapshot is absent /
        # corrupt / trimmed, a pending clarification still reaches the
        # agent via this enriched prompt. The raw `req.message` still
        # flows to the visible-JSONL store unchanged (the user's
        # literal reply is what the UI shows).
        pending = store.get_pending_clarification(sid)
        user_message_text = req.message
        if pending is not None:
            user_message_text = build_clarification_context_prefix(pending, req.message)
        # Pass `message_history` ONLY when there is real history to
        # forward — an empty list is semantically equivalent to omitting
        # the kwarg (Pydantic AI treats both as "no prior context"), and
        # conditional kwargs let test fakes with narrower signatures
        # continue to work.
        run_kwargs: dict = {"deps": deps}
        if history:
            run_kwargs["message_history"] = history
        result = await agent.run(user_message_text, **run_kwargs)
        plan = result.output
        # --- Stage 3.5: persist the post-call history snapshot ----------
        # Best-effort — a failed write logs and returns; the turn
        # continues regardless.
        _safe_append_model_history(store, sid, result)
    except Exception as exc:
        log.exception("agent.run failed; sid=%s", sid)
        _append_assistant_or_500(sid, store, f"Agent failed: {type(exc).__name__}")
        return _not_answerable_response(
            sid=sid,
            note=f"Agent failed: {type(exc).__name__}",
            template_id=None,
        )

    # --- Stage 3.6: persist / clear the pending-clarification state ------
    # Single block, runs for EVERY plan outcome (clarify / not-answerable
    # / execute_sql / template). The legacy template branch is therefore
    # auto-cleared too without any invasive branch-local wiring — the
    # spec's "byte-for-byte unchanged legacy branch" is preserved by
    # keeping all state-machine side-effects in this one block above
    # the branching cascade. Mirrors the equivalent block in
    # ``pipeline.run_turn``.
    if plan.answer_mode == AnswerMode.CLARIFY and plan.clarification is not None:
        clar = plan.clarification
        if isinstance(clar, Clarification):
            # Normalize the empty options list to ``None`` so the
            # enrichment prefix omits the options clause for free-form
            # clarifications.
            options_list: list[str] | None = list(clar.options) if clar.options else None
            state = ClarificationState(
                original_question=req.message,
                clarification_question=clar.question,
                options=options_list,
            )
            try:
                store.set_pending_clarification(sid, state)
            except Exception:  # noqa: BLE001
                log.exception("set_pending_clarification failed; sid=%s", sid)
    else:
        try:
            store.clear_pending_clarification(sid)
        except Exception:  # noqa: BLE001
            log.exception("clear_pending_clarification failed; sid=%s", sid)

    # --- 2. Clarification (no DB run) -----------------------------------
    if plan.clarification is not None:
        clarification_text: str = plan.clarification
        store.append_message(sid, "assistant", clarification_text)
        log.info("chat turn: clarification sid=%s template_id=%r", sid, plan.template_id)
        return ChatResponse(
            session_id=sid,
            answer=clarification_text,
            citations=[],
            not_answerable=False,
            template_id=plan.template_id,
            reasoning_summary="Clarification needed before query.",
        )

    # --- 3. Explicit not-answerable (no DB run) --------------------------
    if plan.not_answerable_note is not None:
        note: str = plan.not_answerable_note
        composed = compose_not_answerable(note)
        store.append_message(sid, "assistant", composed.answer)
        log.info("chat turn: not-answerable sid=%s template_id=%r", sid, plan.template_id)
        return ChatResponse(
            session_id=sid,
            answer=composed.answer,
            citations=[],
            not_answerable=True,
            not_answerable_note=composed.not_answerable_note,
            template_id=plan.template_id,
            reasoning_summary=composed.reasoning_summary,
        )

    # --- 4. Governed SQL (EXECUTE_SQL mode) ----------------------------
    if plan.answer_mode == AnswerMode.EXECUTE_SQL and plan.sql:
        catalog = deps.catalog
        if catalog is None:
            note = "The semantic catalog is not loaded, so I can't run governed queries yet."
            composed = compose_not_answerable(note)
            store.append_message(sid, "assistant", composed.answer)
            log.info("chat turn: governed without catalog sid=%s", sid)
            return ChatResponse(
                session_id=sid,
                answer=composed.answer,
                citations=[],
                not_answerable=True,
                not_answerable_note=composed.not_answerable_note,
                template_id=None,
                reasoning_summary=composed.reasoning_summary,
            )

        report = validate_governed_sql(plan.sql, catalog)
        if not report.valid:
            errors_note = "; ".join(report.errors) or "SQL validation failed."
            composed = compose_not_answerable(errors_note, attempted_sql=plan.sql)
            store.append_message(sid, "assistant", composed.answer)
            log.info(
                "chat turn: governed validation failed sid=%s errs=%s",
                sid,
                report.errors,
            )
            return ChatResponse(
                session_id=sid,
                answer=composed.answer,
                citations=[],
                not_answerable=True,
                not_answerable_note=composed.not_answerable_note,
                template_id=None,
                sql=plan.sql,
                reasoning_summary=composed.reasoning_summary,
            )

        # --- Stage 3.4: dry-run + single-shot repair (mirrors pipeline.py)
        # The catalog gate above only checks structural legality + table
        # allowlist; a second class of bugs (stale columns, fabricated
        # identifiers) only surfaces when DuckDB binds the query. We catch
        # that with `EXPLAIN` (no row reads) and give the agent one bounded
        # re-prompt to fix it. See chat_server.repair for the design lineage.
        db = get_db()
        try:
            await db.dry_run(plan.sql)
        except DryRunError as exc:
            log.info(
                "chat turn: dry-run failed; attempting repair sid=%s err=%s",
                sid,
                exc.original,
            )
            repaired_plan = await repair_sql(
                agent,
                deps,
                question=req.message,
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
                store.append_message(sid, "assistant", composed.answer)
                return ChatResponse(
                    session_id=sid,
                    answer=composed.answer,
                    citations=[],
                    not_answerable=True,
                    not_answerable_note=composed.not_answerable_note,
                    template_id=None,
                    sql=plan.sql,
                    reasoning_summary=composed.reasoning_summary,
                )
            # Re-validate the repaired SQL against the catalog.
            repaired_report = validate_governed_sql(repaired_plan.sql, catalog)
            if not repaired_report.valid:
                repaired_errors = "; ".join(repaired_report.errors) or (
                    "repaired SQL failed validation"
                )
                composed = compose_not_answerable(
                    f"I couldn't fix the query: {repaired_errors}",
                    attempted_sql=plan.sql,
                )
                store.append_message(sid, "assistant", composed.answer)
                return ChatResponse(
                    session_id=sid,
                    answer=composed.answer,
                    citations=[],
                    not_answerable=True,
                    not_answerable_note=composed.not_answerable_note,
                    template_id=None,
                    sql=plan.sql,
                    reasoning_summary=composed.reasoning_summary,
                )
            # Adopt the repaired SQL + refreshed report.
            plan = repaired_plan
            report = repaired_report
            assert plan.sql is not None, "repair_sql returned a plan with no SQL"

        model_sentinel = f"semantic:{next(iter(report.tables_referenced), 'unknown')}"
        row_limit = plan.result_contract.row_limit if plan.result_contract else None
        try:
            query_result = await db.execute(plan.sql, limit=row_limit)
        except Exception as exc:
            log.exception("chat turn: governed db.execute failed sid=%s err=%s", sid, exc)
            note = f"Query execution failed: {type(exc).__name__}"
            composed = compose_not_answerable(note, attempted_sql=plan.sql)
            store.append_message(sid, "assistant", composed.answer)
            return ChatResponse(
                session_id=sid,
                answer=composed.answer,
                citations=[],
                not_answerable=True,
                not_answerable_note=composed.not_answerable_note,
                template_id=model_sentinel,
                sql=plan.sql,
                reasoning_summary=composed.reasoning_summary,
            )

        composed = compose_governed(
            plan.result_contract or ResultContract(grain="results", answer_style="prose"),
            query_result,
            plan.sql,
            model_name=model_sentinel,
        )
        store.append_message(sid, "assistant", composed.answer)
        log.info(
            "chat turn: governed=%s sid=%s duration_ms=%.1f row_count=%d truncated=%s",
            model_sentinel,
            sid,
            query_result.duration_ms,
            query_result.row_count,
            query_result.truncated,
        )
        return ChatResponse(
            session_id=sid,
            answer=composed.answer,
            citations=_citations_to_dicts(composed.citations),
            not_answerable=False,
            template_id=model_sentinel,
            sql=plan.sql,
            row_count=query_result.row_count,
            reasoning_summary=composed.reasoning_summary,
            duration_ms=query_result.duration_ms,
        )

    # --- 5. Resolve template --------------------------------------------
    try:
        template = get_template(plan.template_id)
    except TemplateNotFound:
        note = f"Template {plan.template_id!r} is not registered."
        composed = compose_not_answerable(note)
        store.append_message(sid, "assistant", composed.answer)
        log.warning("chat turn: unknown template sid=%s template_id=%r", sid, plan.template_id)
        return ChatResponse(
            session_id=sid,
            answer=composed.answer,
            citations=[],
            not_answerable=True,
            not_answerable_note=composed.not_answerable_note,
            template_id=plan.template_id,
            reasoning_summary=composed.reasoning_summary,
        )

    # --- 5. Validate params against the template's Pydantic model --------
    try:
        validated_params = template.params_model(**plan.params).model_dump()
    except Exception as exc:
        note = (
            f"Invalid params for {plan.template_id}: {type(exc).__name__}: {exc}. "
            "Rephrase the question with the parameters the template expects."
        )
        composed = compose_not_answerable(note, attempted_sql=template.sql)
        store.append_message(sid, "assistant", composed.answer)
        log.warning(
            "chat turn: invalid params sid=%s template=%s err=%s", sid, plan.template_id, exc
        )
        return ChatResponse(
            session_id=sid,
            answer=composed.answer,
            citations=[],
            not_answerable=True,
            not_answerable_note=composed.not_answerable_note,
            template_id=plan.template_id,
            sql=template.sql,
            reasoning_summary=composed.reasoning_summary,
        )

    # --- 6. Defense-in-depth SQL validation ------------------------------
    # The loader already ran this at import time; re-checking here means
    # a runtime mutation of the template (impossible today, but cheap to
    # guard) would fail loudly instead of executing an out-of-allowlist
    # query.
    sql_report = validate_template_sql(template.sql, template.allowed_tables)
    if not sql_report.valid:
        log.error(
            "template %s failed validate_template_sql at request time: %s",
            plan.template_id,
            sql_report.errors,
        )
        note = "Template SQL failed safety validation at request time."
        composed = compose_not_answerable(note, attempted_sql=template.sql)
        store.append_message(sid, "assistant", composed.answer)
        return ChatResponse(
            session_id=sid,
            answer=composed.answer,
            citations=[],
            not_answerable=True,
            not_answerable_note=composed.not_answerable_note,
            template_id=plan.template_id,
            sql=template.sql,
            reasoning_summary=composed.reasoning_summary,
        )

    # --- 7. Execute against the warehouse --------------------------------
    # Enforce the template's per-query timeout (PLAN §7.7 step 10 / §16).
    # Without this a heavy template (pbp/clutch/lineup, TIMEOUT_SECONDS=300)
    # could hold the process-wide DB lock and starve every other turn (C1).
    try:
        query_result = await asyncio.wait_for(
            get_db().execute(
                template.sql,
                validated_params,
                limit=template.default_limit,
            ),
            timeout=template.timeout_seconds,
        )
    except TimeoutError:
        log.warning(
            "DB execute timed out; sid=%s template=%s timeout=%ss",
            sid,
            plan.template_id,
            template.timeout_seconds,
        )
        note = f"Query exceeded the {template.timeout_seconds}s timeout for this template."
        composed = compose_not_answerable(note, attempted_sql=template.sql)
        store.append_message(sid, "assistant", composed.answer)
        return ChatResponse(
            session_id=sid,
            answer=composed.answer,
            citations=[],
            not_answerable=True,
            not_answerable_note=composed.not_answerable_note,
            template_id=plan.template_id,
            sql=template.sql,
            reasoning_summary=composed.reasoning_summary,
        )
    except Exception as exc:
        log.exception("DB execute failed; sid=%s template=%s err=%s", sid, plan.template_id, exc)
        note = f"Query execution failed: {type(exc).__name__}"
        composed = compose_not_answerable(note, attempted_sql=template.sql)
        store.append_message(sid, "assistant", composed.answer)
        return ChatResponse(
            session_id=sid,
            answer=composed.answer,
            citations=[],
            not_answerable=True,
            not_answerable_note=composed.not_answerable_note,
            template_id=plan.template_id,
            sql=template.sql,
            reasoning_summary=composed.reasoning_summary,
        )

    # --- 8. Compose + persist + respond ---------------------------------
    composed = compose(template, query_result, plan.template_id)
    store.append_message(sid, "assistant", composed.answer)
    log.info(
        "chat turn: template=%s sid=%s duration_ms=%.1f row_count=%d truncated=%s",
        plan.template_id,
        sid,
        query_result.duration_ms,
        query_result.row_count,
        query_result.truncated,
    )
    return ChatResponse(
        session_id=sid,
        answer=composed.answer,
        citations=_citations_to_dicts(composed.citations),
        not_answerable=False,
        template_id=plan.template_id,
        sql=template.sql,
        row_count=query_result.row_count,
        reasoning_summary=composed.reasoning_summary,
        duration_ms=query_result.duration_ms,
    )


# --- helpers (private) ---------------------------------------------------


def _append_assistant_or_500(sid: str, store, text: str) -> None:
    """Persist an assistant message; swallow session-store failures.

    Used in the agent-failure branch where the request is about to
    return a not-answerable response — losing the assistant message to a
    second store error would compound the failure. We log and move on.
    """
    try:
        store.append_message(sid, "assistant", text)
    except Exception:
        log.exception("failed to append assistant message; sid=%s", sid)


def _not_answerable_response(*, sid: str, note: str, template_id: str | None) -> ChatResponse:
    """Build a uniform not-answerable response with empty SQL fields."""
    return ChatResponse(
        session_id=sid,
        answer=note,
        citations=[],
        not_answerable=True,
        not_answerable_note=note,
        template_id=template_id,
    )


__all__ = ["ChatRequest", "ChatResponse", "router"]


# --- streaming endpoint (Phase 4, PLAN §7.7 / §7.9) ---------------------


# Note: ``EventSourceResponse`` accepts any async iterator; it sets the
# ``text/event-stream`` media type and disables buffering at the ASGI
# layer. The route builds the body by mapping each ``ChatEvent`` through
# ``to_sse_dict`` and rendering one ``event:`` / ``data:`` frame per
# payload. SSE keeps the connection open until the generator returns
# (i.e. the pipeline exhausts its async generator).
#
# Client disconnect behaviour: FastAPI/Starlette will close the
# underlying send channel when the client goes away. The inner
# ``async for ev in run_turn(...)`` raises ``GeneratorExit`` (or
# ``asyncio.CancelledError``) and the pipeline's try/except wrappers
# convert any in-flight exception into a ``ChatError`` event first —
# but in the disconnect case the generator is just terminated. The
# pipeline never assumes the consumer is still listening.


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> EventSourceResponse:
    """SSE endpoint: stream one turn's ``ChatEvent``s back to the UI.

    The endpoint is intentionally thin — all substantive work happens in
    ``chat_server.pipeline.run_turn``. The route is responsible for:

    * Resolving the session id (create when the client didn't supply
      one; mirrors the non-streaming route's behaviour).
    * Mapping each ``ChatEvent`` through ``to_sse_dict`` and emitting
      one SSE frame.
    * Letting ``EventSourceResponse`` handle the wire-format headers.

    Errors after the first event become ``ChatError`` events inside the
    stream — the response stays 200 (an SSE body that starts with a
    ``200 OK`` is well-formed even if a later frame carries an error
    code). Errors *before* ``turn_started`` would surface as a 500
    because the generator never yields; in practice ``run_turn`` yields
    ``TurnStarted`` synchronously, so this is a non-issue.
    """
    store = get_store()
    sid = _resolve_session_id(store, req.session_id, _title_from(req.message))

    async def event_gen():
        # ``run_turn`` is an async generator. Each yielded ``ChatEvent``
        # is mapped to one SSE frame. ``EventSourceResponse`` flushes
        # immediately (no buffering); the UI sees events as they are
        # produced.
        try:
            async for ev in run_turn(sid, req.message):
                d = to_sse_dict(ev)
                # SSE wire format: "event: <name>\ndata: <json>\n\n".
                yield f"event: {d['event']}\ndata: {d['data']}\n\n"
        except Exception as exc:  # noqa: BLE001
            # Last-resort safety net — the pipeline already converts
            # step-level errors into ``ChatError`` events; this only
            # fires for catastrophic failures (e.g. the agent runner
            # itself blows up before yielding anything past
            # ``TurnStarted``).
            log.exception("chat_stream: pipeline raised; sid=%s", sid)
            fallback = to_sse_dict(
                ChatError(code="stream_failed", message=f"{type(exc).__name__}: {exc}")
            )
            yield f"event: {fallback['event']}\ndata: {fallback['data']}\n\n"

    return EventSourceResponse(event_gen())
