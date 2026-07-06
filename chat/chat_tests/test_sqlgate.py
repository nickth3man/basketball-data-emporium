"""Tests for `chat_server.sqlgate` (governed-SQL validation gate).

The gate is layered on top of `chat_server.validation.validate_template_sql`
and adds two further checks: the sqlglot optimizer semantic pass and the
catalog-driven fan / chasm-trap detector. These tests exercise each
layer end-to-end against the real semantic catalog; no live warehouse is
required (none of the assertions touch DuckDB data, only schema metadata
sourced from the catalog YAMLs and the parser/optimizer).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from chat_server.semantic_catalog import load_catalog, reset_catalog_cache
from chat_server.sqlgate import build_catalog_schema, validate_governed_sql


@pytest.fixture(autouse=True)
def _clear_catalog_cache() -> Iterator[None]:
    """Reset the module-level catalog cache around every test.

    Mirrors the pattern in `chat_tests/test_semantic_catalog.py`. Each
    test gets a fresh catalog so any future test that mutates the cache
    doesn't bleed into the next.
    """
    reset_catalog_cache()
    yield
    reset_catalog_cache()


# ---------------------------------------------------------------------------
# Inherited allowlist (validation.py) -- smoke tests confirming the layering
# ---------------------------------------------------------------------------


def test_valid_select_over_catalog_table_passes() -> None:
    """A simple catalog-anchored SELECT passes every layer.

    `mart_player_career` is the base_table of the `player_career` model,
    so it's in the allowlist AND in the optimizer schema. `player_id` is
    a real column on that table, so the optimizer accepts it. No joins,
    so the fan-trap detector has nothing to check.
    """
    catalog = load_catalog()
    r = validate_governed_sql("SELECT player_id FROM mart_player_career LIMIT 5", catalog)
    assert r.valid is True, f"expected valid; got errors: {r.errors}"
    assert r.errors == []
    assert r.tables_referenced == {"mart_player_career"}


def test_unknown_table_fails_inherited_allowlist() -> None:
    """A phantom table reference is rejected by the inherited allowlist.

    `some_phantom_table` is not in any catalog model's `base_table.name`,
    so the allowlist derived from the catalog doesn't contain it. The
    inherited `validate_template_sql` gate fires before the optimizer
    pass runs (we never get to the catalog-schema check).
    """
    catalog = load_catalog()
    r = validate_governed_sql("SELECT * FROM some_phantom_table", catalog)
    assert r.valid is False
    assert "some_phantom_table" in r.tables_referenced
    assert any("not in the template's allowed set" in e for e in r.errors), r.errors


# ---------------------------------------------------------------------------
# Optimizer semantic pass
# ---------------------------------------------------------------------------


def test_optimizer_catches_typo_column() -> None:
    """A reference to a column not in the catalog-derived schema is rejected.

    `nonexistent_col` is not declared on any dimension or measure in the
    catalog, so it doesn't appear in the schema dict passed to
    `sqlglot.optimizer.optimize`. The optimizer's qualify sub-pass raises
    `OptimizeError` (caught as the umbrella `SqlglotError`) and the
    gate appends a human-readable error.

    NOTE: this assertion pins sqlglot's qualify behavior -- it does
    catch unknown columns against a typed schema. If a future sqlglot
    release softens this (e.g. issues a warning rather than raising),
    the gate will need a fallback. See the module docstring on
    sqlgate.py for the version-tolerance notes.
    """
    catalog = load_catalog()
    r = validate_governed_sql("SELECT nonexistent_col FROM mart_player_career", catalog)
    assert r.valid is False
    # The optimizer rejection surfaces as a single error whose message
    # mentions both the optimizer and the offending column.
    assert any("optimizer" in e and "nonexistent_col" in e for e in r.errors), r.errors


# ---------------------------------------------------------------------------
# Fan / chasm-trap detection
# ---------------------------------------------------------------------------


def test_fan_chasm_trap_detected() -> None:
    """A SUM over a one_to_many join without a collapsing GROUP BY trips.

    `player_career.season_breakdown` is declared `one_to_many` against
    `player_season`: one career row fans out to many per-season rows.
    The test query `SUM(ps.total_pts)` aggregates a sum-additive measure
    from the fanned side (player_season) without a GROUP BY, so the
    fan-trap detector must flag it.

    We deliberately avoid grouping by the join key (ps.player_id) so the
    collapse check fails -- this is the precise pattern that produced the
    exhibition phantom person id inflation documented in
    `meta_known_gap.bbr_duplicate_identity_phantom_ids`.
    """
    catalog = load_catalog()
    sql = (
        "SELECT pc.player_id, SUM(ps.total_pts) "
        "FROM mart_player_career pc "
        "JOIN mart_player_season ps ON pc.player_id = ps.player_id"
    )
    r = validate_governed_sql(sql, catalog)
    assert r.valid is False
    assert any("fan trap" in e and "season_breakdown" in e for e in r.errors), r.errors


def test_fan_chasm_trap_not_tripped_when_group_by_collapses_fan() -> None:
    """Sanity: GROUP BY the join key collapses the fan -> no fan-trap error.

    This is the inverse of `test_fan_chasm_trap_detected`: the same
    one_to_many join + SUM pattern, but with `GROUP BY ps.player_id` --
    the literal `right_on` from the catalog -- so the fan collapses and
    the detector must not flag the query.
    """
    catalog = load_catalog()
    sql = (
        "SELECT ps.player_id, SUM(ps.total_pts) "
        "FROM mart_player_career pc "
        "JOIN mart_player_season ps ON pc.player_id = ps.player_id "
        "GROUP BY ps.player_id"
    )
    r = validate_governed_sql(sql, catalog)
    assert r.valid is True, f"expected valid; got errors: {r.errors}"


def test_fan_chasm_trap_skipped_for_many_to_one_join() -> None:
    """The detector only flags one_to_many, never many_to_one.

    `player_season.career_rollup` is `many_to_one` against
    `player_career`. SUMming a player_season additive measure without a
    GROUP BY while joining player_career is structurally safe -- the
    player_season rows are the many side and the career row is a lookup
    (1:1 within the join), so SUM without GROUP BY still produces one
    row per player_season row, and the SUM over the whole result is
    well-defined. The detector must skip this case.
    """
    catalog = load_catalog()
    sql = (
        "SELECT pc.player_id, SUM(ps.total_pts) "
        "FROM mart_player_season ps "
        "JOIN mart_player_career pc ON ps.player_id = pc.player_id"
    )
    r = validate_governed_sql(sql, catalog)
    assert r.valid is True, f"expected valid; got errors: {r.errors}"


# ---------------------------------------------------------------------------
# build_catalog_schema shape
# ---------------------------------------------------------------------------


def test_build_catalog_schema_returns_table_column_map() -> None:
    """`build_catalog_schema` returns the documented `{table: {col: type}}` shape.

    Asserts the outer key is the base_table.name of the `player_career`
    model (`mart_player_career`) and the inner dict is non-empty -- the
    optimizer needs at least the column KEYS to perform its qualify pass.
    """
    catalog = load_catalog()
    schema = build_catalog_schema(catalog)

    assert isinstance(schema, dict)
    assert "mart_player_career" in schema, (
        f"expected mart_player_career as a key in the optimizer schema; got keys: {sorted(schema)}"
    )
    assert isinstance(schema["mart_player_career"], dict)
    assert schema["mart_player_career"], (
        "expected a non-empty column dict for mart_player_career; "
        "the optimizer needs at least the column keys to qualify references"
    )
    # Spot-check a known column: `player_id` is the player_career
    # model's primary key and must appear in the schema's column set.
    assert "player_id" in schema["mart_player_career"]
