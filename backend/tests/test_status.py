"""Phase 1 tests.

These tests do not require a live DuckDB file — they inject a fake
`DuckDBPool` that always returns the same stub connection. The real
`DuckDBPool.initialize()` is never called (no `get_pool()` is
exercised in the tests), so the 22 GB file is never opened.

Two test groups:

1. `/api/status` happy path — fake `DuckDBPool` returns a stub
   connection that responds to `execute("SELECT 1")`; we assert the
   response is exactly `{"ok": true, "endpoint_count": 18}`.
2. `_map_exception` envelope shape — every domain exception class is
   raised against a tiny isolated FastAPI app and the response is
   asserted to have the expected status + `{ detail: { code, message,
   detail } }` shape, including the uncaught-`Exception` catch-all.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from basketball_data_emporium.db.pool import DuckDBPool
from basketball_data_emporium.server import errors as err_mod


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _StubConn:
    """Minimal DuckDB connection stand-in for `/api/status` tests."""

    def execute(self, sql: str, params: Any = None):  # noqa: ARG002
        if "SELECT 1" in sql:
            return _StubResult([(1,)])

        class _Cur:
            def fetchone(inner_self) -> tuple[Any, ...] | None:  # noqa: N805
                return None

            def fetchall(inner_self) -> list[tuple[Any, ...]]:  # noqa: N805
                return []

        return _Cur()


class _StubPool(DuckDBPool):
    """DuckDBPool stand-in that hands out a single stub connection."""

    def __init__(self) -> None:
        self._conn = _StubConn()

    def acquire(self):
        return self._conn

    def release(self, conn) -> None:
        return None

    def initialize(self) -> None:
        return None

    def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# /api/status fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def status_client() -> TestClient:
    """A TestClient for the real `app`, with the pool stubbed in.

    We import the `app` symbol from `basketball_data_emporium.server.app` (NOT
    `from .server import app as app_module` — that would shadow the
    `app` attribute and make `app_module.app` invalid). We also
    pass `raise_server_exceptions=False` so the catch-all handler in
    the app can be exercised without the TestClient re-raising the
    server error in the test thread.
    """
    if "basketball_data_emporium.server.app" in sys.modules:
        importlib.reload(sys.modules["basketball_data_emporium.server.app"])

    from basketball_data_emporium.server.app import app as fastapi_app
    from basketball_data_emporium.server.deps import get_db_pool

    stub = _StubPool()
    fastapi_app.dependency_overrides.clear()
    fastapi_app.dependency_overrides[get_db_pool] = lambda: stub
    return TestClient(fastapi_app, raise_server_exceptions=False)


def test_status_happy_path(status_client: TestClient) -> None:
    response = status_client.get("/api/status")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["endpoint_count"] == 18
    assert body["data_state"] == "unverified"
    assert body["data_state_reason"] == "audit_missing"
    assert body["data_verified"] is False
    assert body["data_stale"] is True


def test_status_cors_preflight_allows_next_origin(status_client: TestClient) -> None:
    response = status_client.options(
        "/api/status",
        headers={
            "Origin": "http://127.0.0.1:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"


def test_rate_limit_jail_envelope(
    status_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from basketball_data_emporium.server import rate_limit

    monkeypatch.setenv("BASKETBALL_DATA_RATE_LIMIT_PER_MINUTE", "1")
    rate_limit._hits.clear()

    assert status_client.get("/api/status").status_code == 200
    response = status_client.get("/api/status")
    assert response.status_code == 429
    body = response.json()
    assert body["detail"]["code"] == "rate_limit_jailed"
    assert body["detail"]["detail"]["retry_after"] >= 1


def test_status_openapi_lists_route(status_client: TestClient) -> None:
    response = status_client.get("/openapi.json")
    assert response.status_code == 200
    body = response.json()
    assert "/api/status" in body["paths"]
    assert "get" in body["paths"]["/api/status"]
    assert "StatusResponse" in body["components"]["schemas"]
    schema = body["components"]["schemas"]["StatusResponse"]
    assert schema["properties"]["ok"]["type"] == "boolean"
    assert schema["properties"]["endpoint_count"]["type"] == "integer"
    assert schema["properties"]["data_state"]["enum"] == [
        "passed",
        "failed",
        "stale",
        "unverified",
    ]
    assert schema["properties"]["data_state_reason"]["enum"] == [
        "audit_missing",
        "latest_pipeline_failed",
        "latest_dq_failed",
        "audit_stale",
        "dq_missing",
        "verified",
        "unverified",
    ]


def test_module_exposes_app_and_main() -> None:
    from basketball_data_emporium.server.app import (
        app as fastapi_app,
        main,
        _map_exception,
    )

    # `app` is the FastAPI singleton; `main` is the CLI entry point;
    # `_map_exception` is the named envelope mapper that the frontend
    # references in `frontend/src/lib/api-errors.ts:5`.
    assert isinstance(fastapi_app, FastAPI)
    assert callable(main)
    assert callable(_map_exception)


# ---------------------------------------------------------------------------
# _map_exception envelope (isolated apps — no DB, no routes other than probes)
# ---------------------------------------------------------------------------


def _make_isolated_client() -> tuple[TestClient, FastAPI]:
    """Build a tiny FastAPI app wired up with `_map_exception`.

    This avoids depending on the real `/api/status` route and lets each
    test raise an arbitrary exception against an arbitrary URL. We
    pass `raise_server_exceptions=False` so the catch-all handler can
    convert uncaught `Exception`s into the `internal_error` envelope
    instead of re-raising them in the test thread.
    """
    from basketball_data_emporium.server.app import (
        _map_exception,
        _map_exception_unhandled,
    )

    isolated = FastAPI()
    isolated.add_exception_handler(err_mod.BasketballDataEmporiumError, _map_exception)
    isolated.add_exception_handler(Exception, _map_exception_unhandled)
    return TestClient(isolated, raise_server_exceptions=False), isolated


def _add_raising_route(app: FastAPI, path: str, exc: Exception) -> None:
    async def _endpoint() -> None:
        raise exc

    app.add_api_route(path, _endpoint, methods=["GET"])


EXCEPTION_CASES: list[tuple[err_mod.BasketballDataEmporiumError, int, str]] = [
    (err_mod.InvalidSearchError("bad term"), 400, "invalid_search"),
    (err_mod.BadRequestError("nope"), 400, "bad_request"),
    (
        err_mod.InvalidPlayerError("missing", detail={"identifier": "x"}),
        404,
        "invalid_player",
    ),
    (
        err_mod.InvalidTeamError("missing", detail={"identifier": "BOS"}),
        404,
        "invalid_team",
    ),
    (
        err_mod.InvalidSeasonError("missing", detail={"season": 1900}),
        404,
        "invalid_season",
    ),
    (
        err_mod.RateLimitJailedError("slow down", retry_after=3),
        429,
        "rate_limit_jailed",
    ),
    (err_mod.SchemaDriftError("table missing"), 500, "schema_drift"),
    (err_mod.InternalError("boom"), 500, "internal_error"),
]


@pytest.mark.parametrize(("exc", "status_code", "code"), EXCEPTION_CASES)
def test_map_exception_envelope(
    exc: err_mod.BasketballDataEmporiumError,
    status_code: int,
    code: str,
) -> None:
    client, app = _make_isolated_client()
    _add_raising_route(app, f"/probe/{code}", exc)
    response = client.get(f"/probe/{code}")
    assert response.status_code == status_code
    body = response.json()
    assert "detail" in body, body
    inner = body["detail"]
    assert inner["code"] == code
    assert isinstance(inner["message"], str) and inner["message"]
    if inner.get("detail") is not None:
        assert isinstance(inner["detail"], dict)


def test_map_exception_rate_limit_carries_retry_after() -> None:
    client, app = _make_isolated_client()
    _add_raising_route(
        app,
        "/probe/rate",
        err_mod.RateLimitJailedError("jailed", retry_after=7),
    )
    response = client.get("/probe/rate")
    assert response.status_code == 429
    body = response.json()
    assert body["detail"]["code"] == "rate_limit_jailed"
    assert body["detail"]["detail"] == {"retry_after": 7}


def test_catch_all_handles_uncaught_exception() -> None:
    client, app = _make_isolated_client()

    async def _boom() -> None:
        raise RuntimeError("kaboom")

    app.add_api_route("/probe/uncaught", _boom, methods=["GET"])
    response = client.get("/probe/uncaught")
    assert response.status_code == 500
    body = response.json()
    assert body["detail"]["code"] == "internal_error"
    assert body["detail"]["message"] == "Internal server error"
