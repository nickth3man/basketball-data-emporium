"""Route integration tests for POST /api/chat and POST /api/chat/stream."""

from __future__ import annotations

import datetime as _dt
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

from fastapi.testclient import TestClient

from chat_server.events import (
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
from chat_server.main import app
from chat_server.pipeline import TurnResult
from chat_server.routes.chat import _ResponseFold
from chat_server.sessions import SessionStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_query_ref() -> QueryRef:
    return QueryRef(source="catalog", tables=["mart_player_career"])


def _make_mock_run_turn(events: Sequence[ChatEvent], **kwargs: Any):
    """Return an async generator function that yields *events*.

    Extra keyword arguments are evaluated lazily at call time so test
    functions can capture mutable state (e.g. a ``result`` object).
    """

    async def gen(
        session_id: str,
        message: str,
        *,
        result: TurnResult | None = None,
        store: SessionStore | None = None,
    ) -> AsyncIterator[ChatEvent]:
        # Apply lazy kwargs — typically ``set_result_not_answerable``
        # so the mock can mirror the pipeline's ``_mark_not_answerable``.
        for ev in events:
            yield ev

    return gen


def _make_mock_run_turn_with_result_setter(
    events: Sequence[ChatEvent],
    set_result: Any = None,
):
    """Like ``_make_mock_run_turn`` but mutates *result* before yielding.

    ``set_result`` is a callable ``(result) -> None`` invoked before the
    first event.
    """

    async def gen(
        session_id: str,
        message: str,
        *,
        result: TurnResult | None = None,
        store: SessionStore | None = None,
    ) -> AsyncIterator[ChatEvent]:
        if set_result is not None and result is not None:
            set_result(result)
        for ev in events:
            yield ev

    return gen


# ---------------------------------------------------------------------------
# _ResponseFold unit tests  (no HTTP, no monkeypatching)
# ---------------------------------------------------------------------------


def test_fold_happy_path():
    """Fold a complete happy-path event sequence into ``ChatResponse``."""
    fold = _ResponseFold(sid="test-sid")
    qr = _make_query_ref()

    fold.fold(IntentClassified(query_ref=qr, confidence=1.0))
    fold.fold(
        QueryStarted(query_id="q1", query_ref=qr, sql="SELECT player_id FROM dim_player LIMIT 5")
    )
    fold.fold(
        QueryFinished(
            query_id="q1",
            duration_ms=5.0,
            row_count=100,
            columns=["player_id"],
            truncated=False,
        )
    )
    fold.fold(Reasoning(summary="test reasoning", execution_plan="SELECT"))
    fold.fold(Citation(table_name="dim_player", metric_key="player_id", gap_key=None))
    fold.fold(AnswerFinished(answer="test answer"))

    result = TurnResult()
    response = fold.build(result)

    assert response.session_id == "test-sid"
    assert response.sql == "SELECT player_id FROM dim_player LIMIT 5"
    assert response.row_count == 100
    assert response.duration_ms == 5.0
    assert response.reasoning_summary == "test reasoning"
    assert len(response.citations) == 1
    assert response.citations[0]["metric_key"] == "player_id"
    assert response.answer == "test answer"
    assert response.not_answerable is False


def test_fold_error_terminal():
    """A ``ChatError`` as the last event with no ``AnswerFinished`` yields not_answerable."""
    fold = _ResponseFold(sid="s1")
    fold.fold(ChatError(code="agent_failed", message="agent failed: RuntimeError"))

    result = TurnResult()
    response = fold.build(result)

    assert response.not_answerable is True
    assert response.not_answerable_note == "agent failed: RuntimeError"
    assert response.answer == "agent failed: RuntimeError"


def test_fold_clarify_path():
    """A ``ClarificationNeeded`` yields the legacy reasoning_summary fallback."""
    fold = _ResponseFold(sid="s1")
    fold.fold(ClarificationNeeded(question="Which season should I use?"))

    result = TurnResult()
    response = fold.build(result)

    assert response.answer == "Which season should I use?"
    assert response.reasoning_summary == "Clarification needed before query."


def test_fold_error_after_answer_finished():
    """A ``ChatError`` after ``AnswerFinished`` does NOT set not_answerable."""
    fold = _ResponseFold(sid="s1")
    fold.fold(AnswerFinished(answer="done"))
    fold.fold(ChatError(code="stream_failed", message="stream failed: Oops"))

    result = TurnResult()
    response = fold.build(result)

    # The ChatError overwrites _answer (current fold behaviour), but
    # _saw_answer_finished guards the not_answerable flag so the
    # response is still marked as answerable.
    assert response.answer == "stream failed: Oops"
    assert response.not_answerable is False


# ---------------------------------------------------------------------------
# POST /api/chat route tests  (TestClient + monkeypatching)
# ---------------------------------------------------------------------------


def test_chat_route_happy_path(monkeypatch, tmp_path):
    """POST /api/chat returns answer, sql, row_count on the happy path."""
    import chat_server.routes.chat as routes_mod

    qr = _make_query_ref()
    events = [
        IntentClassified(query_ref=qr, confidence=1.0),
        QueryStarted(query_id="q1", query_ref=qr, sql="SELECT player_id FROM dim_player LIMIT 5"),
        QueryFinished(
            query_id="q1",
            duration_ms=3.0,
            row_count=5,
            columns=["player_id"],
            truncated=False,
        ),
        TableReady(
            columns=[ColumnSpec(name="player_id")],
            rows=[{"player_id": 1}],
            row_count=5,
            truncated=False,
        ),
        Reasoning(summary="listed players", execution_plan="SELECT"),
        Citation(table_name="dim_player", metric_key="player_id", gap_key=None),
        AnswerFinished(answer="Here are the players"),
    ]
    monkeypatch.setattr(routes_mod, "run_turn", _make_mock_run_turn(events))

    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(routes_mod, "get_store", lambda: store)

    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "show players"})

    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "Here are the players"
    assert data["sql"] == "SELECT player_id FROM dim_player LIMIT 5"
    assert data["row_count"] == 5
    assert data["not_answerable"] is False
    assert len(data["citations"]) == 1
    assert data["citations"][0]["metric_key"] == "player_id"


def test_chat_route_clarify_plan(monkeypatch, tmp_path):
    """POST /api/chat returns the clarification question as answer."""
    import chat_server.routes.chat as routes_mod

    events = [
        ClarificationNeeded(question="Which season should I use?", options=["2023-24", "2024-25"]),
    ]
    monkeypatch.setattr(routes_mod, "run_turn", _make_mock_run_turn(events))

    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(routes_mod, "get_store", lambda: store)

    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "show leaders"})

    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "Which season should I use?"
    assert data["reasoning_summary"] == "Clarification needed before query."


def test_chat_route_not_answerable_plan(monkeypatch, tmp_path):
    """POST /api/chat returns not_answerable=True for out-of-scope questions."""
    import chat_server.routes.chat as routes_mod

    def _mark_not_answerable(result: TurnResult) -> None:
        result.not_answerable = True
        result.not_answerable_note = "Warehouse has no salary data"

    events = [
        AnswerFinished(answer="This question cannot be answered with the available data."),
    ]
    monkeypatch.setattr(
        routes_mod,
        "run_turn",
        _make_mock_run_turn_with_result_setter(events, set_result=_mark_not_answerable),
    )

    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(routes_mod, "get_store", lambda: store)

    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "player salaries"})

    assert response.status_code == 200
    data = response.json()
    assert data["not_answerable"] is True
    assert data["not_answerable_note"] == "Warehouse has no salary data"


def test_chat_route_agent_failure(monkeypatch, tmp_path):
    """POST /api/chat folds a ChatError into not_answerable=True."""
    import chat_server.routes.chat as routes_mod

    events = [
        TurnStarted(session_id="s1", turn_id="t1", ts=_dt.datetime.now(tz=_dt.UTC)),
        ChatError(code="agent_failed", message="agent failed: RuntimeError"),
    ]
    monkeypatch.setattr(routes_mod, "run_turn", _make_mock_run_turn(events))

    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(routes_mod, "get_store", lambda: store)

    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "anything"})

    assert response.status_code == 200
    data = response.json()
    assert data["not_answerable"] is True
    assert data["not_answerable_note"] == "agent failed: RuntimeError"
    assert data["answer"] == "agent failed: RuntimeError"


# ---------------------------------------------------------------------------
# Session resolution tests
# ---------------------------------------------------------------------------


def test_chat_route_creates_session_when_null(monkeypatch, tmp_path):
    """POST /api/chat with ``session_id=null`` creates a new session."""
    import chat_server.routes.chat as routes_mod

    monkeypatch.setattr(routes_mod, "run_turn", _make_mock_run_turn([AnswerFinished(answer="ok")]))

    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(routes_mod, "get_store", lambda: store)

    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "hello"})

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] is not None
    assert len(data["session_id"]) > 0


def test_chat_route_reuses_existing_session(monkeypatch, tmp_path):
    """POST /api/chat with an existing session_id returns that same id."""
    import chat_server.routes.chat as routes_mod

    monkeypatch.setattr(routes_mod, "run_turn", _make_mock_run_turn([AnswerFinished(answer="ok")]))

    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(routes_mod, "get_store", lambda: store)

    existing = store.create(title="preexisting")

    client = TestClient(app)
    response = client.post("/api/chat", json={"session_id": existing.id, "message": "hello again"})

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == existing.id


def test_chat_route_creates_new_session_for_stale(monkeypatch, tmp_path):
    """POST /api/chat with a stale session_id creates a brand new session."""
    import chat_server.routes.chat as routes_mod

    monkeypatch.setattr(routes_mod, "run_turn", _make_mock_run_turn([AnswerFinished(answer="ok")]))

    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(routes_mod, "get_store", lambda: store)

    client = TestClient(app)
    response = client.post(
        "/api/chat", json={"session_id": "nonexistent-session-id", "message": "hello"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] is not None
    # The returned id must not be the stale one we sent.
    assert data["session_id"] != "nonexistent-session-id"


# ---------------------------------------------------------------------------
# POST /api/chat/stream SSE framing tests
# ---------------------------------------------------------------------------


def _parse_sse(text: str) -> list[dict[str, Any]]:
    """Parse a raw SSE body into a list of ``{"event": ..., "data": ...}`` dicts."""
    frames: list[dict[str, Any]] = []
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        frame: dict[str, Any] = {}
        for line in block.split("\n"):
            if line.startswith("event: "):
                frame["event"] = line[7:]
            elif line.startswith("data: "):
                try:
                    frame["data"] = json.loads(line[6:])
                except json.JSONDecodeError:
                    frame["data"] = line[6:]
        if frame:
            frames.append(frame)
    return frames


def test_chat_stream_emits_expected_sse_frames(monkeypatch, tmp_path):
    """POST /api/chat/stream yields valid SSE frames including ``table_ready``."""
    import chat_server.routes.chat as routes_mod

    qr = _make_query_ref()
    # Include every event type in the stream so we can verify framing.
    events = [
        TurnStarted(session_id="s1", turn_id="t1", ts=_dt.datetime.now(tz=_dt.UTC)),
        IntentClassified(query_ref=qr, confidence=1.0),
        QueryStarted(query_id="q1", query_ref=qr, sql="SELECT 1"),
        QueryFinished(
            query_id="q1",
            duration_ms=2.0,
            row_count=1,
            columns=["player_id"],
            truncated=False,
        ),
        TableReady(
            columns=[ColumnSpec(name="player_id")],
            rows=[{"player_id": 1}],
            row_count=1,
            truncated=False,
        ),
        Reasoning(summary="test", execution_plan="SELECT"),
        Citation(table_name="t", metric_key="m", gap_key=None),
        AnswerDelta(delta="Hello"),
        AnswerFinished(answer="Hello world"),
    ]
    monkeypatch.setattr(routes_mod, "run_turn", _make_mock_run_turn(events))

    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(routes_mod, "get_store", lambda: store)

    client = TestClient(app)
    response = client.post("/api/chat/stream", json={"message": "test stream"})

    assert response.status_code == 200
    frames = _parse_sse(response.text)

    # Every event in our list should appear as a frame.
    event_names = [f["event"] for f in frames]
    assert "turn_started" in event_names
    assert "intent_classified" in event_names
    assert "query_started" in event_names
    assert "query_finished" in event_names
    assert "table_ready" in event_names
    assert "reasoning" in event_names
    assert "citation" in event_names
    assert "answer_delta" in event_names
    assert "answer_finished" in event_names
    assert "error" not in event_names

    # Verify a specific frame payload.
    table_frames = [f for f in frames if f["event"] == "table_ready"]
    assert len(table_frames) == 1
    assert table_frames[0]["data"]["row_count"] == 1
    assert table_frames[0]["data"]["truncated"] is False


def test_chat_stream_error_fallback(monkeypatch, tmp_path):
    """POST /api/chat/stream yields a ChatError SSE frame when the pipeline raises."""
    import chat_server.routes.chat as routes_mod

    async def _broken_run_turn(
        session_id: str,
        message: str,
        *,
        result: TurnResult | None = None,
        store: SessionStore | None = None,
    ) -> AsyncIterator[ChatEvent]:
        yield TurnStarted(session_id=session_id, turn_id="t1", ts=_dt.datetime.now(tz=_dt.UTC))
        raise RuntimeError("unexpected crash")

    monkeypatch.setattr(routes_mod, "run_turn", _broken_run_turn)

    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(routes_mod, "get_store", lambda: store)

    client = TestClient(app)
    response = client.post("/api/chat/stream", json={"message": "boom"})

    # The response is still 200 — the error is embedded in the SSE stream.
    assert response.status_code == 200

    frames = _parse_sse(response.text)
    event_names = [f["event"] for f in frames]
    assert "turn_started" in event_names
    assert "error" in event_names

    error_frames = [f for f in frames if f["event"] == "error"]
    assert len(error_frames) == 1
    msg = error_frames[0]["data"]["message"]
    assert "RuntimeError" in msg
    assert "unexpected crash" not in msg  # raw str(exc) must not leak
