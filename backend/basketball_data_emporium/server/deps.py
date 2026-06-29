"""FastAPI dependencies.

The `get_db()` dependency yields a single read-only DuckDB connection
from the process-wide pool, returning it to the pool when the request
finishes. Routes depend on this whenever they need to issue a query.
"""

from __future__ import annotations

from collections.abc import Iterator

import duckdb
from fastapi import Depends

from basketball_data_emporium.db.pool import DuckDBPool, get_pool


def get_db_pool() -> DuckDBPool:
    """Return the process-wide `DuckDBPool`.

    Exposed as a separate dependency so test fixtures can override it
    via `app.dependency_overrides[get_db_pool] = ...`.
    """
    return get_pool()


def get_db(
    pool: DuckDBPool = Depends(get_db_pool),
) -> Iterator[duckdb.DuckDBPyConnection]:
    """Yield a read-only DuckDB connection from the pool."""
    conn = pool.acquire()
    try:
        yield conn
    finally:
        pool.release(conn)
