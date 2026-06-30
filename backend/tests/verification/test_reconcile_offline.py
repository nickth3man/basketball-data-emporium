"""Offline unit tests for the Layer 5 cross-source reconciliation scaffold.

These tests are pure-python: no DuckDB, no network, no ``nba_api`` import.
They drive ``reconcile.reconcile()`` with a fake ``fetcher`` (a lambda
returning planted rows) and assert that:

* normalization helpers behave on edge cases,
* the column maps are well-formed and complete enough for the §8.2
  matrix,
* ``reconcile()`` flags a planted discrepancy and stays silent on a
  clean match,
* ``get_official_fetcher()`` raises a clear, actionable error when the
  optional ``[reconcile]`` extra is not installed.

See ``ideas/data-verification-methodology.md`` §8 for the full rationale.
"""

from __future__ import annotations

import pytest

from basketball_data_emporium.verification import reconcile_official as ro
from basketball_data_emporium.verification.reconcile_official import (
    BOX_SCORE_COLUMN_MAP,
    ENDPOINT_MAP,
    IDENTITY_COLUMN_MAP,
    PLAYER_COLUMN_MAP,
    SEASON_TOTALS_COLUMN_MAP,
    get_official_fetcher,
    minutes_to_decimal,
    normalize_season,
    reconcile,
)


# ---------------------------------------------------------------------------
# minutes_to_decimal
# ---------------------------------------------------------------------------

class TestMinutesToDecimal:
    """§8.3 — nba_api returns "MM:SS"; warehouse stores decimal minutes."""

    def test_standard_mmss(self) -> None:
        assert minutes_to_decimal("12:30") == pytest.approx(12.5)

    def test_single_digit_minute_mmss(self) -> None:
        # The task explicitly calls out "M:SS" as a format to handle.
        assert minutes_to_decimal("9:30") == pytest.approx(9.5)

    def test_bare_minute_count(self) -> None:
        # The task explicitly calls out "MM" (no colon) as a format to handle.
        assert minutes_to_decimal("12") == 12.0

    def test_zero(self) -> None:
        assert minutes_to_decimal("0:00") == 0.0

    def test_dnp_empty_string(self) -> None:
        # DNP rows commonly surface as empty strings.
        assert minutes_to_decimal("") == 0.0

    def test_dnp_none(self) -> None:
        assert minutes_to_decimal(None) == 0.0

    def test_numeric_passthrough(self) -> None:
        # Already a number? Return as-is.
        assert minutes_to_decimal(12.5) == 12.5
        assert minutes_to_decimal(12) == 12.0

    def test_whitespace_stripped(self) -> None:
        assert minutes_to_decimal("  12:30  ") == pytest.approx(12.5)

    def test_fractional_seconds(self) -> None:
        # nba_api occasionally emits sub-second precision.
        assert minutes_to_decimal("12:30.5") == pytest.approx(12.5083333, rel=1e-6)

    def test_invalid_string_raises(self) -> None:
        with pytest.raises(ValueError, match="minutes_to_decimal"):
            minutes_to_decimal("not-a-time")

    def test_invalid_mmss_raises(self) -> None:
        with pytest.raises(ValueError, match="MM:SS"):
            minutes_to_decimal("12:abc")


# ---------------------------------------------------------------------------
# normalize_season — bidirectional
# ---------------------------------------------------------------------------

class TestNormalizeSeason:
    """§8.3 — nba_api uses 'YYYY-YY'; warehouse mixes both encodings."""

    def test_hyphenated_to_end_year(self) -> None:
        assert normalize_season("2023-24") == 2024

    def test_hyphenated_to_end_year_int(self) -> None:
        assert normalize_season("1979-80") == 1980

    def test_end_year_passthrough_int(self) -> None:
        assert normalize_season(2024) == 2024

    def test_end_year_passthrough_str(self) -> None:
        assert normalize_season("2024") == 2024

    def test_end_year_to_hyphenated(self) -> None:
        assert normalize_season(2024, to="hyphenated") == "2023-24"

    def test_end_year_to_hyphenated_str_input(self) -> None:
        assert normalize_season("1980", to="hyphenated") == "1979-80"

    def test_y2k_boundary(self) -> None:
        # 1999 -> "1998-99"
        assert normalize_season(1999, to="hyphenated") == "1998-99"
        # 2000 -> "1999-00"
        assert normalize_season(2000, to="hyphenated") == "1999-00"

    def test_round_trip(self) -> None:
        end = 2024
        hyp = normalize_season(end, to="hyphenated")
        assert normalize_season(hyp) == end

    def test_round_trip_hyphenated_first(self) -> None:
        hyp = "2019-20"
        end = normalize_season(hyp)
        assert normalize_season(end, to="hyphenated") == hyp

    def test_unknown_target_raises(self) -> None:
        with pytest.raises(ValueError, match="`to`"):
            normalize_season(2024, to="banana")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Column maps (§8.2 / §8.3 — column case & name normalization)
# ---------------------------------------------------------------------------

class TestColumnMaps:
    """The §8.2 diff matrix needs every key field to be mappable."""

    def test_endpoint_map_has_all_required_objects(self) -> None:
        # The task spec lists exactly these nine warehouse objects.
        expected = {
            "dim_player",
            "dim_game",
            "fact_player_game_boxscore",
            "fact_player_season_stats",
            "fact_team_game_boxscore",
            "v_team_standings",
            "v_franchise_leaders",
            "fact_pbp_event",
            "shots",
        }
        assert set(ENDPOINT_MAP) == expected

    def test_endpoint_map_endpoints_are_strings(self) -> None:
        for obj, spec in ENDPOINT_MAP.items():
            assert isinstance(spec["endpoint"], str) and spec["endpoint"], obj
            assert isinstance(spec["grain"], str) and spec["grain"], obj
            assert isinstance(spec["key_fields"], list) and spec["key_fields"], obj

    def test_endpoint_map_mentions_nba_api_endpoint_names(self) -> None:
        # Spot-check the system-of-record mappings called out in the task spec.
        assert "CommonAllPlayers" in ENDPOINT_MAP["dim_player"]["endpoint"]
        assert "CommonPlayerInfo" in ENDPOINT_MAP["dim_player"]["endpoint"]
        assert ENDPOINT_MAP["dim_game"]["endpoint"] == "LeagueGameLog"
        assert ENDPOINT_MAP["fact_player_game_boxscore"]["endpoint"] == "BoxScoreTraditionalV2"
        assert ENDPOINT_MAP["fact_player_season_stats"]["endpoint"] == "LeagueDashPlayerStats"
        assert ENDPOINT_MAP["v_team_standings"]["endpoint"] == "LeagueStandingsV3"
        assert ENDPOINT_MAP["v_franchise_leaders"]["endpoint"] == "FranchiseLeaders"
        assert ENDPOINT_MAP["fact_pbp_event"]["endpoint"] == "PlayByPlayV2"
        assert ENDPOINT_MAP["shots"]["endpoint"] == "ShotChartDetail"

    def test_box_score_column_map_keys_present(self) -> None:
        # The §8.2 row calls out at least these diff keys for player-game box.
        for upper in (
            "MIN", "PTS", "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA",
            "OREB", "DREB", "REB", "AST", "STL", "BLK", "TOV", "PF",
        ):
            assert upper in BOX_SCORE_COLUMN_MAP, upper
            assert BOX_SCORE_COLUMN_MAP[upper] == upper.lower()

    def test_season_totals_column_map_keys_present(self) -> None:
        for upper in ("GP", "GS", "MIN", "PTS", "REB", "AST", "STL", "BLK"):
            assert upper in SEASON_TOTALS_COLUMN_MAP, upper
            assert SEASON_TOTALS_COLUMN_MAP[upper] == upper.lower()

    def test_box_score_keys_are_all_uppercase(self) -> None:
        for k in BOX_SCORE_COLUMN_MAP:
            assert k == k.upper(), f"key {k!r} is not UPPERCASE"

    def test_season_totals_keys_are_all_uppercase(self) -> None:
        for k in SEASON_TOTALS_COLUMN_MAP:
            assert k == k.upper(), f"key {k!r} is not UPPERCASE"

    def test_no_overlap_collision(self) -> None:
        # Every (uppercase) key maps to a unique lowercase target.
        for mapping in (BOX_SCORE_COLUMN_MAP, SEASON_TOTALS_COLUMN_MAP):
            targets = list(mapping.values())
            assert len(targets) == len(set(targets)), mapping

    def test_identity_column_map_present(self) -> None:
        # Issue #9 — identity keys must be reverse-mapped so the
        # reconciliation key (``player_id`` / ``team_id``) can be
        # written in warehouse naming and still match the official
        # row's UPPERCASE key (``PERSON_ID`` / ``TEAM_ID``).
        for upper, lower in (
            ("PERSON_ID", "player_id"),
            ("TEAM_ID", "team_id"),
            ("GAME_ID", "game_id"),
        ):
            assert IDENTITY_COLUMN_MAP[upper] == lower

    def test_player_column_map_contains_full_name_synonyms(self) -> None:
        # nba_api exposes the player name as both
        # DISPLAY_FIRST_LAST and DISPLAY_LAST_COMMA_FIRST; the reverse
        # map should let ``reconcile(..., fields=['full_name'])``
        # find either one.
        assert PLAYER_COLUMN_MAP["DISPLAY_FIRST_LAST"] == "full_name"
        assert PLAYER_COLUMN_MAP["DISPLAY_LAST_COMMA_FIRST"] == "full_name"
        assert PLAYER_COLUMN_MAP["ROSTERSTATUS"] == "is_active"
        assert PLAYER_COLUMN_MAP["FROM_YEAR"] == "from_year"
        assert PLAYER_COLUMN_MAP["TO_YEAR"] == "to_year"


# ---------------------------------------------------------------------------
# reconcile() — the row-diff function (the core of §8.3)
# ---------------------------------------------------------------------------

class TestReconcile:
    """reconcile() diffs official (fetcher) vs warehouse (expected_rows)."""

    # --- The two scenarios the task explicitly mandates ---

    def test_flags_planted_discrepancy(self) -> None:
        """A planted diff between official and warehouse MUST be flagged."""
        official = [
            # LeBron-like: 30 / 7 / 4
            {"PERSON_ID": 2544, "PTS": 30, "REB": 7, "AST": 4},
            # Curry-like: 28 / 6 / 6 (warehouse says 99 — planted discrepancy)
            {"PERSON_ID": 201939, "PTS": 28, "REB": 6, "AST": 6},
        ]
        warehouse = [
            {"person_id": 2544, "pts": 30, "reb": 7, "ast": 4},
            {"person_id": 201939, "pts": 99, "reb": 6, "ast": 6},
        ]
        discrepancies = reconcile(
            fetcher=lambda: official,
            expected_rows=warehouse,
            fields=["person_id", "pts", "reb", "ast"],
        )

        # Exactly one diff, and it's on the planted field.
        assert len(discrepancies) == 1
        d = discrepancies[0]
        assert d["entity"] == "201939"
        assert d["field"] == "pts"
        assert d["expected"] == 28
        assert d["actual"] == 99
        assert d["severity"] in {"low", "medium", "high"}

    def test_no_flag_when_matching(self) -> None:
        """An exact match MUST produce zero discrepancies."""
        official = [
            {"PERSON_ID": 2544, "PTS": 30, "REB": 7, "AST": 4},
            {"PERSON_ID": 201939, "PTS": 28, "REB": 6, "AST": 6},
        ]
        warehouse = [
            {"person_id": 2544, "pts": 30, "reb": 7, "ast": 4},
            {"person_id": 201939, "pts": 28, "reb": 6, "ast": 6},
        ]
        discrepancies = reconcile(
            fetcher=lambda: official,
            expected_rows=warehouse,
            fields=["person_id", "pts", "reb", "ast"],
        )
        assert discrepancies == []

    # --- Additional scenarios that exercise the surrounding contract ---

    def test_discrepancy_record_shape(self) -> None:
        """Records are dicts with the §8.3 audit.metric_discrepancy shape."""
        official = [{"PERSON_ID": 1, "PTS": 10}]
        warehouse = [{"person_id": 1, "pts": 12}]
        discrepancies = reconcile(
            fetcher=lambda: official,
            expected_rows=warehouse,
            fields=["person_id", "pts"],
        )
        assert len(discrepancies) == 1
        d = discrepancies[0]
        # EXACTLY the audit.metric_discrepancy shape — no extra/missing keys.
        assert set(d.keys()) == {"entity", "field", "expected", "actual", "severity"}
        assert d["entity"] == "1"
        assert d["field"] == "pts"
        assert d["expected"] == 10
        assert d["actual"] == 12

    def test_tolerance_absorbs_percentage_noise(self) -> None:
        """§8.3: percentages get a ±0.001 tolerance; rounding noise is fine."""
        official = [{"PERSON_ID": 1, "FG_PCT": 0.456}]
        warehouse = [{"person_id": 1, "fg_pct": 0.4563}]  # within 0.001
        discrepancies = reconcile(
            fetcher=lambda: official,
            expected_rows=warehouse,
            fields=["person_id", "fg_pct"],
            tolerances={"fg_pct": 0.001},
        )
        assert discrepancies == []

    def test_tolerance_outside_band_is_flagged(self) -> None:
        """A percentage diff outside the tolerance IS flagged."""
        official = [{"PERSON_ID": 1, "FG_PCT": 0.456}]
        warehouse = [{"person_id": 1, "fg_pct": 0.500}]  # diff = 0.044
        discrepancies = reconcile(
            fetcher=lambda: official,
            expected_rows=warehouse,
            fields=["person_id", "fg_pct"],
            tolerances={"fg_pct": 0.001},
        )
        assert len(discrepancies) == 1
        d = discrepancies[0]
        assert d["field"] == "fg_pct"
        assert d["severity"] == "high"  # 0.044 > 2 * 0.001

    def test_missing_warehouse_row_is_flagged(self) -> None:
        """A key present only in the official source is a discrepancy."""
        official = [{"PERSON_ID": 1}, {"PERSON_ID": 2}]
        warehouse = [{"person_id": 1}]
        discrepancies = reconcile(
            fetcher=lambda: official,
            expected_rows=warehouse,
            fields=["person_id"],
        )
        assert any(
            d["field"] == "person_id" and d["expected"] == 2 and d["actual"] is None
            for d in discrepancies
        )

    def test_missing_official_row_is_flagged(self) -> None:
        """A key present only in the warehouse is a discrepancy."""
        official = [{"PERSON_ID": 1}]
        warehouse = [{"person_id": 1}, {"person_id": 2}]
        discrepancies = reconcile(
            fetcher=lambda: official,
            expected_rows=warehouse,
            fields=["person_id"],
        )
        assert any(
            d["field"] == "person_id" and d["expected"] is None and d["actual"] == 2
            for d in discrepancies
        )

    def test_explicit_key_param(self) -> None:
        """An explicit ``key`` overrides the default first-field behavior."""
        # The first field in `fields` is `ast` but the actual row key is
        # `person_id`. The function must honor the explicit override.
        official = [{"PERSON_ID": 2544, "AST": 4}, {"PERSON_ID": 201939, "AST": 6}]
        warehouse = [
            {"person_id": 2544, "ast": 4},
            {"person_id": 201939, "ast": 99},  # planted
        ]
        discrepancies = reconcile(
            fetcher=lambda: official,
            expected_rows=warehouse,
            fields=["ast", "person_id"],
            key="person_id",
        )
        assert len(discrepancies) == 1
        assert discrepancies[0]["entity"] == "201939"
        assert discrepancies[0]["field"] == "ast"

    def test_default_key_is_first_field(self) -> None:
        """When ``key`` is omitted, the first entry of ``fields`` is used."""
        official = [{"PERSON_ID": 2544, "PTS": 30}]
        warehouse = [{"person_id": 2544, "pts": 30}]
        # `person_id` is first -> must work without an explicit `key`.
        discrepancies = reconcile(
            fetcher=lambda: official,
            expected_rows=warehouse,
            fields=["person_id", "pts"],
        )
        assert discrepancies == []

    def test_empty_fields_raises(self) -> None:
        with pytest.raises(ValueError, match="`fields`"):
            reconcile(fetcher=lambda: [], expected_rows=[], fields=[])

    def test_both_sides_empty(self) -> None:
        """No rows on either side -> no discrepancies."""
        assert reconcile(
            fetcher=lambda: [], expected_rows=[], fields=["pts"]
        ) == []

    def test_minutes_round_trip_after_normalization(self) -> None:
        """End-to-end check: official MM:SS normalized -> warehouse decimal minutes.

        The pipeline (§8.3) is responsible for converting ``"MM:SS"`` to
        decimal *before* rows reach ``reconcile()``; this test exercises
        that handoff.
        """
        official = [{"PERSON_ID": 1, "MIN": minutes_to_decimal("12:30")}]  # 12.5
        warehouse = [{"person_id": 1, "min": 12.5}]
        discrepancies = reconcile(
            fetcher=lambda: official,
            expected_rows=warehouse,
            fields=["person_id", "min"],
        )
        assert discrepancies == []

    def test_minutes_discrepancy_caught(self) -> None:
        official = [{"PERSON_ID": 1, "MIN": minutes_to_decimal("12:30")}]  # 12.5
        warehouse = [{"person_id": 1, "min": 13.0}]
        discrepancies = reconcile(
            fetcher=lambda: official,
            expected_rows=warehouse,
            fields=["person_id", "min"],
        )
        assert len(discrepancies) == 1
        assert discrepancies[0]["field"] == "min"
        assert discrepancies[0]["expected"] == 12.5
        assert discrepancies[0]["actual"] == 13.0

    def test_severity_buckets(self) -> None:
        """Sanity-check the high/medium/low bucketing at tolerance=1.

        Bucketing model (tolerance=1):
          * diff 0.5  -> within tolerance, NOT flagged
          * diff 1.5  -> "low"   (just over the tolerance)
          * diff 3.0  -> "medium" (> 2x tolerance)
          * diff 10.0 -> "high"   (> 5x tolerance)
        """
        official = [
            {"PERSON_ID": 1, "PTS": 10},
            {"PERSON_ID": 2, "PTS": 10},
            {"PERSON_ID": 3, "PTS": 10},
            {"PERSON_ID": 4, "PTS": 10},
        ]
        warehouse = [
            {"person_id": 1, "pts": 10.5},   # diff 0.5  -> not flagged
            {"person_id": 2, "pts": 11.5},   # diff 1.5  -> low
            {"person_id": 3, "pts": 13.0},   # diff 3.0  -> medium
            {"person_id": 4, "pts": 20.0},   # diff 10.0 -> high
        ]
        discrepancies = reconcile(
            fetcher=lambda: official,
            expected_rows=warehouse,
            fields=["person_id", "pts"],
            tolerances={"pts": 1.0},
        )
        by_entity = {d["entity"]: d for d in discrepancies}
        # Entity 1 is within tolerance, so no discrepancy at all.
        assert "1" not in by_entity
        assert by_entity["2"]["severity"] == "low"
        assert by_entity["3"]["severity"] == "medium"
        assert by_entity["4"]["severity"] == "high"


# ---------------------------------------------------------------------------
# get_official_fetcher — optional-dependency error path
# ---------------------------------------------------------------------------

class TestGetOfficialFetcherMissingExtra:
    """When ``nba_api`` is not installed, the factory must fail clearly."""

    def test_raises_runtime_error_when_nba_api_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force the import to fail regardless of whether nba_api happens
        # to be installed in the local venv.
        import builtins

        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "nba_api" or name.startswith("nba_api."):
                raise ImportError(f"simulated missing optional dep: {name}")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        with pytest.raises(RuntimeError) as excinfo:
            get_official_fetcher()

        msg = str(excinfo.value)
        # The message must be actionable — name the install command.
        assert "nba_api" in msg
        assert "reconcile" in msg
        assert "pip install" in msg or "uv add" in msg


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------

def test_module_exports_expected_symbols() -> None:
    """Public surface is exactly what the task spec promises."""
    for name in (
        "ENDPOINT_MAP",
        "BOX_SCORE_COLUMN_MAP",
        "SEASON_TOTALS_COLUMN_MAP",
        "IDENTITY_COLUMN_MAP",
        "PLAYER_COLUMN_MAP",
        "minutes_to_decimal",
        "normalize_season",
        "reconcile",
        "get_official_fetcher",
        "OfficialFetcher",
        "FakeFetcher",
        "main",
    ):
        assert hasattr(ro, name), f"missing public symbol: {name}"
