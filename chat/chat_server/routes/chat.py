"""Non-streaming ``POST /api/chat`` route (PLAN §7.9).

Phase 3 minimal: one HTTP endpoint that runs a full turn synchronously
and returns the final ``ChatResponse``. Streaming lands in Phase 4.

The handler is a thin orchestrator. The substantive work lives in:

* ``chat_server.agent`` — intent classification + param extraction
  (Pydantic AI agent; owned by the parallel fixer).
* ``chat_server.templates`` — registry lookup + parameter validation.
* ``chat_server.db`` — read-only DuckDB execution.
* ``chat_server.composer`` — rows → grounded answer + citations.

Failure handling
----------------
The route is wrapped to keep the response shape stable even when
internal steps blow up. Any exception inside the agent / DB / composer
is caught, logged in full, and translated into either a
``not_answerable=True`` response (preferred — keeps the UI flow
intact) or, as a last resort, an HTTP 500. The persisted JSONL history
never contains a stack trace.

The session store is append-only; both the user message and the
assistant response are recorded before the route returns so the
history endpoint can replay the turn without gaps.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# NOTE: these imports come from the parallel fixer's ``chat_server.agent``
# module. If that module is missing (parallel PR not yet landed) the
# application will fail to start; that is the intended fail-fast behaviour
# because the chat route is unusable without an agent to classify intent.
from chat_server.agent import get_agent, make_deps  # noqa: E402, F401 — used inside the handler.
from chat_server.composer import compose, compose_not_answerable
from chat_server.db import get_db
from chat_server.sessions import SessionNotFound, get_store
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
        result = await agent.run(req.message, deps=deps)
        plan = result.output
    except Exception as exc:
        log.exception("agent.run failed; sid=%s", sid)
        _append_assistant_or_500(sid, store, f"Agent failed: {type(exc).__name__}")
        return _not_answerable_response(
            sid=sid,
            note=f"Agent failed: {type(exc).__name__}",
            template_id=None,
        )

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

    # --- 4. Resolve template --------------------------------------------
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
    try:
        query_result = await get_db().execute(
            template.sql,
            validated_params,
            limit=template.default_limit,
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
