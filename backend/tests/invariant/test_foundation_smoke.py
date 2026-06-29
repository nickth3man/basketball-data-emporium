"""Smoke test proving the invariant-suite contract works.

Verifies that ``known_divergences`` imports and the shared fixtures resolve. Real
layer modules follow the same shape.
"""

from __future__ import annotations

import known_divergences as kd


def test_registry_imports() -> None:
    assert kd.SENTINEL_TEAM_ID == 0
    assert kd.AVAILABLE_SINCE_END_YEAR["FG3"] == 1980
    assert kd.season_end_year("1979-80") == 1980
    assert kd.season_end_year("1947") == 1947
    assert kd.season_start_year("2019-20") == 2019


def test_db_fixture_and_count(count) -> None:
    # The canonical served season view passes shooting algebra exactly.
    assert count(
        'SELECT count(*) FROM api.v_canonical_player_season_totals WHERE "FG" > "FGA"'
    ) == 0
