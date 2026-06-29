"""MVCS Test 1 — Schema introspection.

Catches "sidecar wired to a DB that doesn't match assumptions."

This is a pure DuckDB check (no HTTP, no FastAPI). It opens the
read-only DuckDB file and asserts that the tables + columns the
Phase 2 endpoints will depend on are present. If any of these
fail, the sidecar will be unable to serve the catalog/players/teams
endpoints in a later phase.

Spec-facing compatibility contract (from the MVCS brief):
* player identity projection              — bref_player_id, display_name, is_active
* `unified_star.fact_player_season_stats` — player_id, season_year, is_playoffs, pts
* team identity projection                — team_id, full_name

The live DB can still drift internally. Endpoint code reads
`basketball_data_emporium.db.schema_compat`, a read-only SQL projection
that maps those physical columns to stable spec-facing names.
"""

from __future__ import annotations

import duckdb
import pytest

from basketball_data_emporium.db.schema_compat import (
    DIM_PLAYER_COMPAT_SQL,
    DIM_TEAM_COMPAT_SQL,
)


# ---------------------------------------------------------------------------
# Expected column sets
# ---------------------------------------------------------------------------

# Spec-named columns per the MVCS brief. Endpoint SQL should rely on
# the compatibility projections for these names, not on physical table
# column names.
SPEC_DIM_PLAYER = {"bref_player_id", "display_name", "is_active"}
SPEC_FACT_PLAYER_SEASON_STATS = {"player_id", "season_year", "is_playoffs", "pts"}
SPEC_DIM_TEAM = {"team_id", "full_name"}

# Actual columns observed in the live DB on 2026-06-29.
ACTUAL_DIM_PLAYER = {"bref_player_id", "full_name", "is_active"}
ACTUAL_FACT_PLAYER_SEASON_STATS = {"player_id", "season_year", "is_playoffs", "pts"}
ACTUAL_DIM_TEAM = {"team_id", "team_abbrev"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _table_exists(con: duckdb.DuckDBPyConnection, schema: str, table: str) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = ? AND table_name = ?
        LIMIT 1
        """,
        [schema, table],
    ).fetchone()
    return row is not None


def _column_names(con: duckdb.DuckDBPyConnection, fqn: str) -> set[str]:
    rows = con.execute(f"PRAGMA table_info('{fqn}')").fetchall()
    return {r[1] for r in rows}


def _query_column_names(con: duckdb.DuckDBPyConnection, source_sql: str) -> set[str]:
    cursor = con.execute(f"SELECT * FROM {source_sql} LIMIT 0")
    return {column[0] for column in cursor.description or []}


# ---------------------------------------------------------------------------
# Table presence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("schema", "table"),
    [
        ("unified_star", "dim_player"),
        ("unified_star", "fact_player_season_stats"),
        ("unified_star", "dim_team"),
    ],
)
def test_required_table_exists(
    duckdb_conn: duckdb.DuckDBPyConnection,
    schema: str,
    table: str,
) -> None:
    """Each table the planned endpoints depend on must be present."""
    assert _table_exists(duckdb_conn, schema, table), (
        f"Missing required table '{schema}.{table}'; "
        f"sidecar endpoints that read this table will fail at runtime."
    )


# ---------------------------------------------------------------------------
# Column presence — spec-facing compatibility contract
# ---------------------------------------------------------------------------


def test_dim_player_compat_has_spec_named_columns(
    duckdb_conn: duckdb.DuckDBPyConnection,
) -> None:
    """The player compatibility layer exposes `bref_player_id`, `display_name`, `is_active`."""
    cols = _query_column_names(duckdb_conn, DIM_PLAYER_COMPAT_SQL)
    missing = SPEC_DIM_PLAYER - cols
    assert not missing, (
        f"player compatibility projection missing spec columns: {sorted(missing)}"
    )


def test_dim_team_compat_has_spec_named_columns(
    duckdb_conn: duckdb.DuckDBPyConnection,
) -> None:
    """The team compatibility layer exposes `team_id`, `full_name`."""
    cols = _query_column_names(duckdb_conn, DIM_TEAM_COMPAT_SQL)
    missing = SPEC_DIM_TEAM - cols
    assert not missing, (
        f"team compatibility projection missing spec columns: {sorted(missing)}"
    )


def test_fact_player_season_stats_has_spec_named_columns(
    duckdb_conn: duckdb.DuckDBPyConnection,
) -> None:
    """`fact_player_season_stats` exposes `player_id`, `season_year`, `is_playoffs`, `pts`."""
    cols = _column_names(duckdb_conn, "unified_star.fact_player_season_stats")
    missing = SPEC_FACT_PLAYER_SEASON_STATS - cols
    assert not missing, (
        f"unified_star.fact_player_season_stats missing spec columns: {sorted(missing)} "
        f"(have: {sorted(cols)})"
    )


# ---------------------------------------------------------------------------
# Column presence — actual columns (today's ground truth)
#
# These mirror the spec-named assertions but use the column names the
# DB *actually* ships. They are the tests that production code will
# rely on, and they let Phase 2 land without a flag-day migration.
# ---------------------------------------------------------------------------


def test_dim_player_has_actual_columns(duckdb_conn: duckdb.DuckDBPyConnection) -> None:
    """`dim_player` exposes the columns live endpoints will need: bref_player_id, full_name, is_active."""
    cols = _column_names(duckdb_conn, "unified_star.dim_player")
    missing = ACTUAL_DIM_PLAYER - cols
    assert not missing, (
        f"unified_star.dim_player missing live columns: {sorted(missing)}"
    )


def test_dim_team_has_actual_columns(duckdb_conn: duckdb.DuckDBPyConnection) -> None:
    """`dim_team` exposes the columns live endpoints will need: team_id, team_abbrev."""
    cols = _column_names(duckdb_conn, "unified_star.dim_team")
    missing = ACTUAL_DIM_TEAM - cols
    assert not missing, f"unified_star.dim_team missing live columns: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Smoke row counts (so a `CREATE TABLE foo (...);` typo is caught)
# ---------------------------------------------------------------------------


def test_dim_player_has_rows(duckdb_conn: duckdb.DuckDBPyConnection) -> None:
    """`dim_player` is populated, not just present.

    The MVCS brief targets ~6,984 rows (the count we observed in
    Phase 1). The assertion is `> 1000` to avoid drift sensitivity
    while still catching "the table was truncated" regressions.
    """
    n = duckdb_conn.execute("SELECT count(*) FROM unified_star.dim_player").fetchone()[
        0
    ]
    assert n > 1000, (
        f"unified_star.dim_player has only {n} rows; expected at least 1000."
    )


def test_fact_player_season_stats_has_rows(
    duckdb_conn: duckdb.DuckDBPyConnection,
) -> None:
    """`fact_player_season_stats` is populated."""
    n = duckdb_conn.execute(
        "SELECT count(*) FROM unified_star.fact_player_season_stats"
    ).fetchone()[0]
    assert n > 1000, f"unified_star.fact_player_season_stats has only {n} rows."


def test_dim_team_has_rows(duckdb_conn: duckdb.DuckDBPyConnection) -> None:
    """`dim_team` is populated."""
    n = duckdb_conn.execute("SELECT count(*) FROM unified_star.dim_team").fetchone()[0]
    assert n > 10, f"unified_star.dim_team has only {n} rows."
