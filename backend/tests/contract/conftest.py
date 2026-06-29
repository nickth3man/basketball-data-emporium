"""Shared fixtures for the contract tests.

The contract tests live alongside the existing `tests/test_status.py`
but exercise a different facet of the same surface: full HTTP
contract (status, headers, content-type, exact body shape) using
FastAPI's in-process `TestClient`.

If the real DuckDB file is available we exercise the live pool;
otherwise we fall back to the same stub pool pattern used in
`tests/test_status.py`. The contract is the same in both cases
(we never let the underlying data leak into the assertion), so
CI without the 22 GB file still gets a meaningful check.
"""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Stub pool (CI without DuckDB)
# ---------------------------------------------------------------------------


class _StubResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _StubConn:
    def execute(self, sql: str, params: Any = None):  # noqa: ARG002
        if "SELECT 1" in sql:
            return _StubResult([(1,)])

        class _Cur:
            def fetchone(inner_self):  # noqa: N805
                return None

            def fetchall(inner_self):  # noqa: N805
                return []

        return _Cur()


def _has_duckdb_file() -> bool:
    raw = os.environ.get("DUCKDB_PATH", "../data/nba.duckdb")
    path = Path(raw)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path.exists()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def contract_client() -> Iterator[TestClient]:
    """Yield a TestClient for the live `app` with the DB dep wired up.

    Resolution order:
    1. If the real DuckDB file is reachable on disk, use the
       production pool (the sidecar's own singleton). This catches
       "the sidecar can't talk to the DB" regressions.
    2. Otherwise, install a stub pool so the contract check still
       runs in CI without the 22 GB file.

    `raise_server_exceptions=False` so the catch-all handler can
    convert uncaught `Exception`s into the `internal_error` envelope
    instead of re-raising them in the test thread.
    """
    if "basketball_data_emporium.server.app" in sys.modules:
        importlib.reload(sys.modules["basketball_data_emporium.server.app"])

    from basketball_data_emporium.server.app import app as fastapi_app
    from basketball_data_emporium.server.deps import get_db_pool

    fastapi_app.dependency_overrides.clear()

    if _has_duckdb_file():
        # Let the production pool initialize on first request.
        fastapi_app.dependency_overrides[get_db_pool] = get_db_pool
    else:
        from basketball_data_emporium.db.pool import DuckDBPool

        class _StubPool(DuckDBPool):
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

        fastapi_app.dependency_overrides[get_db_pool] = lambda: _StubPool()

    with TestClient(fastapi_app, raise_server_exceptions=False) as client:
        yield client

    fastapi_app.dependency_overrides.clear()
