"""Tests for the execute watchdog + memory_limit (ARCHITECTURE.md §9 step 2).

The gate validates correctness, not cost: an expensive-but-valid query
passes every validation layer, so the bound is a runtime mechanism —
``memory_limit`` at connection open plus a wall-clock watchdog that
interrupts the executing cursor. These tests pin that contract:

* a query past its ``timeout_seconds`` budget raises
  :class:`QueryTimeoutError` promptly;
* the singleton survives the interrupt (next query succeeds);
* ``dry_run`` is exempt (EXPLAIN of the same slow query is cheap);
* the ``memory_limit`` constructor arg reaches the engine;
* ``timeout_seconds=None`` (the default) leaves queries unbounded.

None of this needs the warehouse — a throwaway DuckDB file plus
``range()`` cross-joins produce arbitrarily slow queries with no data.
"""

from __future__ import annotations

import time

import duckdb
import pytest

from chat_server.db import DuckDBSingleton, QueryTimeoutError

# Cross join is ~5e10 tuples pre-aggregation: far beyond what any test
# machine finishes in the 0.2s budget, but instant to abort via interrupt.
_SLOW_SQL = "SELECT count(*) AS n FROM range(50000000) a, range(1000) b"


@pytest.fixture()
def tmp_db(tmp_path):
    """A DuckDBSingleton over a fresh throwaway database file."""
    path = str(tmp_path / "watchdog.duckdb")
    duckdb.connect(path).close()  # create the file so read_only=True can open it
    db = DuckDBSingleton(path)
    yield db
    db.close()


async def test_timeout_raises_and_connection_survives(tmp_db):
    t0 = time.perf_counter()
    with pytest.raises(QueryTimeoutError) as excinfo:
        await tmp_db.execute(_SLOW_SQL, timeout_seconds=0.2)
    elapsed = time.perf_counter() - t0

    assert excinfo.value.sql == _SLOW_SQL
    assert excinfo.value.timeout_seconds == pytest.approx(0.2)
    # Generous bound: the point is "seconds, not the full cross-join".
    assert elapsed < 10.0, f"interrupt took {elapsed:.1f}s — watchdog not firing?"

    # The interrupt must not poison the connection for the next query.
    result = await tmp_db.execute("SELECT 1 AS one")
    assert result.rows == [{"one": 1}]


async def test_no_timeout_by_default(tmp_db):
    """Without timeout_seconds the watchdog never arms."""
    result = await tmp_db.execute("SELECT count(*) AS n FROM range(1000)")
    assert result.rows[0]["n"] == 1000


async def test_fast_query_within_budget_unaffected(tmp_db):
    result = await tmp_db.execute("SELECT 42 AS v", timeout_seconds=30.0)
    assert result.rows == [{"v": 42}]


async def test_dry_run_exempt_from_watchdog(tmp_db):
    """EXPLAIN of the slow query is planner-only work — no timeout applies."""
    assert await tmp_db.dry_run(_SLOW_SQL) is None


async def test_memory_limit_applied(tmp_path):
    path = str(tmp_path / "memlimit.duckdb")
    duckdb.connect(path).close()

    default_db = DuckDBSingleton(path)
    default_value = (await default_db.execute("SELECT current_setting('memory_limit') AS v")).rows[
        0
    ]["v"]
    default_db.close()

    limited_db = DuckDBSingleton(path, memory_limit="1GB")
    try:
        limited_value = (
            await limited_db.execute("SELECT current_setting('memory_limit') AS v")
        ).rows[0]["v"]
    finally:
        limited_db.close()

    assert limited_value != default_value, "memory_limit SET did not reach the engine"
    assert "GiB" in limited_value or "MiB" in limited_value
