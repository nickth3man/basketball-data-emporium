"""Tests for the three-layer governed SQL gate."""

from __future__ import annotations

import pytest

from chat_server.db import get_db
from chat_server.semantic_catalog import load_catalog
from chat_server.sqlgate import (
    build_live_schema,
    validate_governed_sql,
    validate_select_sql,
)
from chat_tests.conftest import skip_no_db


def test_layer_one_rejects_writes_and_unapproved_tables() -> None:
    assert not validate_select_sql("DROP TABLE dim_player", {"dim_player"}).valid
    assert not validate_select_sql("SELECT * FROM src_secret", {"dim_player"}).valid


@skip_no_db
@pytest.mark.asyncio
async def test_live_schema_accepts_approved_fallback_table() -> None:
    report = await validate_governed_sql(
        "SELECT * FROM dim_player LIMIT 1",
        get_db(),
        load_catalog(),
    )
    assert report.valid, report.errors


@skip_no_db
@pytest.mark.asyncio
async def test_catalog_fan_trap_is_rejected_after_live_validation() -> None:
    report = await validate_governed_sql(
        "SELECT pc.player_id, SUM(ps.total_pts) FROM mart_player_career pc "
        "JOIN mart_player_season ps ON pc.player_id = ps.player_id",
        get_db(),
        load_catalog(),
    )
    assert not report.valid
    assert any("fan trap" in error for error in report.errors)


@skip_no_db
@pytest.mark.asyncio
async def test_allowlisted_source_table_passes_live_schema() -> None:
    """Allowlisted source table appears in live schema.

    ``src_fact_bref_team_season_summary`` is in ALLOWED_TABLES_FOR_AGENT
    so it must appear in the live schema after the union fix.
    """
    schema = await build_live_schema(get_db())
    assert "src_fact_bref_team_season_summary" in schema, (
        "allowlisted source table missing from live schema"
    )


@skip_no_db
@pytest.mark.asyncio
async def test_unlisted_source_table_is_rejected() -> None:
    """Unlisted source table must NOT appear in live schema.

    A real ``src_*`` warehouse table NOT in ALLOWED_TABLES_FOR_AGENT
    must NOT pass the live-schema gate. ``src_fact_player_matchups`` is
    a confirmed warehouse table that is NOT in the allowlist.
    """
    schema = await build_live_schema(get_db())
    assert "src_fact_player_matchups" not in schema, "unlisted source table leaked into live schema"


@skip_no_db
@pytest.mark.asyncio
async def test_allowlisted_source_passes_gate() -> None:
    """End-to-end ``validate_governed_sql`` must accept an explicitly
    allowlisted src_* table query."""
    db = get_db()
    catalog = load_catalog()
    report = await validate_governed_sql(
        "SELECT team_id, season, w, l FROM src_fact_bref_team_season_summary LIMIT 5",
        db,
        catalog,
    )
    assert report.valid, report.errors


@skip_no_db
@pytest.mark.asyncio
async def test_unlisted_source_fails_gate() -> None:
    """End-to-end ``validate_governed_sql`` must reject a query on a
    real warehouse src_* table NOT in ALLOWED_TABLES_FOR_AGENT."""
    db = get_db()
    catalog = load_catalog()
    report = await validate_governed_sql(
        "SELECT * FROM src_fact_player_matchups LIMIT 5",
        db,
        catalog,
    )
    assert not report.valid
    assert any("not allowed" in e.lower() for e in report.errors), report.errors
