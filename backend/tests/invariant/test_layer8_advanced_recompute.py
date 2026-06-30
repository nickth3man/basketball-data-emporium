"""Layer 8 advanced-metric recomputation invariants.

Layer: data (canonical served views + team standings)

Recomputes the standard advanced-metric formulas directly from the raw
counting stats stored in the view and verifies that the stored value is
within the documented tolerance. Any recomputable relationship whose
inputs exist in the view is checked; metrics whose inputs are absent
are skipped (documented in a docstring) rather than asserted vacuously.

Conventions (see ``known_divergences.py`` for the wider taxonomy):
  * CLEAN invariant     -> ``assert count(...) == 0``
  * GENUINE residual    -> ``assert count(...) <= _MEASURED_BASELINE_<key>``

All tolerances are taken from the layer-8 contract; the 0.001 tolerance
for player shooting percentages accommodates the 3-decimal storage
precision of Basketball-Reference-derived data, and the 0.0005 tolerance
for standings win_pct accommodates the 3-decimal storage of season win
percentages (e.g. 37/80 = 0.4625 stored as 0.463, delta 0.0005).

Measured against ``data/nba.duckdb`` on 2026-06-29. The genuine-residual
baseline for ``win_pct`` (30 rows) is pinned here as a module-level
constant — the target is 0 and lowering it requires storing win_pct at
higher precision upstream.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Measured baselines (2026-06-29, data/nba.duckdb).
# Each constant is keyed by the assertion it backs; lowering one is how a
# fix gets ratcheted in. Do not edit known_divergences.py for these.
# ---------------------------------------------------------------------------

# 30 standings rows have a stored win_pct at 3-decimal precision that
# differs from the recomputed value by exactly 0.0005 (e.g. 37/80 = 0.4625
# stored as 0.463; 42/96 = 0.4375 stored as 0.438). All other rows match
# to within 0.0005.
_TEAM_STANDINGS_WIN_PCT_BASELINE: int = 30


# ---------------------------------------------------------------------------
# (A) api.v_canonical_player_season_totals — shooting percentage recompute
# ---------------------------------------------------------------------------
# Mixed-case columns are quoted. Each test counts rows where every input
# is non-null AND the denominator is positive, then asserts that the
# stored percentage matches the recomputed one to within 0.001.

_V = "api.v_canonical_player_season_totals"


def test_v_eFG_pct_recompute(count) -> None:
    """CLEAN — served view: ``"eFG%" == ("FG" + 0.5*"3P") / NULLIF("FGA", 0)`` (tol 0.001)."""
    assert count(
        f'SELECT count(*) FROM {_V} '
        f'WHERE "eFG%" IS NOT NULL AND "FG" IS NOT NULL AND "3P" IS NOT NULL '
        f'AND "FGA" IS NOT NULL AND "FGA" > 0 '
        f'AND abs("eFG%" - (("FG" + 0.5*"3P") / NULLIF("FGA", 0))) > 0.001'
    ) == 0


def test_v_FG_pct_recompute(count) -> None:
    """CLEAN — served view: ``"FG%" == "FG" / NULLIF("FGA", 0)`` (tol 0.001)."""
    assert count(
        f'SELECT count(*) FROM {_V} '
        f'WHERE "FG%" IS NOT NULL AND "FG" IS NOT NULL AND "FGA" IS NOT NULL AND "FGA" > 0 '
        f'AND abs("FG%" - ("FG" / NULLIF("FGA", 0))) > 0.001'
    ) == 0


def test_v_3P_pct_recompute(count) -> None:
    """CLEAN — served view: ``"3P%" == "3P" / NULLIF("3PA", 0)`` (tol 0.001)."""
    assert count(
        f'SELECT count(*) FROM {_V} '
        f'WHERE "3P%" IS NOT NULL AND "3P" IS NOT NULL AND "3PA" IS NOT NULL AND "3PA" > 0 '
        f'AND abs("3P%" - ("3P" / NULLIF("3PA", 0))) > 0.001'
    ) == 0


def test_v_FT_pct_recompute(count) -> None:
    """CLEAN — served view: ``"FT%" == "FT" / NULLIF("FTA", 0)`` (tol 0.001)."""
    assert count(
        f'SELECT count(*) FROM {_V} '
        f'WHERE "FT%" IS NOT NULL AND "FT" IS NOT NULL AND "FTA" IS NOT NULL AND "FTA" > 0 '
        f'AND abs("FT%" - ("FT" / NULLIF("FTA", 0))) > 0.001'
    ) == 0


def test_v_2P_pct_recompute(count) -> None:
    """CLEAN — served view: ``"2P%" == "2P" / NULLIF("2PA", 0)`` (tol 0.001)."""
    assert count(
        f'SELECT count(*) FROM {_V} '
        f'WHERE "2P%" IS NOT NULL AND "2P" IS NOT NULL AND "2PA" IS NOT NULL AND "2PA" > 0 '
        f'AND abs("2P%" - ("2P" / NULLIF("2PA", 0))) > 0.001'
    ) == 0


# ---------------------------------------------------------------------------
# (B) api.v_team_standings — win_pct recompute
# ---------------------------------------------------------------------------
# win_pct is stored at 3-decimal precision; 30 rows have a recomputed
# value that differs by exactly 0.0005 (the half-step rounding at the 4th
# decimal). Pinned as a genuine-residual baseline; target is 0 once the
# upstream value is stored at higher precision.

_TS = "api.v_team_standings"


def test_v_team_standings_win_pct_recompute(count) -> None:
    """GENUINE — standings: ``win_pct == wins / NULLIF(wins + losses, 0)`` (tol 0.0005).

    Measured residual 2026-06-29: 30 rows differ by exactly 0.0005 due to
    3-decimal storage of season win percentage (e.g. 37/80 = 0.4625
    stored as 0.463). Pinned to module-level baseline.
    """
    assert count(
        f'SELECT count(*) FROM {_TS} '
        f'WHERE win_pct IS NOT NULL AND wins IS NOT NULL AND losses IS NOT NULL '
        f'AND (wins + losses) > 0 '
        f'AND abs(win_pct - (wins / NULLIF(wins + losses, 0))) > 0.0005'
    ) <= _TEAM_STANDINGS_WIN_PCT_BASELINE


# ---------------------------------------------------------------------------
# (C) api.v_canonical_team_season — TS% sanity
# ---------------------------------------------------------------------------
# The view exposes ``"TS%"`` (with space-of-pct suffix); sanity-bound it
# to the [0, 1.5] range — true shooting percentage for an NBA team
# should sit comfortably inside this band; > 1.5 indicates upstream
# data corruption.

_TT = "api.v_canonical_team_season"


def test_v_team_season_TS_pct_in_range(count) -> None:
    """CLEAN — team season: ``"TS%"`` within the sanity range [0, 1.5].

    All inputs (``"TS%"``) are present in the view; the check counts
    rows outside the sanity range and asserts exactly zero. NBA true
    shooting percentage is bounded above by 1.0 for a team that never
    misses; we leave headroom to 1.5 to flag obvious corruption without
    noise from rounding edge cases.
    """
    assert count(
        f'SELECT count(*) FROM {_TT} '
        f'WHERE "TS%" IS NOT NULL AND ("TS%" < 0 OR "TS%" > 1.5)'
    ) == 0


# ---------------------------------------------------------------------------
# Documented non-recomputable metrics
# ---------------------------------------------------------------------------
# The contract scope for layer 8 covers eFG%, FG%, 3P%, FT%, 2%, win_pct,
# and TS% sanity. Other advanced metrics are not part of this layer:
#
#   * PER (Player Efficiency Rating) — requires minutes, team pace, and
#     league-average normalization constants that are not stored in the
#     canonical view. Not recomputable from the view alone.
#   * Game Score — requires steals, blocks, fouls, and minutes; the
#     inputs exist in the view but the layer-8 contract scope is the
#     shooting/standings recompute set.
#   * ORtg/DRtg (team offensive/defensive rating) — present as columns
#     in v_canonical_team_season but require possessions (FGA + 0.44*FTA
#     - OREB + TOV) computed at a different grain. Out of scope for
#     layer 8.
# These omissions are deliberate: the contract says "only assert
# relationships whose inputs exist in the view" and the layer-8 scope
# is the recompute-set named above.
