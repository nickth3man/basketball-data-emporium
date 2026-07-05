"""JSONL session store for visible chat history.

Visible sessions live at ``{chat_data_dir}/sessions/<session_id>.jsonl``
(one JSON object per line: ``{role, content, ts}``) with a sibling
``<session_id>.meta.json`` carrying ``id, title, status, created_at,
message_count``. Messages are append-only; manual history clears
(``DELETE /api/sessions/{id}``) truncate the messages file but keep the
meta so the session remains discoverable.

PLAN §6, §7.10.
"""

from __future__ import annotations

import datetime as _dt
import json
import secrets
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .config import get_settings


class SessionMeta(BaseModel):
    """Metadata for a single visible chat session.

    Surfaces the minimum the UI needs to render a session list without
    scanning the per-session JSONL messages file.
    """

    id: str
    title: str
    status: str = "active"  # active | archived
    created_at: _dt.datetime
    message_count: int = 0


class SessionMessage(BaseModel):
    """One visible message in a session.

    Role is a free-form string at the storage layer; the route layer
    enforces the ``"user" | "assistant"`` subset at write time (Phase 4
    SSE pipeline). Keeping the model open at the store keeps this module
    independent of that pipeline.
    """

    role: str
    content: str
    ts: _dt.datetime


class HistoryPage(BaseModel):
    """One paginated window over a session's visible messages."""

    session_id: str
    messages: list[SessionMessage]
    total: int
    limit: int
    offset: int


class SessionNotFound(KeyError):  # noqa: N818 - name matches PLAN §7.10
    """Raised when a session id is unknown.

    Subclasses `KeyError` so plain ``except KeyError`` keeps working; the
    routes translate this into a 404 via ``HTTPException``.
    """


def _default_title(title: str | None) -> str:
    """Resolve an optional user-supplied title to a non-empty string."""
    if title is None:
        return "New chat"
    stripped = title.strip()
    return stripped or "New chat"


def _utcnow() -> _dt.datetime:
    """Timezone-aware ``now()`` in UTC. Centralised for testability."""
    return _dt.datetime.now(tz=_dt.UTC)


class SessionStore:
    """File-backed JSONL session store. Thread-safe via a single lock."""

    def __init__(self, root: Path) -> None:
        # Sessions always live in `<root>/sessions/` so a single data root
        # can hold other derived artefacts (logs, caches) without colliding
        # with the JSONL files.
        self._root = Path(root) / "sessions"
        self._lock = threading.Lock()
        self._root.mkdir(parents=True, exist_ok=True)

    # -- path helpers -------------------------------------------------------

    def _msgs_path(self, session_id: str) -> Path:
        return self._root / f"{session_id}.jsonl"

    def _meta_path(self, session_id: str) -> Path:
        return self._root / f"{session_id}.meta.json"

    # -- low-level meta helpers (caller holds the lock) ---------------------

    def _read_meta(self, session_id: str) -> SessionMeta:
        meta_path = self._meta_path(session_id)
        if not meta_path.exists():
            raise SessionNotFound(session_id)
        payload: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
        return SessionMeta.model_validate(payload)

    def _write_meta(self, meta: SessionMeta) -> None:
        self._meta_path(meta.id).write_text(
            meta.model_dump_json(indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _read_messages(path: Path) -> list[SessionMessage]:
        """Load every JSONL message line from `path` into memory.

        Caller is responsible for any locking. The path is allowed to
        exist but be empty (zero messages); the open-for-read returns
        ``""`` immediately and we return ``[]``.
        """
        messages: list[SessionMessage] = []
        if path.exists() and path.stat().st_size > 0:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    messages.append(SessionMessage.model_validate_json(line))
        return messages

    # -- public API ---------------------------------------------------------

    def create(self, title: str | None = None) -> SessionMeta:
        """Create a new session with a random id; return the meta."""
        session_id = secrets.token_urlsafe(12)
        meta = SessionMeta(
            id=session_id,
            title=_default_title(title),
            status="active",
            created_at=_utcnow(),
            message_count=0,
        )
        with self._lock:
            # Create the empty messages file so list/history don't race.
            self._msgs_path(session_id).touch()
            self._write_meta(meta)
        return meta

    def get(self, session_id: str) -> SessionMeta:
        """Return the meta for one session, or raise `SessionNotFound`."""
        with self._lock:
            return self._read_meta(session_id)

    def append_message(self, session_id: str, role: str, content: str) -> SessionMessage:
        """Append one message and bump the cached message count.

        Returns the stored message (with the timestamp the store assigned,
        so the caller can echo it back through SSE if needed). Raises
        `SessionNotFound` if the session has no meta file.
        """
        msg = SessionMessage(role=role, content=content, ts=_utcnow())
        with self._lock:
            meta = self._read_meta(session_id)
            with self._msgs_path(session_id).open("a", encoding="utf-8") as fh:
                fh.write(msg.model_dump_json())
                fh.write("\n")
            meta = meta.model_copy(update={"message_count": meta.message_count + 1})
            self._write_meta(meta)
        return msg

    def history(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> HistoryPage:
        """Return one paginated window over the visible messages."""
        if limit < 0:
            limit = 0
        if offset < 0:
            offset = 0
        with self._lock:
            meta = self._read_meta(session_id)
            total = meta.message_count
            messages = self._read_messages(self._msgs_path(session_id))
            window = messages[offset : offset + limit]
        return HistoryPage(
            session_id=session_id,
            messages=window,
            total=total,
            limit=limit,
            offset=offset,
        )

    def clear(self, session_id: str) -> None:
        """Truncate the visible history; reset the cached count.

        The meta (title, created_at, status) is preserved so the session
        still appears in ``list()`` afterwards.
        """
        with self._lock:
            meta = self._read_meta(session_id)
            # ``open(..., "w")`` truncates and creates if missing.
            self._msgs_path(session_id).open("w", encoding="utf-8").close()
            meta = meta.model_copy(update={"message_count": 0})
            self._write_meta(meta)

    def list(self) -> list[SessionMeta]:
        """Return every session by reading all sibling ``*.meta.json`` files."""
        with self._lock:
            results: list[SessionMeta] = []
            for meta_path in sorted(self._root.glob("*.meta.json")):
                try:
                    payload: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
                    results.append(SessionMeta.model_validate(payload))
                except Exception:  # noqa: BLE001 - skip corrupt entries
                    continue
        return results


_store: SessionStore | None = None
_store_lock = threading.Lock()


def _resolve_data_root() -> Path:
    """Return the configured chat data directory as a resolved Path.

    Resolves to an absolute path so the store is anchored to one place
    even when the working directory changes mid-process (tests, uvicorn
    reload, etc.).
    """
    root = Path(get_settings().chat_data_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_store() -> SessionStore:
    """Return the process-wide `SessionStore` (lazily constructed).

    The first call resolves ``settings.chat_data_dir`` and builds the
    store; subsequent calls return the same instance. Tests that need
    isolation should call `reset_store_for_tests()` first or monkeypatch
    this function at the route layer.
    """
    global _store
    with _store_lock:
        if _store is None:
            _store = SessionStore(_resolve_data_root())
        return _store


def reset_store_for_tests() -> None:
    """Drop the cached singleton (test helper only)."""
    global _store
    with _store_lock:
        _store = None


__all__ = [
    "HistoryPage",
    "SessionMessage",
    "SessionMeta",
    "SessionNotFound",
    "SessionStore",
    "get_store",
    "reset_store_for_tests",
]
