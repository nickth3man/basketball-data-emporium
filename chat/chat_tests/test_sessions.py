"""Tests for the session store and the `/api/sessions` REST surface.

These tests are file-only and do NOT need the warehouse. Each test gets a
fresh temp directory via the ``tmp_path`` fixture; the singleton
``get_store()`` is monkeypatched at the route layer so handlers resolve
to the temp store instead of writing under ``./data``.

Mirror the patterns in `chat_tests/test_validation.py` and
`chat_tests/test_json_safe.py` (sync-style TestClient, no async fixtures).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from chat_server.main import app
from chat_server.routes import sessions as sessions_routes
from chat_server.sessions import (
    HistoryPage,
    SessionMessage,
    SessionMeta,
    SessionNotFound,
    SessionStore,
)

# --- Helpers / fixtures --------------------------------------------------


@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    """Build a tmp_path-backed store and monkeypatch it into the routes module.

    Routes call `chat_server.routes.sessions.get_store()`. The routes
    module imported that symbol from `chat_server.sessions` at import
    time, so patching the routes module's binding is sufficient.
    """
    store = SessionStore(tmp_path)
    monkeypatch.setattr(sessions_routes, "get_store", lambda: store)
    return store


@pytest.fixture
def client(temp_store):
    """A `TestClient` over the real `app` (no DB calls in Phase 2 routes)."""
    return TestClient(app)


# --- clamp_limit unit tests ---------------------------------------------


@pytest.mark.parametrize(
    ("value", "default", "maximum", "expected"),
    [
        (None, 50, 200, 50),
        (0, 50, 200, 50),
        (-5, 50, 200, 50),
        (1, 50, 200, 1),
        (50, 50, 200, 50),
        (200, 50, 200, 200),
        (201, 50, 200, 200),
        (10_000, 50, 200, 200),
    ],
)
def test_clamp_limit_table(value, default, maximum, expected):
    """``clamp_limit`` policy: None / non-positive → default; >max → max."""
    from chat_server.routes.sessions import clamp_limit

    assert clamp_limit(value, default, maximum) == expected


# --- session store unit tests -------------------------------------------


def test_store_create_assigns_id_and_default_title(temp_store):
    meta = temp_store.create()
    assert isinstance(meta, SessionMeta)
    assert meta.id and len(meta.id) >= 8
    assert meta.status == "active"
    assert meta.message_count == 0
    assert meta.title == "New chat"


def test_store_create_respects_optional_title(temp_store):
    meta = temp_store.create(title="  hello  ")
    assert meta.title == "hello"


def test_store_get_unknown_raises_keyerror(temp_store):
    with pytest.raises(KeyError):
        temp_store.get("does-not-exist")


def test_store_get_returns_same_meta(temp_store):
    a = temp_store.create(title="x")
    b = temp_store.get(a.id)
    assert b.id == a.id
    assert b.title == "x"


def test_store_append_then_history_roundtrip(temp_store):
    meta = temp_store.create(title="rt")
    temp_store.append_message(meta.id, "user", "hi")
    temp_store.append_message(meta.id, "assistant", "hello")

    page = temp_store.history(meta.id, limit=10, offset=0)
    assert isinstance(page, HistoryPage)
    assert page.session_id == meta.id
    assert page.total == 2
    assert len(page.messages) == 2
    assert page.messages[0].role == "user"
    assert page.messages[0].content == "hi"
    assert page.messages[1].content == "hello"
    assert isinstance(page.messages[0], SessionMessage)


def test_store_history_paginates_correctly(temp_store):
    meta = temp_store.create()
    for i in range(7):
        temp_store.append_message(meta.id, "user", f"m{i}")

    a = temp_store.history(meta.id, limit=3, offset=0)
    b = temp_store.history(meta.id, limit=3, offset=3)
    c = temp_store.history(meta.id, limit=3, offset=6)
    d = temp_store.history(meta.id, limit=3, offset=99)

    assert [m.content for m in a.messages] == ["m0", "m1", "m2"]
    assert [m.content for m in b.messages] == ["m3", "m4", "m5"]
    assert [m.content for m in c.messages] == ["m6"]
    assert d.messages == []
    assert d.total == 7
    assert d.offset == 99


def test_store_clear_resets_messages_keeps_meta(temp_store):
    meta = temp_store.create(title="keep")
    temp_store.append_message(meta.id, "user", "x")
    temp_store.append_message(meta.id, "assistant", "y")

    temp_store.clear(meta.id)

    after = temp_store.get(meta.id)
    assert after.id == meta.id
    assert after.title == "keep"
    assert after.message_count == 0

    page = temp_store.history(meta.id, limit=50)
    assert page.total == 0
    assert page.messages == []


def test_store_clear_unknown_raises(temp_store):
    with pytest.raises(SessionNotFound):
        temp_store.clear("nope")


def test_store_list_includes_all_created(temp_store):
    a = temp_store.create(title="A")
    b = temp_store.create(title="B")

    items = temp_store.list()
    titles = {m.title for m in items}
    ids = {m.id for m in items}
    assert {"A", "B"}.issubset(titles)
    assert {a.id, b.id}.issubset(ids)


# --- /api/sessions REST tests -------------------------------------------


def test_post_sessions_returns_201_and_meta(client):
    r = client.post("/api/sessions", json={"title": "first"})
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == "first"
    assert body["status"] == "active"
    assert body["message_count"] == 0
    assert body["id"]
    assert body["created_at"]


def test_post_sessions_omits_title_uses_default(client):
    r = client.post("/api/sessions", json={})
    assert r.status_code == 201
    assert r.json()["title"] == "New chat"


def test_post_sessions_no_body_uses_default(client):
    r = client.post("/api/sessions")
    assert r.status_code == 201
    assert r.json()["title"] == "New chat"


def test_post_sessions_empty_title_uses_default(client):
    r = client.post("/api/sessions", json={"title": "   "})
    assert r.status_code == 201
    assert r.json()["title"] == "New chat"


def test_get_sessions_returns_list(client):
    client.post("/api/sessions", json={"title": "A"})
    client.post("/api/sessions", json={"title": "B"})

    r = client.get("/api/sessions")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    titles = {s["title"] for s in body}
    assert {"A", "B"}.issubset(titles)


def test_get_session_returns_meta(client):
    created = client.post("/api/sessions", json={"title": "meta"}).json()
    r = client.get(f"/api/sessions/{created['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]
    assert r.json()["title"] == "meta"


def test_get_session_404_for_unknown(client):
    r = client.get("/api/sessions/does-not-exist")
    assert r.status_code == 404
    assert r.json()["detail"] == "session not found"


def test_history_returns_messages_after_append(client, temp_store):
    created = client.post("/api/sessions", json={"title": "h"}).json()
    sid = created["id"]
    temp_store.append_message(sid, "user", "ping")
    temp_store.append_message(sid, "assistant", "pong")

    r = client.get(f"/api/sessions/{sid}/history")
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == sid
    assert body["total"] == 2
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert [m["content"] for m in body["messages"]] == ["ping", "pong"]
    for msg in body["messages"]:
        assert "ts" in msg and msg["ts"]


def test_history_404_for_unknown_session(client):
    r = client.get("/api/sessions/nope/history")
    assert r.status_code == 404


def test_history_clamps_limit_to_max(client):
    created = client.post("/api/sessions", json={"title": "clamp"}).json()
    sid = created["id"]

    # Way above the maximum → clamped to 200.
    r_high = client.get(f"/api/sessions/{sid}/history?limit=10000")
    assert r_high.status_code == 200
    assert r_high.json()["limit"] == 200

    # At the maximum → still 200.
    r_max = client.get(f"/api/sessions/{sid}/history?limit=200")
    assert r_max.json()["limit"] == 200

    # Non-positive → default (50).
    r_zero = client.get(f"/api/sessions/{sid}/history?limit=0")
    assert r_zero.json()["limit"] == 50
    r_neg = client.get(f"/api/sessions/{sid}/history?limit=-10")
    assert r_neg.json()["limit"] == 50


def test_history_pagination_via_route(client, temp_store):
    created = client.post("/api/sessions", json={"title": "page"}).json()
    sid = created["id"]
    for i in range(5):
        temp_store.append_message(sid, "user", f"m{i}")

    page0 = client.get(f"/api/sessions/{sid}/history?limit=2&offset=0").json()
    page1 = client.get(f"/api/sessions/{sid}/history?limit=2&offset=2").json()
    page2 = client.get(f"/api/sessions/{sid}/history?limit=2&offset=4").json()

    assert [m["content"] for m in page0["messages"]] == ["m0", "m1"]
    assert [m["content"] for m in page1["messages"]] == ["m2", "m3"]
    assert [m["content"] for m in page2["messages"]] == ["m4"]


def test_delete_session_clears_messages_keeps_session(client, temp_store):
    created = client.post("/api/sessions", json={"title": "del"}).json()
    sid = created["id"]
    temp_store.append_message(sid, "user", "x")
    temp_store.append_message(sid, "assistant", "y")

    rd = client.delete(f"/api/sessions/{sid}")
    assert rd.status_code == 204
    assert rd.content == b""

    # Meta still resolvable, but the count is back to zero.
    rg = client.get(f"/api/sessions/{sid}")
    assert rg.status_code == 200
    assert rg.json()["id"] == sid
    assert rg.json()["message_count"] == 0

    rh = client.get(f"/api/sessions/{sid}/history")
    assert rh.status_code == 200
    assert rh.json()["total"] == 0
    assert rh.json()["messages"] == []


def test_delete_session_404_for_unknown(client):
    r = client.delete("/api/sessions/nope")
    assert r.status_code == 404


def test_debug_artifact_stub_returns_404(client):
    r = client.get("/api/debug/artifacts/whatever")
    assert r.status_code == 404
    assert r.json()["detail"] == "artifact not found"


def test_openapi_exposes_session_paths(client):
    """Phase 2 contract: /openapi.json exposes the new /api/sessions paths.

    A later fixer generates frontend types from this schema; pinning it
    here means a future regression to route registration will fail loudly
    rather than silently.
    """
    schema = client.get("/openapi.json").json()
    paths = set(schema["paths"].keys())
    assert "/api/sessions" in paths
    assert "/api/sessions/{session_id}" in paths
    assert "/api/sessions/{session_id}/history" in paths
    assert "/api/debug/artifacts/{artifact_id}" in paths
