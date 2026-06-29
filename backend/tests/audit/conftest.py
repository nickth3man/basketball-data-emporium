"""Shared fixtures for the audit-read tests.

The audit tables are read-only queryable artifacts of the ETL
pipeline. They live in the `audit.*` schema in the same DuckDB
file. We open the file once per test session (opening the 22 GB
file is expensive) and share the connection across the small
number of tests in `test_can_read_pipeline_log.py`.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest


def _resolve_db_path() -> Path:
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
        pytest.skip(f"DuckDB file not found at {path}; set DUCKDB_PATH to enable audit tests.")
    con = duckdb.connect(str(path), read_only=True)
    try:
        yield con
    finally:
        con.close()
