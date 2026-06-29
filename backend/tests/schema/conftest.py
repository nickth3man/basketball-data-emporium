"""Shared fixtures for the schema-introspection tests.

The MVCS schema tests open the read-only DuckDB file directly (not
through the FastAPI pool — that would couple a pure DB check to the
HTTP stack). The connection is reused across the whole test session
because opening the 22 GB file is expensive; tests run read-only
queries only.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest


def _resolve_db_path() -> Path:
    """Resolve the DuckDB file path.

    Honors the `DUCKDB_PATH` env var (matching the sidecar's own
    convention in `basketball_data_emporium.db.pool`) and falls back to the
    default `../data/nba.duckdb` relative to the `backend/` CWD.
    """
    raw = os.environ.get("DUCKDB_PATH", "../data/nba.duckdb")
    path = Path(raw)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


@pytest.fixture(scope="session")
def duckdb_conn() -> Iterator[duckdb.DuckDBPyConnection]:
    """Yield a read-only DuckDB connection for the duration of the test session."""
    path = _resolve_db_path()
    if not path.exists():
        pytest.skip(
            f"DuckDB file not found at {path}; set DUCKDB_PATH to enable schema tests."
        )
    con = duckdb.connect(str(path), read_only=True)
    try:
        yield con
    finally:
        con.close()
