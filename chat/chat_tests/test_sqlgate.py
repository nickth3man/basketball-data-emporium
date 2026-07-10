"""Tests for the three-layer governed SQL gate."""

from __future__ import annotations

import pytest

from chat_server.db import get_db
from chat_server.semantic_catalog import load_catalog
from chat_server.sqlgate import validate_governed_sql, validate_select_sql
from chat_tests.conftest import skip_no_db


def test_layer_one_rejects_writes_and_unapproved_tables() -> None:
    assert not validate_select_sql("DROP TABLE dim_player", {"dim_player"}).valid
    assert not validate_select_sql("SELECT * FROM src_secret", {"dim_player"}).valid


@skip_no_db
@pytest.mark.asyncio
async def test_live_schema_accepts_approved_fallback_table() -> None:
    report = await validate_governed_sql(
        "SELECT * FROM dim_player LIMIT 1", get_db(), load_catalog()
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
