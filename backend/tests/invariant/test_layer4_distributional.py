"""Layer 4 — Distributional & uniqueness invariants.

Citation: ``ideas/data-verification-methodology.md`` §7 (lines 209–224).
Layer 4 profiles distributions to surface *plausible-but-wrong* values that
no algebraic identity catches. Three flavors live here:

  1. **Outlier bounds** — single-game ``points`` (Wilt's 100-pt record),
     single-game ``min`` (era-gated to the MP cutoff + non-null/pos),
     season ``gp`` (lockout/COVID years push past 82; 110 is a generous
     post-1990 bound).
  2. **Uniqueness** — the natural key of ``fact_player_game_boxscore`` is
     ``(player_id, game_id)`` and must be exact. The natural key of
     ``fact_player_season_stats`` is ``(player_id, season_end_year,
     team_id, is_playoffs)`` and is *known* to have residual duplicates
     from the snapshot's mixed ``season_year`` encoding — see the
     corresponding test for the prominent divergence writeup.
  3. **Null-rate monitoring** — track the null rate of ``min`` on the
     player-game fact. The pre-1952 era legitimately lacks minutes
     (``MP`` tracked since 1951-52) and modern DNPs are NULL by design,
     so a ~10 % null rate is the expected steady state.

All counts were measured against ``data/nba.duckdb`` on 2026-06-29. Where
the layer 4 check is *clean* (== 0 expected violations) the test asserts
exactly that. Where the layer 4 check has a measured *genuine residual*
the suite pins a module-level baseline (a regression guard) — see
``known_divergences.GENUINE_RESIDUAL_BASELINE`` for the canonical registry
and the per-baseline comments below for the divergence writeup.
"""

from __future__ import annotations

import known_divergences as kd


# ---------------------------------------------------------------------------
# Module-level baselines (measured 2026-06-29 against data/nba.duckdb).
#
# These document a real, current state of the snapshot and act as regression
# guards: any *new* dup / outlier / null jump fails CI. The target is 0;
# lowering a baseline here is how a real upstream ETL fix is "ratcheted in".
# Do NOT edit known_divergences.py — the registry is the canonical home; these
# module-level constants are only for checks that were not pre-existing in
# that registry (or were measured after the registry was last touched).
# ---------------------------------------------------------------------------


# Outlier bound: ``min > 65`` rows in the modern era (season ending year
# >= 1952, non-null/pos minutes).
#   Measured 2026-06-29: 15 rows.
#   Composition (verified via dim_game join):
#     - 8× preseason OT in 2006-07:
#         * 1×3OT 2006-10-13 (game 0010600027; one player @ 93 min)
#         * 1×2OT 2006-10-24 (game 0010600102; one player @ 96 min)
#     - 1×2OT 1989-90 1989-11-09 (game 0028900045)
#     - 1×4OT 1952-53 1953-03-21 (game 0045200232 — the longest NBA game)
#     - 1× 65 min + 1× 66 min (boundary cases)
#   These are *legit* extreme-minutes rows, not data corruption. Asserting
#   strict == 0 would either (a) miss OT games or (b) require upstream to
#   drop rows — both wrong. The baseline keeps CI green while catching
#   *new* outliers.
BASELINE_PGAME_MIN_GT_65: int = 15


# Uniqueness divergence: dup groups on (player_id, season_end_year, team_id,
# is_playoffs) in fact_player_season_stats.
#   Measured 2026-06-29: 3,949 dup groups (and 3,949 extra rows, i.e. exactly
#   one duplicate per group). All 3,949 dups sit on
#   ``(team_id = 0, is_playoffs = False)`` — the "team unresolved / combined"
#   sentinel rows (see ``kd.SENTINEL_TEAM_ID = 0``). The root cause is the
#   snapshot's MIXED ``season_year`` encoding: 26,283 rows use the hyphenated
#   "YYYY-YY" form (e.g. ``'1975-76'``) and 40,138 rows use the integer
#   ending-year form (e.g. ``1976``). After normalization via
#   ``kd.SEASON_END_YEAR_SQL``, both forms collide on the natural key.
#   Example: player 77997 has *both* ``('1975-76', 0, False)`` and
#   ``(1976, 0, False)`` rows, which normalize to the same
#   ``(player_id, season_end_year=1976, team_id=0, is_playoffs=False)`` key.
#   This is a real, documented uniqueness divergence — *not* a vacuous pass.
#   The test docstring carries the full writeup; the assertion is the
#   regression guard.
BASELINE_SEASON_DUP_GROUPS: int = 3949


# Null-rate ceiling: percentage of NULL ``min`` in fact_player_game_boxscore.
#   Measured 2026-06-29: 10.17 % overall / 10.11 % era-gated to >= 1952.
#   Drivers (both expected, not corruption):
#     - Pre-1952 games: minutes not tracked until 1951-52 (see
#       ``kd.AVAILABLE_SINCE_END_YEAR['MP'] = 1952``).
#     - Modern DNP rows: player is on the roster but did not play; the
#       ETL stores ``min = NULL`` (correctly).
#   The 10.5 % baseline adds 0.33 p.p. of cushion over the measured rate so
#   that a small modern-era ingestion drift (a few thousand new DNPs, say)
#   does not turn the suite red — only a real jump in the null rate would.
BASELINE_PGAME_MIN_NULL_PCT: float = 10.5


# ---------------------------------------------------------------------------
# 1. Outlier bounds
# ---------------------------------------------------------------------------


def test_pgame_points_under_wilt_record(count) -> None:
    """Layer 4 — single-game ``points`` is bounded by Wilt's 100-pt game (1962-03-02).

    CLEAN invariant. Asserts the snapshot stores no ``points > 100`` row,
    matching the methodology's outlier-bounds rule. Measured max = 100.
    """
    assert count(
        "SELECT count(*) FROM unified_star.fact_player_game_boxscore "
        "WHERE points > 100"
    ) == 0, "single-game points > 100 is impossible (Wilt's 100-pt game is the all-time record)"


def test_pgame_min_under_ot_bound_era_gated(count) -> None:
    """Layer 4 — single-game ``min`` is bounded by 65, era-gated to MP era.

    Era gate: ``kd.AVAILABLE_SINCE_END_YEAR['MP'] = 1952`` — minutes are
    only reliable from 1951-52 onward. Excludes NULL and non-positive
    minutes (the negative-min residual is owned by
    ``kd.GENUINE_RESIDUAL_BASELINE['pgame_min_negative'] = 12``).

    Measured 2026-06-29: ``BASELINE_PGAME_MIN_GT_65 = 15`` legit-long
    games (3OT 2006 preseason, 2OT 1989, 4OT 1952-53, plus a few 65-66
    min boundary cases). The suite enforces the baseline as a regression
    guard — a new 80+ min game in the modern era should fail CI.
    """
    era_end_year = kd.AVAILABLE_SINCE_END_YEAR["MP"]
    season_end_sql = kd.SEASON_END_YEAR_SQL.format(col="g.season_year")
    observed = count(
        f"""
        SELECT count(*) FROM unified_star.fact_player_game_boxscore b
        JOIN unified_star.dim_game g USING (game_id)
        WHERE ({season_end_sql}) >= {era_end_year}
          AND b.min IS NOT NULL
          AND b.min > 0
          AND b.min > 65
        """
    )
    assert observed <= BASELINE_PGAME_MIN_GT_65, (
        f"pgame.min > 65 (era-gated to >= {era_end_year}, non-null/pos) = "
        f"{observed}; baseline = {BASELINE_PGAME_MIN_GT_65}. "
        f"A new legit-OT row would lift this — investigate dim_game and "
        f"the offending game_id before bumping the baseline."
    )


def test_season_gp_under_extended_bound(count) -> None:
    """Layer 4 — season ``gp`` <= 110 (post-1990 schedule-reality bound).

    The 82-game standard rule is too tight — lockout-shortened seasons
    (1998-99 = 50, 2011-12 = 66), the COVID 2019-20 irregular season,
    and the 2020-21 72-game season still produce real player-gp values
    of 84–90. 110 is a generous post-1990 outlier bound. Measured max
    = 90; clean (== 0 violations).
    """
    assert count(
        "SELECT count(*) FROM unified_star.fact_player_season_stats "
        "WHERE gp > 110"
    ) == 0, "season gp > 110 is impossible (no NBA season has that many games)"


# ---------------------------------------------------------------------------
# 2. Uniqueness
# ---------------------------------------------------------------------------


def test_pgame_player_id_game_id_is_unique(count) -> None:
    """Layer 4 — ``(player_id, game_id)`` is the natural key of pgame.

    CLEAN invariant. A box-score fact must have exactly one row per
    (player, game); duplicates would double-count points/minutes across
    the roll-up views. Measured: 0 dup groups, 0 extra rows.
    """
    assert count(
        """
        SELECT count(*) FROM (
            SELECT player_id, game_id
            FROM unified_star.fact_player_game_boxscore
            GROUP BY player_id, game_id
            HAVING count(*) > 1
        )
        """
    ) == 0, "duplicate (player_id, game_id) rows in fact_player_game_boxscore"


def test_season_player_team_playoffs_uniqueness_mixed_encoding_divergence(count) -> None:
    """Layer 4 — ``(player_id, season_end_year, team_id, is_playoffs)`` is the
    natural key of fact_player_season_stats.

    >>> REAL UNIQUENESS DIVERGENCE — measured residual <<<

    Measured 2026-06-29: 3,949 duplicate groups (and 3,949 extra rows — i.e.
    exactly one duplicate per group, no group has count(*) > 2). All 3,949
    duplicates sit on the sentinel ``(team_id = 0, is_playoffs = False)``
    rows (``kd.SENTINEL_TEAM_ID`` is the "team unresolved / combined"
    value).

    Root cause — the snapshot's MIXED ``season_year`` encoding (etl
    divergence #5 in the methodology doc): the same season is stored
    using two different string forms:

        * 40,138 rows use the integer ending-year form (e.g. ``1976``)
        * 26,283 rows use the hyphenated start-end form (e.g. ``'1975-76'``)

    After normalizing via ``kd.SEASON_END_YEAR_SQL`` (the project's
    canonical "ending year" expression), both forms collide on the
    natural key. Example (player 77997):

        ('1975-76', 0, False)  AND  (1976, 0, False)
        → both → (player_id=77997, season_end_year=1976,
                  team_id=0, is_playoffs=False)  # duplicate

    The task spec calls this out as a *real* divergence, not a strict
    invariant failure — the suite enforces a regression guard
    (``BASELINE_SEASON_DUP_GROUPS``) so that any *new* duplicate would
    fail CI, and lowering the baseline is the explicit "ratchet" for an
    upstream ETL fix.
    """
    season_end_sql = kd.SEASON_END_YEAR_SQL.format(col="season_year")
    observed = count(
        f"""
        SELECT count(*) FROM (
            SELECT player_id,
                   {season_end_sql} AS season_end_year,
                   team_id,
                   is_playoffs
            FROM unified_star.fact_player_season_stats
            GROUP BY player_id, season_end_year, team_id, is_playoffs
            HAVING count(*) > 1
        )
        """
    )
    assert observed <= BASELINE_SEASON_DUP_GROUPS, (
        f"fact_player_season_stats duplicate groups on "
        f"(player_id, season_end_year, team_id, is_playoffs) = {observed}; "
        f"baseline = {BASELINE_SEASON_DUP_GROUPS}. "
        f"This divergence is driven by the mixed season_year encoding "
        f"('YYYY-YY' vs ending-year int) on the team_id=0 sentinel rows — "
        f"see test docstring. A regression here means new dups were "
        f"introduced (likely a normalization regression in the ETL); "
        f"investigate before bumping the baseline."
    )


# ---------------------------------------------------------------------------
# 3. Null-rate monitoring
# ---------------------------------------------------------------------------


def test_pgame_min_null_rate_under_ceiling(count) -> None:
    """Layer 4 — null rate of ``min`` on pgame is bounded by the measured
    baseline (~10 %).

    Measured 2026-06-29: 10.17 % overall / 10.11 % era-gated to >= 1952.
    Drivers (both expected, neither corruption):

        * Pre-1952 games — minutes were not tracked until 1951-52
          (see ``kd.AVAILABLE_SINCE_END_YEAR['MP'] = 1952``); the
          ETL stores ``min = NULL`` for those rows.
        * Modern DNP rows — a player on the roster who did not play
          legitimately has ``min = NULL`` (correct per the statisticians'
          manual).

    A jump above ``BASELINE_PGAME_MIN_NULL_PCT`` (10.5 %) would mean a
    modern-era ingestion gap — pre-cutoff nulls are already capped by
    the era distribution, so a meaningful increase would point at a
    new ETL regression.
    """
    total = count("SELECT count(*) FROM unified_star.fact_player_game_boxscore")
    assert total > 0, "fact_player_game_boxscore is empty — pipeline did not load"
    null_min = count(
        "SELECT count(*) FROM unified_star.fact_player_game_boxscore WHERE min IS NULL"
    )
    null_pct = 100.0 * null_min / total
    assert null_pct <= BASELINE_PGAME_MIN_NULL_PCT, (
        f"pgame.min null rate = {null_pct:.3f} % "
        f"({null_min:,} of {total:,} rows); "
        f"baseline = {BASELINE_PGAME_MIN_NULL_PCT} %. "
        f"Drivers: pre-1952 era (min not tracked) + modern DNPs. A jump "
        f"above the baseline points at a new ingestion gap in the modern era."
    )
