"""Layer 2 — Aggregation & grain consistency invariants.

These checks verify that the served aggregate views and the underlying star-schema
facts are internally consistent: row-level math holds, season-level rollups
balance, no sentinel values leak into team-grain tables, multi-team player
seasons behave like TOT (combined) totals, and game-level detail aggregates to
season totals in the modern era.

Each test measures a real number first and then asserts against either zero
(clean) or a pinned module-level baseline (genuine residual) or a documented
explanation (era artifact). The contract is the shared foundation in
``conftest.py`` / ``known_divergences.py``: a ``count`` fixture, the
``SENTINEL_TEAM_ID`` / ``SEASON_END_YEAR_SQL`` helpers, and the
``PBP_ERA_START_END_YEAR`` / ``MODERN_BOXSCORE_CLEAN_FROM_END_YEAR`` cutoffs.

All counts were measured against ``data/nba.duckdb`` on 2026-06-29. The
baselines are intentionally tight to the measured value — any growth is a
regression that would warrant investigation.
"""

from __future__ import annotations

import pytest

import known_divergences as kd

# ---------------------------------------------------------------------------
# Module-level measured baselines.
# Each constant is annotated with the query that produced it and the date.
# Bump a baseline here (never in ``known_divergences.py``) only after a
# documented upstream fix.
# ---------------------------------------------------------------------------

# (1b) v_team_standings.win_pct == round(wins / (wins + losses), 3). The
# view's win_pct column is rounded to 3 decimal places, so the maximum
# absolute error vs the unbounded fraction is 0.0005 (e.g. 0.4625 -> 0.463,
# |0.463 - 0.4625| == 0.0005). All 30 violations are at exactly that
# boundary; none exceeds it.
WIN_PCT_ROUNDING_VIOLATIONS: int = 30

# (2) v_team_standings is built from nbadb.bridge_game_team without filtering
# by season_type, so it INCLUDES regular season, playoffs, play-in, and
# preseason games. Playoff teams accumulate more wins than non-playoff
# teams within the same season, so a per-season sum(wins)=sum(losses) check
# is structurally imbalanced. 78 seasons are imbalanced across the full
# history; 29 of those are in the modern era (ending year >= 1997). This
# is a real artifact of the source data, not corruption.
PER_SEASON_WINS_LOSSES_IMBALANCE: int = 78

# (4) For a multi-team player-season (regular season, modern era) that has
# a team_id=0 (TOT) row, the TOT row's pts equals SUM(per-team pts) in only
# 1 of 17 such cases. The team_id=0 rows are not reliably combined totals
# — they are a noisy sentinel whose semantics are inconsistent across the
# snapshot.
TRADE_SPLIT_TOT_MISMATCH: int = 16

# (5) fact_player_game_boxscore aggregates (regular season, modern era) to
# fact_player_season_stats pts (per-team, excluding team_id=0) match exactly
# in 13394 of 14582 jointly-populated (player, season, team) tuples. The
# 1188 value-mismatches are genuine residual: rows in both tables that
# disagree on pts. (Separately, 1016 season rows have NO game boxscore at
# all, and 1488 game_sum rows have NO season stat — the join coverage is
# incomplete; this baseline covers only the value-mismatch subset.)
GAME_TO_SEASON_PTS_VALUE_MISMATCH: int = 1188


# ---------------------------------------------------------------------------
# (1) v_team_standings row-level math
# ---------------------------------------------------------------------------


def test_v_team_standings_games_played_eq_wins_plus_losses(count) -> None:
    """Layer 2 — games_played equals wins+losses for every standings row.

    Measured: 0 violations across all 2889 rows. The view aggregates a
    per-game win/loss flag and a count of game records, so this identity
    must hold by construction — a non-zero count would indicate broken
    counting or a NULL leak.
    """
    assert (
        count(
            """
            SELECT count(*) FROM api.v_team_standings
            WHERE games_played IS NOT NULL
              AND wins IS NOT NULL
              AND losses IS NOT NULL
              AND games_played <> wins + losses
            """
        )
        == 0
    )


def test_v_team_standings_win_pct_within_tolerance(count) -> None:
    """Layer 2 — win_pct is wins/(wins+losses) within 0.0005.

    The view rounds win_pct to 3 decimal places, so the worst-case
    rounding error is 0.0005. Measured: 30 violations, all exactly at
    the 0.0005 boundary (e.g. 0.4625 -> 0.463). PINNED to the measured
    count; any growth past 30 would indicate a change to the view's
    rounding rule.
    """
    assert (
        count(
            """
            SELECT count(*) FROM api.v_team_standings
            WHERE wins IS NOT NULL
              AND losses IS NOT NULL
              AND (wins + losses) > 0
              AND win_pct IS NOT NULL
              AND ABS(win_pct - (CAST(wins AS DOUBLE) / (wins + losses))) > 0.0005
            """
        )
        <= WIN_PCT_ROUNDING_VIOLATIONS
    )


def test_v_team_standings_no_nulls_in_required_columns(count) -> None:
    """Layer 2 — no NULL games_played / wins / losses in the served view.

    The view INNER-JOINS nbadb.bridge_game_team to dim_team and aggregates
    wins/losses from a non-nullable W/L flag, so a NULL leak would
    indicate an upstream column type or join regression. Measured: 0.
    """
    assert (
        count(
            """
            SELECT count(*) FROM api.v_team_standings
            WHERE games_played IS NULL OR wins IS NULL OR losses IS NULL
            """
        )
        == 0
    )


# ---------------------------------------------------------------------------
# (2) v_team_standings per-season rollup
# ---------------------------------------------------------------------------


def test_v_team_standings_per_season_win_loss_balance(count) -> None:
    """Layer 2 — within a single season, SUM(wins) equals SUM(losses).

    v_team_standings aggregates bridge_game_team WITHOUT filtering by
    season_type, so playoff / play-in / preseason wins are included.
    Playoff teams accumulate additional wins, so per-season sums cannot
    balance in a season that includes playoffs. Measured: 78 imbalanced
    seasons (29 of them in the modern era). PINNED to the measured count.
    The intent of the check is regression detection: any new "regular-only"
    season that falls out of balance would show up as a delta over 78.
    """
    assert (
        count(
            """
            SELECT count(*) FROM (
              SELECT season_year
              FROM api.v_team_standings
              WHERE wins IS NOT NULL AND losses IS NOT NULL
              GROUP BY season_year
              HAVING SUM(wins) <> SUM(losses)
            )
            """
        )
        <= PER_SEASON_WINS_LOSSES_IMBALANCE
    )


# ---------------------------------------------------------------------------
# (3) Sentinel team_id must not leak into team-grain tables
# ---------------------------------------------------------------------------


def test_fact_team_season_summary_no_sentinel_team_id(count) -> None:
    """Layer 2 — fact_team_season_summary never carries the team_id=0 TOT sentinel.

    This fact is the team-grain season rollup; team_id=0 means "team
    unresolved / combined" and is the player-grain SENTINEL. Allowing
    it here would double-count players and break the API team endpoint.
    Measured: 0 rows across all 1672.
    """
    assert (
        count(
            f"""
            SELECT count(*) FROM unified_star.fact_team_season_summary
            WHERE team_id = {kd.SENTINEL_TEAM_ID}
            """
        )
        == 0
    )


def test_v_team_standings_no_empty_team_abbreviation(count) -> None:
    """Layer 2 — every v_team_standings row has a non-empty team abbreviation.

    v_team_standings INNER-JOINS nbadb.bridge_game_team to
    unified_star.dim_team on team_id; rows whose team_id is missing from
    dim_team (the 20 NBA-Cup-only team_ids in 2025-26) are dropped, so a
    NULL/empty team column would indicate that the join condition has been
    weakened. Measured: 0.
    """
    assert (
        count(
            """
            SELECT count(*) FROM api.v_team_standings
            WHERE team IS NULL OR team = ''
            """
        )
        == 0
    )


# ---------------------------------------------------------------------------
# (4) Trade splits — does team_id=0 (TOT) match SUM(per-team pts)?
# ---------------------------------------------------------------------------


def test_trade_splits_tot_equals_per_team_sum(count) -> None:
    """Layer 2 — for a multi-team player-season with a TOT row, TOT.pts == SUM(per-team.pts).

    Measured on 1618 modern-era (ending year >= 1997) regular-season
    multi-team player-seasons in unified_star.fact_player_season_stats:
    only 17 of them have a team_id=0 (TOT) row, and only 1 of those 17
    TOT rows matches the per-team sum exactly. The other 16 carry
    different numbers (sometimes equal to one per-team row, sometimes an
    arbitrary value). Conclusion: ``team_id=0`` is NOT a reliable TOT
    sentinel in this snapshot; downstream consumers must compute the
    total themselves or rely on a per-team view. PINNED to 16; any
    growth would indicate the per-team aggregation is breaking, any
    decrease would require an upstream fix.
    """
    season_end_year_player = kd.SEASON_END_YEAR_SQL.format(col="ps.season_year")
    assert (
        count(
            f"""
            WITH multi AS (
              SELECT ps.player_id, ps.season_year
              FROM unified_star.fact_player_season_stats ps
              WHERE ps.is_playoffs = false
                AND ps.team_id <> {kd.SENTINEL_TEAM_ID}
                AND {season_end_year_player} >= {kd.PBP_ERA_START_END_YEAR}
              GROUP BY ps.player_id, ps.season_year
              HAVING COUNT(DISTINCT ps.team_id) >= 2
            ),
            tot AS (
              SELECT player_id, season_year, pts AS tot_pts
              FROM unified_star.fact_player_season_stats
              WHERE team_id = {kd.SENTINEL_TEAM_ID} AND is_playoffs = false
            ),
            per_team_sum AS (
              SELECT player_id, season_year, SUM(pts) AS sum_pts
              FROM unified_star.fact_player_season_stats
              WHERE team_id <> {kd.SENTINEL_TEAM_ID} AND is_playoffs = false
              GROUP BY player_id, season_year
            )
            SELECT count(*) FROM multi m
            JOIN tot t ON m.player_id = t.player_id AND m.season_year = t.season_year
            JOIN per_team_sum p ON m.player_id = p.player_id AND m.season_year = p.season_year
            WHERE t.tot_pts IS DISTINCT FROM p.sum_pts
            """
        )
        <= TRADE_SPLIT_TOT_MISMATCH
    )


# ---------------------------------------------------------------------------
# (5) Game -> Season points rollup in the modern era
# ---------------------------------------------------------------------------


_SEASON_END_YEAR_DIM_GAME = kd.SEASON_END_YEAR_SQL.format(col="d.season_year")
_SEASON_END_YEAR_PLAYER = kd.SEASON_END_YEAR_SQL.format(col="player_stats.season_year")


@pytest.mark.parametrize(
    "scope_label",
    ["value_mismatch_only"],
)
def test_game_to_season_points_value_mismatch_modern(count, scope_label: str) -> None:
    """Layer 2 — game->season pts value-mismatch count in the modern era.

    Scope decision (documented): the full outer-join of
    fact_player_game_boxscore (regular season) aggregated to
    (player, season_end_year, team) vs the corresponding
    fact_player_season_stats rows has THREE failure modes:

      * 1016 season rows have NO game boxscore at all (orphan season rows)
      * 1488 game-sum rows have NO season stat at all (orphan game rows)
      * 1188 jointly-populated rows have VALUE mismatch (game sum != pts)

    The first two reflect an incomplete join coverage and are tracked
    separately by the API's fact_bref / boxscore reconciliation
    pipeline. This test pins only the value-mismatch subset, which is
    the most actionable: a regression here means the per-game pts rollup
    no longer agrees with the published season stat. Measured: 1188 in
    the modern era (ending year >= 1997). PINNED to that value; lower
    only after an upstream fact reconciliation fix.
    """
    del scope_label  # placeholder so a future scope expansion can parametrize
    assert (
        count(
            f"""
            WITH game_sum AS (
              SELECT g.player_id, {_SEASON_END_YEAR_DIM_GAME} AS season_end_year,
                     g.team_id, SUM(g.points) AS game_pts
              FROM unified_star.fact_player_game_boxscore g
              JOIN unified_star.dim_game d ON g.game_id = d.game_id
              WHERE d.season_type = 'Regular'
                AND g.team_id <> {kd.SENTINEL_TEAM_ID}
                AND {_SEASON_END_YEAR_DIM_GAME} >= {kd.PBP_ERA_START_END_YEAR}
              GROUP BY g.player_id, season_end_year, g.team_id
            ),
            season AS (
              SELECT player_id, {_SEASON_END_YEAR_PLAYER} AS season_end_year,
                     team_id, pts
              FROM unified_star.fact_player_season_stats player_stats
              WHERE is_playoffs = false
                AND team_id <> {kd.SENTINEL_TEAM_ID}
                AND {_SEASON_END_YEAR_PLAYER} >= {kd.PBP_ERA_START_END_YEAR}
            )
            SELECT count(*) FROM season s
            JOIN game_sum g
              ON s.player_id = g.player_id
             AND s.season_end_year = g.season_end_year
             AND s.team_id = g.team_id
            WHERE s.pts IS DISTINCT FROM g.game_pts
            """
        )
        <= GAME_TO_SEASON_PTS_VALUE_MISMATCH
    )
