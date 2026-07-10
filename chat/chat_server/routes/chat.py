"""Chat routes — non-streaming ``POST /api/chat`` (Phase 3) and
streaming ``POST /api/chat/stream`` (Phase 4).

Both routes are **thin consumers** of ``chat_server.pipeline.run_turn``,
the single canonical cascade that owns the agent → gate → dry-run →
repair → execute → compose sequence. The routes are responsible only
for:

* Resolving the session id (``POST /api/sessions`` semantics: an unknown
  id is treated as "create a new session with this title"; the title
  defaults to the first 40 chars of the incoming message).
* Mapping the ``ChatEvent`` stream from ``run_turn`` onto the route's
  wire format — SSE frames for ``/stream``, a single ``ChatResponse``
  for ``/chat``.

Failure handling (non-streaming)
-------------------------------
``run_turn`` yields a ``ChatError`` event for any uncaught exception
inside the cascade; the JSON reducer folds it into a
``not_answerable=True`` response so the wire shape stays uniform. An
exception raised before ``run_turn`` yields ``TurnStarted`` (rare —
session-store failures happen synchronously *after* the first yield in
practice) is translated into an HTTP 500. The persisted JSONL history
never contains a stack trace.

Failure handling (streaming)
---------------------------
``run_turn`` yields a ``ChatError`` event for any uncaught exception;
the route just passes the events through. The SSE generator returning
terminates the response cleanly without leaking a 500 to the client.

Session store
-------------
The session store is append-only; ``run_turn`` writes both the user
message and the assistant response as part of its cascade. The JSON
route does no session-store writes of its own (per Phase 1 §9 step 4 —
the route is a consumer, not a duplicator). The streaming route has
the same behaviour.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.sse import EventSourceResponse
from pydantic import BaseModel, Field

from chat_server.events import (
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
    to_sse_dict,
)
from chat_server.pipeline import TurnResult, run_turn
from chat_server.sessions import SessionNotFound, get_store

router = APIRouter(tags=["chat"])
log = logging.getLogger(__name__)


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

    Mirrors the canonical turn response: answer + citations +
    provenance fields. ``sql`` is non-null only on the happy path
    (a governed query executed). ``not_answerable=True``
    short-circuits the SQL + row_count fields.
    """

    session_id: str
    answer: str
    citations: list[dict] = Field(default_factory=list)
    not_answerable: bool = False
    not_answerable_note: str | None = None
    query_ref: QueryRef | None = None
    sql: str | None = None
    row_count: int | None = None
    reasoning_summary: str | None = None
    duration_ms: float | None = None


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


# ---------------------------------------------------------------------------
# JSON reducer: fold the 11-event ChatEvent stream into one ChatResponse
# ---------------------------------------------------------------------------
#
# The 11-event union (see ``chat_server.events.ChatEvent``) is the single
# source of truth for what happened during a turn. ``run_turn`` already
# drives the agent / gate / dry-run / repair / execute / compose cascade
# and emits the relevant events; the reducer below just accumulates them
# into the JSON wire shape.
#
# Event → ChatResponse mapping
# ----------------------------
# ``IntentClassified.query_ref``     → ``ChatResponse.query_ref``
# ``QueryStarted.sql``               → ``ChatResponse.sql``
# ``QueryStarted.query_ref``         → ``ChatResponse.query_ref``
#                                     (fallback if IntentClassified was
#                                     skipped on an early-degradation path).
# ``QueryFinished.row_count``        → ``ChatResponse.row_count``
# ``QueryFinished.duration_ms``      → ``ChatResponse.duration_ms``
# ``Reasoning.summary``              → ``ChatResponse.reasoning_summary``
# ``Citation``                       → ``ChatResponse.citations``
#                                     (one dict per event; the composer's
#                                     ``Citation`` dataclass and the SSE
#                                     ``Citation`` event share field names).
# ``AnswerFinished.answer``          → ``ChatResponse.answer``
#                                     (authoritative full text).
# ``ChatError.message``              → ``ChatResponse.answer`` +
#                                     ``ChatResponse.not_answerable=True``
#                                     when no ``AnswerFinished`` follows.
# ``ClarificationNeeded.question``   → ``ChatResponse.answer`` for the
#                                     clarification branch (no AnswerFinished
#                                     is emitted on that path).
# ``TurnResult.not_answerable``      → ``ChatResponse.not_answerable``
# ``TurnResult.not_answerable_note`` → ``ChatResponse.not_answerable_note``
#
# Events with no ChatResponse field
# ---------------------------------
# ``TurnStarted`` carries sid / turn_id / ts — metadata the JSON route
# already has from the request.
# ``TableReady`` duplicates ``row_count`` and adds a preview row window
# the JSON response doesn't surface (the React UI uses the SSE stream
# for the preview table; the JSON consumer reads the row_count only).
# ``AnswerDelta`` is intentionally dropped — ``AnswerFinished.answer``
# carries the authoritative full text; deltas are an SSE-view concern.


class _ResponseFold:
    """Accumulate ``ChatEvent``s into the fields of one ``ChatResponse``.

    The fold runs alongside the ``run_turn`` async generator so we never
    buffer the whole stream before responding — events are folded as
    they arrive. The reducer is the single place that maps events to
    fields; both the happy path and every degradation path flow through
    the same ``fold`` → ``build`` pair.

    Contract change from v1 (documented):
    On pre-QueryStarted not-answerable paths (gate failure or repair exhaustion),
    ChatResponse.sql is now None. Attempted SQL remains visible in the answer prose
    via compose_not_answerable(attempted_sql=...). Post-QueryStarted failures
    (timeout, db-execute) still surface sql because QueryStarted was emitted.
    """

    __slots__ = (
        "_answer",
        "_citations",
        "_duration_ms",
        "_last_event_was_error",
        "_reasoning_summary",
        "_row_count",
        "_saw_answer_finished",
        "_saw_clarification",
        "_sid",
        "_sql",
        "_query_ref",
    )

    def __init__(self, *, sid: str) -> None:
        self._sid = sid
        self._query_ref: QueryRef | None = None
        self._sql: str | None = None
        self._row_count: int | None = None
        self._duration_ms: float | None = None
        self._reasoning_summary: str | None = None
        self._citations: list[dict] = []
        self._answer: str = ""
        self._saw_clarification = False
        self._saw_answer_finished = False
        self._last_event_was_error = False

    def fold(self, ev: ChatEvent) -> None:
        """Fold one ``ChatEvent`` into the accumulator."""
        self._last_event_was_error = isinstance(ev, ChatError)
        if isinstance(ev, IntentClassified):
            self._query_ref = ev.query_ref
        elif isinstance(ev, ClarificationNeeded):
            self._saw_clarification = True
            self._answer = ev.question
        elif isinstance(ev, QueryStarted):
            self._sql = ev.sql
            # Pipeline emits IntentClassified before QueryStarted with the
            # same query reference, but capture defensively in case the
            # ordering ever changes.
            if self._query_ref is None:
                self._query_ref = ev.query_ref
        elif isinstance(ev, QueryFinished):
            self._row_count = ev.row_count
            self._duration_ms = ev.duration_ms
        elif isinstance(ev, Reasoning):
            self._reasoning_summary = ev.summary
        elif isinstance(ev, Citation):
            self._citations.append(
                {
                    "table_name": ev.table_name,
                    "metric_key": ev.metric_key,
                    "gap_key": ev.gap_key,
                }
            )
        elif isinstance(ev, AnswerFinished):
            self._answer = ev.answer
            self._saw_answer_finished = True
        elif isinstance(ev, ChatError):
            # ``ChatError.message`` is safe to render — the pipeline
            # redacts CoT and secrets before emission. It becomes the
            # answer; ``build`` flags the response as not-answerable
            # because no ``AnswerFinished`` followed.
            self._answer = ev.message
        # ``TurnStarted``, ``TableReady``, ``AnswerDelta``: no fold target
        # (see module-level docstring for rationale).

    def build(self, result: TurnResult) -> ChatResponse:
        """Build the final ``ChatResponse`` from accumulated events + out-of-band flags."""
        not_answerable = result.not_answerable
        not_answerable_note = result.not_answerable_note

        # Errors the pipeline did NOT mark as graceful
        # (``agent_failed`` / ``query_timeout`` / ``db_execute_failed``)
        # terminate the stream with a ``ChatError`` and no
        # ``AnswerFinished``; surface them here as not-answerable so the
        # JSON shape stays uniform.
        if not not_answerable and self._last_event_was_error and not self._saw_answer_finished:
            not_answerable = True
            not_answerable_note = self._answer

        # Legacy hardcoded string preserved verbatim so the JSON contract
        # for clarification turns doesn't drift. The pipeline doesn't
        # emit a ``Reasoning`` event on the clarify branch.
        if self._saw_clarification and self._reasoning_summary is None:
            self._reasoning_summary = "Clarification needed before query."

        return ChatResponse(
            session_id=self._sid,
            answer=self._answer,
            citations=self._citations,
            not_answerable=not_answerable,
            not_answerable_note=not_answerable_note,
            query_ref=self._query_ref,
            sql=self._sql,
            row_count=self._row_count,
            duration_ms=self._duration_ms,
            reasoning_summary=self._reasoning_summary,
        )


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Run one chat turn end-to-end and return the final answer.

    Thin consumer of :func:`chat_server.pipeline.run_turn`: resolves the
    session id, drains the ``ChatEvent`` generator, and folds the
    events into one ``ChatResponse``. The cascade (agent → gate →
    dry-run → repair → execute → compose) lives in exactly one place
    — ``run_turn`` — and the JSON and SSE routes are both consumers
    of it.
    """
    store = get_store()
    sid = _resolve_session_id(store, req.session_id, _title_from(req.message))

    fold = _ResponseFold(sid=sid)
    result = TurnResult()
    try:
        async for ev in run_turn(sid, req.message, result=result):
            fold.fold(ev)
    except Exception as exc:  # noqa: BLE001
        log.exception("chat: pipeline raised before producing events; sid=%s", sid)
        raise HTTPException(
            status_code=500,
            detail=f"pipeline failed: {type(exc).__name__}",
        ) from None

    return fold.build(result)


__all__ = ["ChatRequest", "ChatResponse", "router"]


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
        try:
            async for ev in run_turn(sid, req.message):
                d = to_sse_dict(ev)
                yield f"event: {d['event']}\ndata: {d['data']}\n\n"
        except Exception as exc:  # noqa: BLE001
            log.exception("chat_stream: pipeline raised; sid=%s", sid)
            fallback = to_sse_dict(
                ChatError(code="stream_failed", message=f"{type(exc).__name__}: {exc}")
            )
            yield f"event: {fallback['event']}\ndata: {fallback['data']}\n\n"

    return EventSourceResponse(event_gen())
