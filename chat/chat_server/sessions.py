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

import contextlib
import datetime as _dt
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from .clarify import ClarificationState
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

    def _model_history_path(self, session_id: str) -> Path:
        """Path for the Pydantic AI ``ModelMessage`` history snapshot.

        Sibling to ``<session_id>.jsonl`` (visible messages) and
        ``<session_id>.meta.json`` (session metadata); the ``.model.``
        infix disambiguates from both. Each turn's snapshot is the full
        serialized history (``result.all_messages_json()``); it is
        written atomically so a partial write never leaves a torn file.
        """
        return self._root / f"{session_id}.model.jsonl"

    def _clarify_path(self, session_id: str) -> Path:
        """Path for the pending-clarification state (Stage 3.6).

        Sibling to ``<session_id>.jsonl`` (visible messages) and
        ``<session_id>.meta.json`` (session metadata); the
        ``.clarify.`` infix disambiguates from the model-history
        snapshot (``<session_id>.model.jsonl``) and keeps the three
        artifacts distinct on disk.

        Holds a single ``ClarificationState`` JSON document — there is
        at most one pending clarification per session at any time, so a
        single-object file (not a JSONL stream) is the natural shape.
        Written atomically via the ``.tmp`` + ``os.replace`` pattern
        shared with the model-history store.
        """
        return self._root / f"{session_id}.clarify.json"

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

    def list_all(self) -> list[SessionMeta]:
        """Return every session by reading all sibling ``*.meta.json`` files.

        Named ``list_all`` (not ``list``) to avoid shadowing the builtin
        ``list`` on the class body — that shadow made ty reject
        ``list[SessionMeta]`` annotations elsewhere in this module
        ("Invalid subscript of object of type `def list(self) -> Unknown`").
        """
        with self._lock:
            results: list[SessionMeta] = []
            for meta_path in sorted(self._root.glob("*.meta.json")):
                try:
                    payload: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
                    results.append(SessionMeta.model_validate(payload))
                except Exception:  # noqa: BLE001 - skip corrupt entries
                    continue
        return results

    # -- model-history store (parallel to the visible JSONL store) -------
    # The visible-JSONL contract above is UNCHANGED. These methods are a
    # sibling store for Pydantic AI's ``ModelMessage`` history (the full
    # agent transcript, tool calls and all), one atomic overwrite per
    # turn. The filename ``<id>.model.jsonl`` is deliberately distinct
    # from ``<id>.jsonl`` (visible) and ``<id>.meta.json`` (metadata) so
    # the three siblings can coexist without collision.
    #
    # IO contract: both methods MAY raise. Callers in ``pipeline.py`` /
    # ``routes/chat.py`` wrap them in try/except because a missing or
    # unreadable history file is the expected path on a fresh session
    # (``load_model_history`` returns ``[]`` for absent) and a failed
    # write must not break the turn (mirrors the visible store's
    # robustness pattern at the caller layer — see
    # ``_safe_append_user`` in pipeline.py).

    def append_model_history(self, session_id: str, messages_json: bytes) -> None:
        """Atomically overwrite the model-history snapshot for one session.

        ``messages_json`` is the full ``result.all_messages_json()`` payload
        (bytes). One write per turn — the Pydantic AI history is a
        coherent list that must round-trip intact, so we never append
        per-message (an append would interleave turns and break
        ``ModelMessagesTypeAdapter.validate_python``).

        Atomicity: write to a sibling ``.tmp`` file then ``os.replace``
        into place. ``os.replace`` is atomic on POSIX and Windows, so a
        concurrent reader never sees a torn file.
        """
        path = self._model_history_path(session_id)
        tmp = path.parent / f".{path.name}.tmp"
        try:
            tmp.write_bytes(messages_json)
            os.replace(tmp, path)
        finally:
            # Best-effort cleanup if the replace didn't run (write failed
            # before rename, or os.replace itself blew up).
            with contextlib.suppress(Exception):
                tmp.unlink()

    def load_model_history(self, session_id: str) -> list[Any]:
        """Load the raw parsed history list for one session.

        Returns ``[]`` when no snapshot file exists yet (fresh session,
        or a turn whose post-call persistence hasn't completed yet).
        Returns the raw ``json.loads`` output — the caller is responsible
        for validating each item via
        ``pydantic_ai.messages.ModelMessagesTypeAdapter.validate_python``
        before handing the list to ``agent.run(message_history=...)``.

        Raises on a present-but-corrupt file (``json.JSONDecodeError``,
        OSError). The caller is expected to wrap this in try/except and
        degrade to an empty history list rather than crash the turn.
        """
        path = self._model_history_path(session_id)
        if not path.exists() or path.stat().st_size == 0:
            return []
        raw = path.read_bytes()
        payload: Any = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, list):
            # A previous turn wrote something other than a list — treat
            # as corrupt; the caller will catch the validation error.
            raise ValueError(f"model history at {path} is not a JSON array")
        return payload

    # -- pending-clarification store (Stage 3.6) ------------------------
    # Companion to the model-history store above. The visible-JSONL
    # contract and the 3.5 model-history methods are UNCHANGED. These
    # three methods manage a side-channel for the clarification
    # follow-up state machine: a single ``ClarificationState`` document
    # per session, written atomically and read with strict "never
    # raise" semantics so the pipeline / route can treat ``None`` as
    # "no pending clarification" without a try/except ladder.
    #
    # IO contract:
    # * ``get_pending_clarification`` NEVER raises (returns ``None`` on
    #   any failure path: absent file, JSON decode error, validation
    #   error, OSError, stale timestamp).
    # * ``set_pending_clarification`` / ``clear_pending_clarification``
    #   MAY raise (disk full, permissions, etc.) — callers in
    #   ``pipeline.py`` / ``routes/chat.py`` wrap them in try/except
    #   because a failed side-channel write must not break the turn
    #   (mirrors the robustness pattern of the visible store at the
    #   caller layer).

    def set_pending_clarification(
        self,
        session_id: str,
        state: ClarificationState,
    ) -> None:
        """Atomically overwrite the pending-clarification file for ``session_id``.

        Mirrors the atomic-write shape of
        :meth:`append_model_history`: serialize the state, write to a
        sibling ``.tmp``, then ``os.replace`` into place. A failed
        write is best-effort cleaned up via the ``finally`` block so
        no half-written ``.tmp`` lingers across turns.
        """
        path = self._clarify_path(session_id)
        tmp = path.parent / f".{path.name}.tmp"
        try:
            tmp.write_text(state.model_dump_json(), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            # Best-effort cleanup if the replace didn't run (write failed
            # before rename, or os.replace itself blew up).
            with contextlib.suppress(Exception):
                tmp.unlink()

    def get_pending_clarification(self, session_id: str) -> ClarificationState | None:
        """Return the pending clarification for ``session_id``, or ``None``.

        "None" covers four distinct conditions:

        1. The file does not exist (no clarification was ever set, or
           the previous one was cleared).
        2. The file exists but cannot be read (``OSError`` — disk
           vanished, permissions, etc.). Treated as "no pending" so
           the turn continues without enrichment.
        3. The file exists but its contents are corrupt (JSON decode
           failure or a Pydantic ``ValidationError`` against
           :class:`ClarificationState`). Best-effort cleared so the
           corruption does not recur on every subsequent turn.
        4. The file is structurally valid but stale per
           :meth:`ClarificationState.is_stale`. Best-effort cleared so
           the user is not trapped in a stale clarify loop.

        ``get_pending_clarification`` NEVER raises — every failure
        path collapses to ``None``.
        """
        path = self._clarify_path(session_id)
        try:
            if not path.exists() or path.stat().st_size == 0:
                return None
            raw = path.read_text(encoding="utf-8")
            state = ClarificationState.model_validate_json(raw)
        except (FileNotFoundError, json.JSONDecodeError, ValidationError, OSError):
            # Best-effort: clear the corrupt/unreadable file so we
            # don't keep tripping on it. Failures here are swallowed —
            # we're already in a degraded path and the caller only
            # cares about returning ``None``.
            with contextlib.suppress(Exception):
                path.unlink()
            return None
        if state.is_stale():
            # Stale pending clarification: discard and clear the file
            # so the next turn starts fresh. Mirrors the corrupt-file
            # best-effort cleanup above.
            with contextlib.suppress(Exception):
                path.unlink()
            return None
        return state

    def clear_pending_clarification(self, session_id: str) -> None:
        """Remove the pending-clarification file for ``session_id``.

        Idempotent: calling when no file exists is a no-op. Best-effort
        cleanup — a failed ``unlink`` (file already gone, permissions,
        etc.) is swallowed so the caller can fire this safely on every
        non-clarify plan outcome.
        """
        path = self._clarify_path(session_id)
        with contextlib.suppress(Exception):
            path.unlink()


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
