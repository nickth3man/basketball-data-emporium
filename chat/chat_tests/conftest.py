"""Shared pytest fixtures for the chatbot test suite.

The DB-backed fixtures (`db`, `db_path`, `has_db`) gracefully skip when
the warehouse is absent. CI sets `CHAT_SKIP_DB_TESTS=1` to make this
explicit; locally, just having no `data/nba.duckdb` does the same.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest

from chat_server.config import get_settings


def _skip_flag_set() -> bool:
    """Return True when the env flag telling us to skip DB tests is set."""
    return os.environ.get("CHAT_SKIP_DB_TESTS", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _db_available() -> bool:
    """True when the warehouse file exists *and* the skip flag is unset.

    We import settings lazily because `get_settings()` raises if env vars
    are missing; in CI environments without the warehouse, those tests
    shouldn't even reach that point.
    """
    if _skip_flag_set():
        return False
    try:
        path = get_settings().duckdb_path
    except Exception:
        return False
    return bool(path) and os.path.exists(path)


#: Decorator for tests that need a live warehouse. Apply to individual
#: tests or whole classes — pytest will skip them in environments where
#: the warehouse is unavailable.
skip_no_db = pytest.mark.skipif(not _db_available(), reason="warehouse not available")


@pytest.fixture(scope="session")
def db_path() -> str:
    """The configured DuckDB path (resolved from settings)."""
    return get_settings().duckdb_path


@pytest.fixture(scope="session")
def has_db() -> bool:
    """True when the warehouse file is present and usable."""
    return _db_available()


@pytest.fixture
async def db() -> AsyncIterator:
    """Yield a `DuckDBSingleton` and close it after the test.

    Creates an isolated singleton per test so the reset doesn't bleed
    across tests. The underlying connection is closed in teardown.
    """
    from chat_server.db import DuckDBSingleton

    settings = get_settings()
    singleton = DuckDBSingleton(settings.duckdb_path)
    try:
        yield singleton
    finally:
        singleton.close()
