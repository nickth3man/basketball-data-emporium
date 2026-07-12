"""Unit tests for `chat_server.sqlgate.validate_select_sql`.

No DB connection required. Covers every branch of `validate_select_sql`:

* SELECT on allowlisted tables (with/without JOIN, with/without CTE)
* DDL/DML forbidden (INSERT/UPDATE/DELETE/CREATE/DROP/ALTER)
* Catch-all forbidden (PRAGMA/ATTACH/COPY/CALL/LOAD/INSTALL/EXPORT/VACUUM/CHECKPOINT)
* Multi-statement rejection
* Parse errors
* Table-not-in-allowlist rejection
"""

from __future__ import annotations

import pytest

from chat_server.sqlgate import ValidationReport, validate_select_sql

# The shared allowlist used by the "happy path" cases below. Templates
# against the production warehouse get to read from this set; anything
# else triggers a rejection.
ALLOWED = {"mart_player_season", "dim_player", "fact_game_result"}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_tableless_select_rejected() -> None:
    """SELECT without a FROM clause referencing a warehouse table
    must now be rejected by the empty-tableset gate."""
    r = validate_select_sql("SELECT 1 AS x", ALLOWED)
    assert r.valid is False
    assert any("must reference" in e.lower() for e in r.errors), r.errors
    assert r.tables_referenced == set()


def test_select_from_allowed_table_is_valid() -> None:
    r = validate_select_sql("SELECT * FROM mart_player_season", ALLOWED)
    assert r.valid is True
    assert r.tables_referenced == {"mart_player_season"}


def test_select_join_two_allowed_tables_is_valid() -> None:
    sql = (
        "SELECT p.full_name, ps.season_year "
        "FROM mart_player_season ps JOIN dim_player p USING (player_id)"
    )
    r = validate_select_sql(sql, ALLOWED)
    assert r.valid is True
    assert r.tables_referenced == {"mart_player_season", "dim_player"}


def test_select_with_cte_referencing_allowed_table_is_valid() -> None:
    sql = (
        "WITH p AS ("
        "  SELECT player_id, full_name FROM dim_player WHERE full_name LIKE 'Stephen Curry'"
        ") "
        "SELECT p.full_name, ps.season_year "
        "FROM p JOIN mart_player_season ps USING (player_id) "
        "ORDER BY ps.season_year DESC LIMIT 5"
    )
    r = validate_select_sql(sql, ALLOWED)
    assert r.valid is True
    assert r.tables_referenced == {"mart_player_season", "dim_player"}
    # CTE aliases must NOT be reported as referenced tables.
    assert "p" not in r.tables_referenced


def test_select_with_multiple_ctes_is_valid() -> None:
    sql = (
        "WITH a AS (SELECT * FROM mart_player_season), "
        "     b AS (SELECT * FROM dim_player) "
        "SELECT a.player_id, b.full_name FROM a JOIN b USING (player_id)"
    )
    r = validate_select_sql(sql, ALLOWED)
    assert r.valid is True
    assert r.tables_referenced == {"mart_player_season", "dim_player"}
    assert r.tables_referenced.isdisjoint({"a", "b"})


def test_empty_allowlist_with_no_tables_rejected() -> None:
    """`SELECT 1` without a warehouse table reference is rejected
    even with an empty allowlist (empty-tableset gate fires first)."""
    r = validate_select_sql("SELECT 1 + 2", set())
    assert r.valid is False
    assert any("must reference" in e.lower() for e in r.errors), r.errors


# ---------------------------------------------------------------------------
# Forbidden DDL / DML
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO mart_player_season VALUES (1)",
        "UPDATE mart_player_season SET x = 1",
        "DELETE FROM mart_player_season",
        "CREATE TABLE foo (a INT)",
        "DROP TABLE mart_player_season",
        "ALTER TABLE mart_player_season ADD COLUMN y INT",
    ],
)
def test_ddl_dml_rejected(sql: str) -> None:
    r = validate_select_sql(sql, ALLOWED)
    assert r.valid is False
    assert any("only SELECT" in e for e in r.errors), r.errors


# ---------------------------------------------------------------------------
# Forbidden session / utility statements (the catch-all set)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "ATTACH 'foo.db' AS x",
        "ATTACH DATABASE 'foo.db' AS x",
        "PRAGMA enable_progress_bar",
        "VACUUM",
        "LOAD my_extension",
        "INSTALL my_extension",
        "EXPORT DATABASE 'foo'",
        "CHECKPOINT",
    ],
)
def test_session_and_utility_statements_rejected(sql: str) -> None:
    r = validate_select_sql(sql, ALLOWED)
    assert r.valid is False, f"expected {sql!r} to be rejected"


# ---------------------------------------------------------------------------
# Multi-statement rejection
# ---------------------------------------------------------------------------


def test_multi_statement_rejected_select_then_drop() -> None:
    sql = "SELECT 1; DROP TABLE mart_player_season"
    r = validate_select_sql(sql, ALLOWED)
    assert r.valid is False
    assert any("exactly 1" in e for e in r.errors), r.errors


def test_multi_statement_rejected_two_selects() -> None:
    # Two benign SELECTs are still a multi-statement payload — reject.
    sql = "SELECT 1; SELECT 2"
    r = validate_select_sql(sql, ALLOWED)
    assert r.valid is False
    assert any("exactly 1" in e for e in r.errors), r.errors


# ---------------------------------------------------------------------------
# Allowlist enforcement
# ---------------------------------------------------------------------------


def test_table_not_in_allowlist_rejected() -> None:
    r = validate_select_sql("SELECT * FROM not_allowed_table", ALLOWED)
    assert r.valid is False
    assert "not_allowed_table" in r.tables_referenced
    assert any("not allowed by the approved warehouse set" in e for e in r.errors), r.errors


def test_join_with_one_bad_table_rejected() -> None:
    sql = "SELECT * FROM mart_player_season ps JOIN some_other_table ot USING (player_id)"
    r = validate_select_sql(sql, ALLOWED)
    assert r.valid is False
    assert r.tables_referenced == {"mart_player_season", "some_other_table"}


# ---------------------------------------------------------------------------
# Parse errors
# ---------------------------------------------------------------------------


def test_parse_error_rejected() -> None:
    r = validate_select_sql("SELCT 1 FROM x", ALLOWED)
    assert r.valid is False
    assert any("parse error" in e.lower() for e in r.errors), r.errors


def test_parse_error_truncated_query_rejected() -> None:
    r = validate_select_sql("SELECT * FROM", ALLOWED)
    assert r.valid is False


# ---------------------------------------------------------------------------
# Empty-tableset gate: tableless SELECT and comment-truncated patterns
# ---------------------------------------------------------------------------


def test_tableless_select_via_comment_truncated_rejected() -> None:
    """A SELECT whose FROM clause is commented out by a ``--`` line
    comment must be rejected by the empty-tableset gate.

    This exercises the conv_026 same-line comment-truncated pattern:
    the ``--`` on the line before the table reference causes sqlglot to
    parse only ``SELECT *`` with no FROM clause, producing an empty
    tables set.
    """
    r = validate_select_sql(
        "SELECT *\n-- FROM dim_player\nWHERE 1 = 1",
        ALLOWED,
    )
    assert r.valid is False
    assert any("must reference" in e.lower() for e in r.errors), r.errors


def test_cte_over_dim_player_still_passes() -> None:
    """A CTE referencing a real warehouse table must still pass the
    empty-tableset gate because the CTE's body references dim_player."""
    sql = "WITH p AS (SELECT player_id, full_name FROM dim_player) SELECT * FROM p"
    r = validate_select_sql(sql, ALLOWED)
    assert r.valid is True
    assert r.tables_referenced == {"dim_player"}


def test_tableless_select_via_block_comment_rejected() -> None:
    """A SELECT whose only table reference is inside a ``/* */`` block
    comment must be rejected."""
    r = validate_select_sql(
        "SELECT *\n/* FROM dim_player */\nWHERE 1 = 1",
        ALLOWED,
    )
    assert r.valid is False
    assert any("must reference" in e.lower() for e in r.errors), r.errors


# ---------------------------------------------------------------------------
# API shape sanity
# ---------------------------------------------------------------------------


def test_validation_report_shape() -> None:
    """`ValidationReport` exposes the three documented fields with the
    documented types, regardless of the verdict."""
    r = validate_select_sql("SELECT 1", ALLOWED)
    assert isinstance(r, ValidationReport)
    assert isinstance(r.valid, bool)
    assert isinstance(r.errors, list)
    assert isinstance(r.tables_referenced, set)
    # SELECT 1 is rejected by the empty-tableset gate; errors is non-empty.
    assert r.valid is False
    assert len(r.errors) >= 1
    assert r.tables_referenced == set()
