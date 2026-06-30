"""Layer 6 play-by-play derivation invariants (PBP-era gate, bounded sample).

Layer: data (raw facts only)

Cross-table checks that derive canonical box-score quantities from
``unified_star.fact_pbp_events`` and ``unified_star.fact_game_quarter_scores`` and
assert they reconcile with the corresponding ``*_boxscore`` rollups. These are the
upstream-data checks: a mismatch means the rollup disagrees with its own
sources.

Gating
------
PBP data is only reliable from season **ending year >= 1997** (see
``kd.PBP_ERA_START_END_YEAR``). All checks are restricted to the **last 3
completed seasons** (``2022-23``, ``2023-24``, ``2024-25`` — 4,153 regular +
playoff games; ending years 2023, 2024, 2025 — see "sample scope" below) so
each test scans ~2.4M PBP events instead of 18.7M and the whole module
runs in a couple of seconds.

Sample scope
------------
The last 3 *completed* seasons (2022-23, 2023-24, 2024-25) are used as a
*bounded* PBP sample. The 2025-26 season is excluded because it is still
in progress; expanding the sample further would add runtime without
meaningfully changing regression coverage. This keeps the module fast and
stable for CI; the trade-off is that 3 seasons are measured, not the entire
modern era. The era gate is enforced explicitly by the import-time assert
in the "Bounded PBP sample" block below (every season in the sample has
ending year >= ``kd.PBP_ERA_START_END_YEAR``).

Per-season breakdown of the 4,153-game sample::

    season  | games | CHECK 1 | CHECK 2 | CHECK 3
    --------|-------|---------|---------|--------
    2022-23 | 1,385 |    0    |   97    |  168
    2023-24 | 1,383 |    0    |    0    |  124
    2024-25 | 1,385 |    1    |   40    |  132
    --------|-------|---------|---------|--------
    combined| 4,153 |    1    |  137    |  424

Measured on 2026-06-29 against ``data/nba.duckdb`` (combined over the
expanded 3-season sample)::

    CHECK 1 (final score, PBP max vs team boxscore pts):    1  game
    CHECK 2 (player made-FG, PBP count vs fgm):           137  player-games
    CHECK 3 (quarter -> team, sum(qtr.pts) vs pts):       424  team-games

CHECK 1 is essentially clean (1 game, 1-pt discrepancy in the away side,
the only mismatch across all 3 seasons). CHECK 2 and CHECK 3 are pinned to
module-level constants; a regression that increases either count will fail
CI. The 2023-24 season reconciles to zero on CHECK 2, so the 137-player-
game residual is concentrated in 2022-23 (97) and 2024-25 (40).

Each test takes the ``count`` fixture (auto-skips without DB; the connection is
session-scoped read-only).
"""
from __future__ import annotations

import known_divergences as kd


# ---------------------------------------------------------------------------
# Bounded PBP sample
# ---------------------------------------------------------------------------
# Restrict every PBP / quarter scan in this module to the last 3 completed
# seasons (4,153 games; ending years 2023, 2024, 2025 — all >= kd.PBP_ERA_START_END_YEAR).
# The subquery is inlined as a CTE fragment so the file remains declarative
# and the test collection cost is zero (no Python list materialization).

_SAMPLE_SEASONS: tuple[str, ...] = ("2022-23", "2023-24", "2024-25")

# Enforce the PBP-era gate explicitly so a future edit of _SAMPLE_SEASONS to
# include a pre-1997 season fails at import time instead of silently scanning
# unreliable data. This is the executable form of the header's "ending year
# >= 1997" claim.
for _sample_season in _SAMPLE_SEASONS:
    assert kd.season_end_year(_sample_season) >= kd.PBP_ERA_START_END_YEAR, (
        f"_SAMPLE_SEASONS entry {_sample_season!r} ends in "
        f"{kd.season_end_year(_sample_season)}, before the PBP era "
        f"(>= {kd.PBP_ERA_START_END_YEAR}); pick seasons inside the era."
    )

# SQL fragment yielding the set of sample game_ids. Reused by every check.
_SAMPLE_SEASONS_SQL = ", ".join("'" + s + "'" for s in _SAMPLE_SEASONS)
_SAMPLE_GAMES_CTE = (
    f"(SELECT game_id FROM unified_star.dim_game "
    f"WHERE season_year IN ({_SAMPLE_SEASONS_SQL}))"
)

# CTE for the PBP-game filter in PBP-heavy queries (slightly cheaper than the
# full dim_game scan inside the PBP aggregation).
_SAMPLE_GAMES_GBL = f"game_id IN {_SAMPLE_GAMES_CTE}"


# ---------------------------------------------------------------------------
# Measured baselines (2026-06-29, 3-season sample: 2022-23, 2023-24, 2024-25)
# ---------------------------------------------------------------------------
# Each baseline is a one-sided regression guard (asserted with ``<=``): CI only
# fails when the measured count *increases* past the constant. A *decrease* —
# e.g. from an upstream ETL fix — still passes silently; that improvement is
# ratcheted in by hand by lowering the constant here (see
# ``known_divergences.GENUINE_RESIDUAL_BASELINE`` for the same convention).
# Re-measure by re-running the count fixtures in isolation; the constant and
# the inline COUNT(*) agree.

# CHECK 1: PBP-final vs team boxscore pts (per game). Measured 1 (game 0022400072,
# 1-pt away-side discrepancy in the 2024-25 sample). The 2022-23 and 2023-24
# seasons reconcile exactly; the bulk of modern games reconcile exactly;
# treat this as "almost clean" with a single residual pinned across the 3-season
# sample.
# MEASURED 2026-06-29: 1 mismatched game in the combined 3-season sample.
LAYER6_FINAL_SCORE_MISMATCHED_GAMES: int = 1

# CHECK 2: PBP made-FG count vs fact_player_game_boxscore.fgm (per player-game).
# MEASURED 2026-06-29: 137 mismatched (player, game) rows in the combined 3-season
# sample. Per-season split: 2022-23 = 97, 2023-24 = 0 (clean), 2024-25 = 40.
# Includes: PBP-missing-but-boxscore-has-FGM, boxscore-missing-but-PBP-has-FGM,
# and small PBP/box count-of-1 deltas. See the test docstring for the breakdown.
LAYER6_PLAYER_MADE_FG_MISMATCHED: int = 137

# CHECK 3: sum(fact_game_quarter_scores.pts) per (game_id, team_id) vs
# fact_team_game_boxscore.pts. MEASURED 2026-06-29: 424 mismatched
# (game_id, team_id) rows in the combined 3-season sample. Per-season split:
# 2022-23 = 168, 2023-24 = 124, 2024-25 = 132. None of the affected games
# are flagged as overtime, and the quarter rows have only periods 1-4.
LAYER6_QUARTER_TO_TEAM_PTS_MISMATCHED: int = 424


# ---------------------------------------------------------------------------
# CHECK 1 — final score from PBP matches the team boxscore pts
# ---------------------------------------------------------------------------
# For every modern game: max(score_home) and max(score_away) computed from
# fact_pbp_events must equal the two rows of fact_team_game_boxscore
# (is_home=true and is_home=false). One row per game because the join joins
# both team rows.

_TEAM = "unified_star.fact_team_game_boxscore"
_PBP = "unified_star.fact_pbp_events"


def test_pbp_final_score_matches_team_boxscore(count) -> None:
    """LAYER 6 / PBP derivation — final score derived from PBP == team boxscore pts.

    For every 2022-23..2024-25 game: ``max(score_home)`` and ``max(score_away)``
    over ``fact_pbp_events`` must equal the two ``fact_team_game_boxscore.pts``
    rows (one per side, distinguished by ``is_home``). Measured 1 mismatched
    game in the 3-season sample (game ``0022400072`` — a 1-pt away-side
    discrepancy in the 2024-25 season; the home side reconciles exactly).
    Pinned to the measured baseline.
    """
    sql = f"""
    WITH pbp_final AS (
      SELECT game_id,
        max(score_home) AS pbp_home,
        max(score_away) AS pbp_away
      FROM {_PBP}
      WHERE {_SAMPLE_GAMES_GBL}
      GROUP BY game_id
    )
    SELECT count(*)
    FROM pbp_final p
    JOIN {_TEAM} t1 ON t1.game_id = p.game_id AND t1.is_home = true
    JOIN {_TEAM} t2 ON t2.game_id = p.game_id AND t2.is_home = false
    WHERE p.pbp_home <> t1.pts OR p.pbp_away <> t2.pts
    """
    mismatched = count(sql)
    assert mismatched <= LAYER6_FINAL_SCORE_MISMATCHED_GAMES, (
        f"final-score mismatches jumped to {mismatched} "
        f"(baseline {LAYER6_FINAL_SCORE_MISMATCHED_GAMES}); investigate upstream."
    )


# ---------------------------------------------------------------------------
# CHECK 2 — player made-FG count from PBP matches fact_player_game_boxscore.fgm
# ---------------------------------------------------------------------------
# For every modern (game_id, player_id) pair: count(PBP WHERE is_field_goal AND
# shot_result='Made') must equal fact_player_game_boxscore.fgm. FULL OUTER JOIN
# to catch both "PBP has it, boxscore doesn't" and vice versa. The task's
# noted validation (game 0022501193, 2025-26 season) reconciles perfectly;
# in the 3-season sample the residual is 137 (2022-23=97, 2023-24=0, 2024-25=40).

_PGAME = "unified_star.fact_player_game_boxscore"


def test_pbp_player_made_fg_matches_fgm(count) -> None:
    """LAYER 6 / PBP derivation — per (game, player) PBP made-FG == fact_player_game_boxscore.fgm.

    Counts the ``is_field_goal AND shot_result='Made'`` PBP rows per
    (game_id, player_id) and FULL OUTER JOINs against
    ``fact_player_game_boxscore.fgm`` so both directions of missing-ness are
    counted. The contract was validated on game 0022501193 (2025-26) where
    every player reconciles exactly. Measured residual in the 3-season sample
    (2022-23..2024-25) is 137 player-games (2023-24 reconciles to zero) —
    pinned as a regression guard.
    """
    sql = f"""
    WITH pbp_fg AS (
      SELECT pbp.game_id, pbp.player_id, count(*) AS pbp_fgm
      FROM {_PBP} pbp
      WHERE pbp.is_field_goal AND pbp.shot_result = 'Made'
        AND {_SAMPLE_GAMES_GBL}
      GROUP BY pbp.game_id, pbp.player_id
    ),
    b AS (
      SELECT b.game_id, b.player_id, b.fgm
      FROM {_PGAME} b
      WHERE {_SAMPLE_GAMES_GBL}
    )
    SELECT count(*)
    FROM pbp_fg p
    FULL OUTER JOIN b ON b.game_id = p.game_id AND b.player_id = p.player_id
    WHERE COALESCE(p.pbp_fgm, 0) <> COALESCE(b.fgm, 0)
    """
    mismatched = count(sql)
    assert mismatched <= LAYER6_PLAYER_MADE_FG_MISMATCHED, (
        f"player made-FG mismatches jumped to {mismatched} "
        f"(baseline {LAYER6_PLAYER_MADE_FG_MISMATCHED}); investigate upstream."
    )


# ---------------------------------------------------------------------------
# CHECK 3 — sum(quarter scores.pts) per (game, team) == fact_team_game_boxscore.pts
# ---------------------------------------------------------------------------
# For every modern (game_id, team_id) row in fact_team_game_boxscore, the sum of
# fact_game_quarter_scores.pts over that (game, team) must equal the boxscore
# total. Mismatches here are upstream data drift in either direction
# (quarters undercounted, or team boxscore diverged from the quarter rollup).

_QTR = "unified_star.fact_game_quarter_scores"


def test_pbp_quarter_pts_sum_matches_team_pts(count) -> None:
    """LAYER 6 / PBP derivation — sum(quarter.pts) per (game, team) == team boxscore.pts.

    Aggregates ``fact_game_quarter_scores.pts`` to (game_id, team_id) and
    joins the result against ``fact_team_game_boxscore.pts``. Measured
    residual in the 3-season sample (2022-23..2024-25) is 424 (game, team)
    rows (2022-23=168, 2023-24=124, 2024-25=132). None of the affected games
    are flagged as overtime, and the quarter rows contain only periods 1-4,
    so the deltas are not OT-related. Pinned as a regression guard.
    """
    sql = f"""
    WITH qsum AS (
      SELECT game_id, team_id, sum(pts) AS q_pts
      FROM {_QTR}
      WHERE {_SAMPLE_GAMES_GBL}
      GROUP BY game_id, team_id
    )
    SELECT count(*)
    FROM qsum q
    JOIN {_TEAM} t ON t.game_id = q.game_id AND t.team_id = q.team_id
    WHERE q.q_pts <> t.pts
    """
    mismatched = count(sql)
    assert mismatched <= LAYER6_QUARTER_TO_TEAM_PTS_MISMATCHED, (
        f"quarter->team pts mismatches jumped to {mismatched} "
        f"(baseline {LAYER6_QUARTER_TO_TEAM_PTS_MISMATCHED}); investigate upstream."
    )
