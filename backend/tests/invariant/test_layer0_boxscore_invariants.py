"""Layer 0 box-score internal-consistency invariants.

Layer: data (raw facts + the canonical served view)

Covers three box-score surfaces and the most basic algebraic relationships
that must hold on every row:

  (A) ``unified_star.fact_player_game_boxscore`` — raw, with era artifacts
  (B) ``unified_star.fact_team_game_boxscore``    — team rollup
  (C) ``api.v_canonical_player_season_totals``    — served season view

Measured against ``data/nba.duckdb`` on 2026-06-29 (see module docstring of
``known_divergences.py`` for the wider registry). Each assertion is pinned to
the measured reality; era artifacts are documented and explained rather than
asserted to be zero.

The three assertion kinds used across the suite (see
``known_divergences.py`` for the full taxonomy):

  * CLEAN invariant     -> ``assert count(...) == 0``
  * GENUINE residual    -> ``assert count(...) <= kd.GENUINE_RESIDUAL_BASELINE[key]``
  * ERA ARTIFACT        -> violation minus explained-portion within genuine baseline
"""
from __future__ import annotations

import known_divergences as kd


# ---------------------------------------------------------------------------
# (A) unified_star.fact_player_game_boxscore  (raw game-level facts)
# ---------------------------------------------------------------------------
# Era artifacts in this table (rows where the upstream data is missing/zero
# pre-1983) are NOT errors — they are how the snapshot stores "stat was not
# tracked yet". Genuine-residual baselines for unexplained rows are pinned
# in known_divergences.GENUINE_RESIDUAL_BASELINE.

_PGAME = "unified_star.fact_player_game_boxscore"


def test_pgame_fgm_le_fga_explained_by_fga_zero(count) -> None:
    """ERA ARTIFACT — player game: fgm>fga - (fgm>fga AND fga=0) <= genuine baseline.

    The bulk of the ``fgm > fga`` rows (~48.3k) have ``fga = 0``, which is the
    era artifact: pre-modern boxscores stored shot attempts as NULL/0, then the
    ETL backfilled ``fgm`` from play-by-play. What remains after excluding the
    explained rows must stay inside the genuine-residual baseline (25).
    """
    fgm_gt_fga = count(f"SELECT count(*) FROM {_PGAME} WHERE fgm > fga")
    fgm_gt_fga_fga_zero = count(
        f"SELECT count(*) FROM {_PGAME} WHERE fgm > fga AND fga = 0"
    )
    unexplained = fgm_gt_fga - fgm_gt_fga_fga_zero
    assert unexplained <= kd.GENUINE_RESIDUAL_BASELINE["pgame_fgm_gt_fga_genuine"]


def test_pgame_fgm_le_fga_genuine(count) -> None:
    """GENUINE residual — same row population as the era-artifact test, explicit form."""
    assert count(
        f"SELECT count(*) FROM {_PGAME} WHERE fgm > fga AND fga > 0"
    ) <= kd.GENUINE_RESIDUAL_BASELINE["pgame_fgm_gt_fga_genuine"]


def test_pgame_ftm_le_fta_genuine(count) -> None:
    """GENUINE residual — ``ftm > fta`` rows with ``fta > 0`` (free-throw attempt present)."""
    assert count(
        f"SELECT count(*) FROM {_PGAME} WHERE ftm > fta AND fta > 0"
    ) <= kd.GENUINE_RESIDUAL_BASELINE["pgame_ftm_gt_fta_genuine"]


def test_pgame_reb_split_genuine(count) -> None:
    """GENUINE residual — ``oreb + dreb <> reb`` rows that are not the oreb=dreb=0 era artifact.

    The ~200.7k artifact rows are pre-1974 era: ORB/DRB were not tracked, so
    both are 0 and TRB carries the real total. Removing the explained-portion
    leaves the genuine residual pinned at 87.
    """
    assert count(
        f"SELECT count(*) FROM {_PGAME} "
        f"WHERE oreb + dreb <> reb AND NOT (oreb = 0 AND dreb = 0)"
    ) <= kd.GENUINE_RESIDUAL_BASELINE["pgame_reb_split_genuine"]


def test_pgame_reb_split_explained_by_both_zero(count) -> None:
    """ERA ARTIFACT — same population, but explicit "both zero" explanation test.

    The artifact portion (oreb=dreb=0, ~200.7k rows) is the dominant signal;
    this test pins the *unexplained* residual to the baseline.
    """
    reb_split = count(f"SELECT count(*) FROM {_PGAME} WHERE oreb + dreb <> reb")
    reb_split_both_zero = count(
        f"SELECT count(*) FROM {_PGAME} "
        f"WHERE oreb + dreb <> reb AND oreb = 0 AND dreb = 0"
    )
    unexplained = reb_split - reb_split_both_zero
    assert unexplained <= kd.GENUINE_RESIDUAL_BASELINE["pgame_reb_split_genuine"]


def test_pgame_pts_identity(count) -> None:
    """GENUINE residual — ``points <> 2*fgm + fg3m + ftm`` (shooting-algebra identity)."""
    assert count(
        f"SELECT count(*) FROM {_PGAME} WHERE points <> 2*fgm + fg3m + ftm"
    ) <= kd.GENUINE_RESIDUAL_BASELINE["pgame_pts_identity"]


def test_pgame_fg_pct_in_range(count) -> None:
    """GENUINE residual — ``fg_pct`` outside [0, 1]."""
    assert count(
        f"SELECT count(*) FROM {_PGAME} WHERE fg_pct < 0 OR fg_pct > 1"
    ) <= kd.GENUINE_RESIDUAL_BASELINE["pgame_fg_pct_out_of_range"]


def test_pgame_fg3m_le_fgm(count) -> None:
    """GENUINE residual — 3-point makes cannot exceed total field-goal makes."""
    assert count(
        f"SELECT count(*) FROM {_PGAME} WHERE fg3m > fgm"
    ) <= kd.GENUINE_RESIDUAL_BASELINE["pgame_fg3m_gt_fgm"]


def test_pgame_fg3a_le_fga(count) -> None:
    """GENUINE residual — 3-point attempts cannot exceed total field-goal attempts."""
    assert count(
        f"SELECT count(*) FROM {_PGAME} WHERE fg3a > fga"
    ) <= kd.GENUINE_RESIDUAL_BASELINE["pgame_fg3a_gt_fga"]


def test_pgame_min_non_negative(count) -> None:
    """GENUINE residual — minutes played cannot be negative."""
    assert count(
        f"SELECT count(*) FROM {_PGAME} WHERE min < 0"
    ) <= kd.GENUINE_RESIDUAL_BASELINE["pgame_min_negative"]


# --- CLEAN checks: any negative among the core counting stats must be 0 ---

def test_pgame_fgm_non_negative(count) -> None:
    """CLEAN — ``fgm`` is a count, never negative."""
    assert count(f"SELECT count(*) FROM {_PGAME} WHERE fgm < 0") == 0


def test_pgame_fga_non_negative(count) -> None:
    """CLEAN — ``fga`` is a count, never negative."""
    assert count(f"SELECT count(*) FROM {_PGAME} WHERE fga < 0") == 0


def test_pgame_ftm_non_negative(count) -> None:
    """CLEAN — ``ftm`` is a count, never negative."""
    assert count(f"SELECT count(*) FROM {_PGAME} WHERE ftm < 0") == 0


def test_pgame_fta_non_negative(count) -> None:
    """CLEAN — ``fta`` is a count, never negative."""
    assert count(f"SELECT count(*) FROM {_PGAME} WHERE fta < 0") == 0


def test_pgame_reb_non_negative(count) -> None:
    """CLEAN — ``reb`` is a count, never negative."""
    assert count(f"SELECT count(*) FROM {_PGAME} WHERE reb < 0") == 0


def test_pgame_points_non_negative(count) -> None:
    """CLEAN — ``points`` is a count, never negative."""
    assert count(f"SELECT count(*) FROM {_PGAME} WHERE points < 0") == 0


def test_pgame_assists_non_negative(count) -> None:
    """CLEAN — ``assists`` is a count, never negative."""
    assert count(f"SELECT count(*) FROM {_PGAME} WHERE assists < 0") == 0


# ---------------------------------------------------------------------------
# (B) unified_star.fact_team_game_boxscore  (team rollup of the same box scores)
# ---------------------------------------------------------------------------
# This is the aggregated team view. By construction it has been validated
# against the underlying player rows, so all the same internal-consistency
# invariants must hold exactly (== 0). Measured 2026-06-29: all clean.

_TEAM = "unified_star.fact_team_game_boxscore"


def test_team_fgm_le_fga(count) -> None:
    """CLEAN — team: ``fgm`` cannot exceed ``fga`` (shooting algebra)."""
    assert count(f"SELECT count(*) FROM {_TEAM} WHERE fgm > fga") == 0


def test_team_fg3m_le_fgm(count) -> None:
    """CLEAN — team: 3-point makes cannot exceed total field-goal makes."""
    assert count(f"SELECT count(*) FROM {_TEAM} WHERE fg3m > fgm") == 0


def test_team_ftm_le_fta(count) -> None:
    """CLEAN — team: ``ftm`` cannot exceed ``fta``."""
    assert count(f"SELECT count(*) FROM {_TEAM} WHERE ftm > fta") == 0


def test_team_reb_split(count) -> None:
    """CLEAN — team: ``oreb + dreb = reb`` must hold for every row.

    No era artifact here: the team rollup was built from modernized data.
    """
    assert count(f"SELECT count(*) FROM {_TEAM} WHERE oreb + dreb <> reb") == 0


def test_team_pts_identity(count) -> None:
    """CLEAN — team: ``pts = 2*fgm + fg3m + ftm`` shooting-algebra identity."""
    assert count(
        f"SELECT count(*) FROM {_TEAM} WHERE pts <> 2*fgm + fg3m + ftm"
    ) == 0


# ---------------------------------------------------------------------------
# (C) api.v_canonical_player_season_totals  (served season view)
# ---------------------------------------------------------------------------
# Mixed-case quoted columns. The view already filters out partial-season
# pre-1983 noise, so all invariants here are clean (== 0). Measured
# 2026-06-29: all clean. Rows with NULL in ORB/DRB/TRB (~3.9k) or
# PTS/FG/3P/FT (~5.8k) are excluded from the comparison by the WHERE clause
# (DuckDB NULL semantics: ``NULL <> 1`` is NULL, not TRUE, so they're filtered).

_V = 'api.v_canonical_player_season_totals'


def test_v_FG_le_FGA(count) -> None:
    """CLEAN — served view: ``"FG" <= "FGA"`` for every row."""
    assert count(f'SELECT count(*) FROM {_V} WHERE "FG" > "FGA"') == 0


def test_v_3P_le_FG(count) -> None:
    """CLEAN — served view: 3-point makes cannot exceed total field-goal makes."""
    assert count(f'SELECT count(*) FROM {_V} WHERE "3P" > "FG"') == 0


def test_v_FT_le_FTA(count) -> None:
    """CLEAN — served view: ``"FT" <= "FTA"`` for every row."""
    assert count(f'SELECT count(*) FROM {_V} WHERE "FT" > "FTA"') == 0


def test_v_reb_split(count) -> None:
    """CLEAN — served view: ``ORB + DRB = TRB`` for every non-null row.

    ~3.9k rows have a NULL in ORB/DRB/TRB; they are implicitly excluded by
    DuckDB NULL comparison semantics, so the count of violating rows is 0.
    """
    assert count(f'SELECT count(*) FROM {_V} WHERE "ORB" + "DRB" <> "TRB"') == 0


def test_v_pts_identity(count) -> None:
    """CLEAN — served view: ``PTS = 2*"FG" + "3P" + "FT"`` for every non-null row.

    ~5.8k rows have a NULL in PTS/FG/3P/FT; they are implicitly excluded.
    """
    assert count(
        f'SELECT count(*) FROM {_V} WHERE "PTS" <> 2*"FG" + "3P" + "FT"'
    ) == 0
