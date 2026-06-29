"""Shared fixtures for the data-invariant suite.

These tests open the read-only DuckDB snapshot directly (not through the FastAPI
pool — a pure data check should not depend on the HTTP stack) and run read-only
queries only. The connection is session-scoped because opening the 22 GB file is
expensive.

Mirrors the skip-when-absent convention in ``tests/schema/conftest.py`` so the
suite stays green on CI runners that do not carry the snapshot.

Shared helpers are exposed as fixtures so every test module gets them with no
import gymnastics:

    def test_x(count):           # count(sql) -> int
        assert count("SELECT count(*) FROM ... WHERE <bad>") == 0

    def test_y(db):              # db is a duckdb connection
        ...

Constants and the divergence registry live in ``known_divergences.py`` (import it
directly: ``import known_divergences as kd``).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest


def _resolve_db_path() -> Path:
    """Resolve the DuckDB path, honoring DUCKDB_PATH / BASKETBALL_DATA_DB_PATH."""
    raw = (
        os.environ.get("BASKETBALL_DATA_DB_PATH")
        or os.environ.get("DUCKDB_PATH")
        or "../data/nba.duckdb"
    )
    path = Path(raw)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


@pytest.fixture(scope="session")
def db() -> Iterator[duckdb.DuckDBPyConnection]:
    """Session-scoped read-only DuckDB connection (skips when the file is absent)."""
    path = _resolve_db_path()
    if not path.exists():
        pytest.skip(
            f"DuckDB file not found at {path}; "
            "set DUCKDB_PATH (or BASKETBALL_DATA_DB_PATH) to enable invariant tests."
        )
    conn = duckdb.connect(str(path), read_only=True)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def count(db: duckdb.DuckDBPyConnection) -> Callable[[str], int]:
    """Return ``fn(sql) -> int`` that runs a scalar-count query against the snapshot."""

    def _count(sql: str) -> int:
        row = db.execute(sql).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    return _count


@pytest.fixture()
def rows(db: duckdb.DuckDBPyConnection) -> Callable[[str], list[tuple[Any, ...]]]:
    """Return ``fn(sql) -> list[tuple]`` for inspecting a handful of offending rows."""

    def _rows(sql: str) -> list[tuple[Any, ...]]:
        return db.execute(sql).fetchall()

    return _rows
