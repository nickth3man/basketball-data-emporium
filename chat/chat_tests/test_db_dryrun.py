"""Tests for ``DuckDBSingleton.dry_run`` (Stage 3.4).

The dry-run is the planner-side counterpart to ``execute``: it wraps
SQL as ``EXPLAIN <sql>`` and runs it through the same lock + cursor
acquisition path so a planner-time failure (unknown column, bad
identifier, type mismatch) raises :class:`DryRunError` BEFORE we ever
allocate the row-fetch path that ``execute`` takes.

The three tests pin the contract:

* ``test_dry_run_valid_sql_returns_none`` -- a catalog-valid SELECT
  dry-runs cleanly; returns ``None``.
* ``test_dry_run_invalid_sql_raises_dryrunerror`` -- a SQL string with
  a parse-time error (``SELECT FROM``) raises :class:`DryRunError`
  carrying both the failing SQL and the original ``duckdb.Error``.
* ``test_dry_run_unknown_column_raises_dryrunerror`` -- a SQL string
  that parses but references an unknown column raises
  :class:`DryRunError` at planner / binder time (the same class of
  bug the repair loop is meant to recover from).

All tests are gated behind ``skip_no_db`` -- the warehouse is required
to resolve table/column bindings against the live schema.
"""

from __future__ import annotations

import pytest

from chat_server.db import DryRunError
from chat_tests.conftest import skip_no_db


@skip_no_db
async def test_dry_run_valid_sql_returns_none(db):
    """``dry_run`` against a catalog-valid SELECT returns ``None``.

    Uses a real catalog table (``mart_player_career``) so the planner
    has to bind a real column (``player_id``). The point is to
    exercise the lock + cursor + ``EXPLAIN`` path on a happy payload;
    we don't care about the EXPLAIN result row(s).
    """
    sql = "SELECT player_id FROM mart_player_career LIMIT 5"
    result = await db.dry_run(sql)
    assert result is None


@skip_no_db
async def test_dry_run_invalid_sql_raises_dryrunerror(db):
    """``dry_run`` against a parse-broken SQL raises :class:`DryRunError`.

    We use a deliberately malformed statement (``SELECT FROM`` -- no
    column list, no table) that the DuckDB planner rejects with a
    parser error. The exception must carry BOTH:

    * ``sql`` -- the failing SQL string (for the repair loop's
      refiner message).
    * ``original`` -- the underlying ``duckdb.Error`` (for the
      pipeline's structured logging and the composer error note).
    """
    bad_sql = "SELECT FROM"
    with pytest.raises(DryRunError) as excinfo:
        await db.dry_run(bad_sql)

    err = excinfo.value
    assert err.sql == bad_sql, f"expected original sql on the exception, got {err.sql!r}"
    assert err.original is not None
    # The wrapped duckdb.Error should produce a non-empty str -- the
    # repair loop passes str(err.original) into the refiner message.
    assert str(err.original).strip(), "duckdb.Error had an empty message"


@skip_no_db
async def test_dry_run_unknown_column_raises_dryrunerror(db):
    """A SQL statement that binds cleanly but references an unknown column
    raises ``DryRunError`` at planner time.

    Pins the planner-vs-execution distinction: this SELECT parses fine
    (column + table tokens are well-formed), so the parse-time path
    can't catch it. DuckDB's binder / optimizer flags the unknown
    column during the EXPLAIN walk -- exactly the class of bug the
    repair loop is meant to recover from.
    """
    sql = "SELECT nonexistent_column FROM mart_player_career LIMIT 1"
    with pytest.raises(DryRunError) as excinfo:
        await db.dry_run(sql)
    assert excinfo.value.sql == sql
    assert excinfo.value.original is not None
    assert str(excinfo.value.original).strip()
