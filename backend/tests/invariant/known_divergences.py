"""Registry of known data divergences and genuine-residual baselines.

This module is the single source of truth that lets the invariant suite tell the
difference between:

1. **Era artifacts** — systematic "not recorded, stored as 0/sentinel" patterns
   that are *explained* by NBA stat-tracking history. The suite asserts these are
   fully explained (e.g. every ``fgm > fga`` row in the raw game fact has
   ``fga = 0`` and predates 1983), NOT that they are zero.
2. **Genuine residual** — real corruption with no era explanation. The suite
   asserts the count does not exceed a pinned baseline (a regression guard). The
   *target* is 0, reachable only by an upstream ETL fix; until then the baseline
   keeps CI green while making any new regression fail loudly.
3. **Clean invariants** — checks that already pass exactly (assert == 0).

All counts were measured against ``data/nba.duckdb`` on 2026-06-29. See
``ideas/data-verification-methodology.md`` for the full rationale.

Pure-python only (no DuckDB import) so it is trivially importable by every test
module and by the cross-source reconciliation scaffold.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# NBA stat availability cutoffs (Basketball-Reference glossary).
# Keyed by stat; value is the first **season ending year** the stat exists.
# A value present before its cutoff is an era artifact (should be NULL, not 0).
# ---------------------------------------------------------------------------
AVAILABLE_SINCE_END_YEAR: dict[str, int] = {
    "TRB": 1951,   # total rebounds since 1950-51
    "MP": 1952,    # minutes since 1951-52
    "ORB": 1974,   # offensive rebounds since 1973-74
    "DRB": 1974,   # defensive rebounds since 1973-74
    "STL": 1974,   # steals since 1973-74
    "BLK": 1974,   # blocks since 1973-74
    "TOV": 1978,   # turnovers since 1977-78
    "FG3": 1980,   # three-pointers since 1979-80
    "GS": 1982,    # games started since 1982
}

# Sentinel used in fact_player_season_stats for "team unresolved / combined"
# (the server-side analogue of Basketball-Reference's TOT row). Not corruption;
# must be excluded from FK checks and franchise/team aggregations.
SENTINEL_TEAM_ID: int = 0

# Event-level play-by-play / shot data is reliable only from 1996-97 onward.
PBP_ERA_START_END_YEAR: int = 1997

# Box-score era artifacts (FGA-as-0, ORB/DRB-split-as-0) are essentially gone by
# this ending year; after it, the corresponding checks must be clean.
MODERN_BOXSCORE_CLEAN_FROM_END_YEAR: int = 1983

# NBA game_id season-type digit (position 3 of the 10-digit id) -> label.
# Verified against unified_star.dim_game.season_type distinct values.
GAME_ID_SEASON_TYPE: dict[str, str] = {
    "1": "Preseason",
    "2": "Regular",
    "3": "All-Star",
    "4": "Playoffs",
    "5": "Play-In",
    "6": "Cup",  # NBA Cup / In-Season Tournament final, 2023-24+
}

# ---------------------------------------------------------------------------
# Genuine-residual baselines: measured count of real (non-era) violations.
# Tests assert observed <= baseline. Target is 0 (pending upstream ETL fix).
# Lowering a baseline here is how a fix is "ratcheted in".
# ---------------------------------------------------------------------------
GENUINE_RESIDUAL_BASELINE: dict[str, int] = {
    # unified_star.fact_player_game_boxscore (raw game level; feeds deferred views)
    "pgame_fgm_gt_fga_genuine": 25,        # fgm>fga with fga>0 (era artifact has fga=0)
    "pgame_ftm_gt_fta_genuine": 71,        # ftm>fta with fta>0
    "pgame_reb_split_genuine": 87,         # oreb+dreb<>reb not explained by oreb=dreb=0
    "pgame_pts_identity": 64,              # pts <> 2*fgm + fg3m + ftm
    "pgame_fg_pct_out_of_range": 25,       # fg_pct outside [0,1]
    "pgame_fg3m_gt_fgm": 1,
    "pgame_fg3a_gt_fga": 6,
    "pgame_min_negative": 12,
    # unified_star.fact_player_season_stats (Player Hub season source)
    "season_gs_gt_gp": 1,
    "season_ts_pct_out_of_range": 30,
    # referential integrity (the validate-staging-fk failure surface)
    "pgame_player_orphan": 189,
    "pgame_game_orphan": 4861,
    # game_id structural
    "gameid_embedded_season_mismatch": 33,
}


def season_end_year(season_year: object) -> int:
    """Normalize a season_year value to its **ending year** (int).

    Handles the snapshot's mixed encoding (ETL divergence #5):
      - "1979-80"  -> 1980   (hyphenated start-end form)
      - "2019-20"  -> 2020
      - 1947 / "1947" -> 1947 (already an ending-year integer)
    """
    s = str(season_year).strip()
    if "-" in s:
        return int(s[:4]) + 1
    return int(s)


def season_start_year(season_year: object) -> int:
    """Normalize a season_year value to its **starting year** (int).

    The NBA game_id embeds the starting year (e.g. 0021900001 -> 2019-20).
      - "1979-80" -> 1979
      - 1980 / "1980" -> 1979
    """
    s = str(season_year).strip()
    if "-" in s:
        return int(s[:4])
    return int(s) - 1


# SQL fragment that yields the ending year for a `season_year` column of either
# encoding. Use this in DuckDB queries instead of `left(season_year, 4)` (which
# returns the START year for the hyphenated form and is therefore inconsistent).
SEASON_END_YEAR_SQL = (
    "CASE WHEN strpos(CAST({col} AS VARCHAR), '-') > 0 "
    "THEN CAST(left(CAST({col} AS VARCHAR), 4) AS INT) + 1 "
    "ELSE CAST(CAST({col} AS VARCHAR) AS INT) END"
)
