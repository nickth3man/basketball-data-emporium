"""Session REST routes.

Mounted under ``/api`` by ``chat_server.main``. Paths in this file are bare
(``/sessions``, ``/sessions/{id}``, ``/debug/artifacts/{id}``) so they
share the FastAPI prefix declaration done in ``main``.

PLAN §7.9 (rest surface) + §7.10 (visible sessions / debug artifacts).
"""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

from ..sessions import (
    HistoryPage,
    SessionMeta,
    SessionNotFound,
    get_store,
)

router = APIRouter(tags=["sessions"])


# --- Request / response models -------------------------------------------


class CreateSessionRequest(BaseModel):
    """Body for `POST /api/sessions` (PLAN §7.9).

    ``title`` is optional; the store falls back to ``"New chat"`` when
    the field is omitted, empty, or whitespace-only.
    """

    title: str | None = Field(default=None, max_length=200)


# --- Pagination helpers --------------------------------------------------

# Page-size bounds for `GET /api/sessions/{id}/history`. The clamp lives in
# the route layer so the store stays policy-free; tests exercise both edges
# of the clamp directly.
_DEFAULT_HISTORY_LIMIT = 50
_MAX_HISTORY_LIMIT = 200


def clamp_limit(value: int | None, default: int, maximum: int) -> int:
    """Clamp an optional pagination limit into [1, maximum].

    - ``None`` (or any value <= 0) → ``default``.
    - values above ``maximum`` → ``maximum``.

    Centralising the policy here makes it testable in isolation from the
    FastAPI request lifecycle.
    """
    if value is None or value <= 0:
        return default
    return min(value, maximum)


def _session_not_found() -> HTTPException:
    """Single source of truth for the 404 shape."""
    return HTTPException(status_code=404, detail="session not found")


# --- Routes --------------------------------------------------------------


@router.post(
    "/sessions",
    response_model=SessionMeta,
    status_code=201,
)
def create_session(
    body: CreateSessionRequest | None = Body(default=None),  # noqa: B008 - FastAPI marker
) -> SessionMeta:
    """Create a new session with the given (or default) title.

    Body is optional so a bare ``POST /api/sessions`` (no payload) yields
    the same default-title session. Returns ``201 Created`` with the
    freshly minted `SessionMeta`.
    """
    title = body.title if body is not None else None
    return get_store().create(title)


@router.get("/sessions", response_model=list[SessionMeta])
def list_sessions() -> list[SessionMeta]:
    """Return every session's meta (no messages).

    Not explicitly in PLAN §7.9 but useful for the UI's session-list view;
    kept here so the OpenAPI snapshot exposes the shape to the frontend
    codegen.
    """
    return get_store().list_all()


@router.get("/sessions/{session_id}/history", response_model=HistoryPage)
def get_history(
    session_id: str,
    limit: int = Query(
        default=_DEFAULT_HISTORY_LIMIT,
        description=(
            "Page size. Out-of-range (non-positive, >200) is clamped to "
            "the default of 50 by the server."
        ),
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Number of oldest messages to skip.",
    ),
) -> HistoryPage:
    """Return one paginated window of visible messages.

    ``limit`` is clamped to ``[1, 200]``; non-positive or oversized
    values fall back to the default. ``offset`` must be non-negative
    (FastAPI rejects negatives with 422). Raises ``404`` for unknown
    sessions so the UI can distinguish "empty history" from "session
    was deleted".
    """
    try:
        return get_store().history(
            session_id,
            limit=clamp_limit(limit, _DEFAULT_HISTORY_LIMIT, _MAX_HISTORY_LIMIT),
            offset=offset,
        )
    except SessionNotFound:
        raise _session_not_found() from None


@router.get("/sessions/{session_id}", response_model=SessionMeta)
def get_session(session_id: str) -> SessionMeta:
    """Return meta for one session."""
    try:
        return get_store().get(session_id)
    except SessionNotFound:
        raise _session_not_found() from None


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str) -> None:
    """Clear the visible history (PLAN §7.9 manual-clear).

    The session's meta (title, id, created_at) is preserved so the UI can
    keep showing the empty session in its list. ``404`` if the meta is
    missing — the caller asked to clear something that never existed.
    """
    try:
        get_store().clear(session_id)
    except SessionNotFound:
        raise _session_not_found() from None
    return None


@router.get("/debug/artifacts/{artifact_id}")
def get_debug_artifact(artifact_id: str) -> dict[str, str]:
    """Fetch a query/model debug artifact by id.

    Phase 2 stub: no real artifact index exists yet — Phase 4 will
    materialise artifacts under ``chat/logs/{queries,model}`` and resolve
    ``artifact_id`` (a query id or a model-log filename) to the matching
    JSONL/SQL file on disk. For now every id 404s so clients can rely on
    the contract; the response body documents the gap.
    """
    raise HTTPException(
        status_code=404,
        detail="artifact not found",
    )


__all__ = [
    "CreateSessionRequest",
    "clamp_limit",
    "router",
]
