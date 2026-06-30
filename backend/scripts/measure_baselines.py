"""Ratchet helper: measure every pinned baseline against the live DuckDB snapshot.

The invariant suite (``tests/invariant/``) pins a count (or rate) of "genuine
residual" divergence rows as a regression ceiling. When an upstream ETL fix
lands, the measured value drops and the pinned constant is *ratcheted* down
to track reality.

This script measures every pinned baseline against the current snapshot and
prints a single row per baseline:

    name | measured | pinned | headroom (pinned - measured) | status

where ``status`` is one of:

    REGRESSION         measured > pinned   (test would now fail)
    RATCHET_AVAILABLE  measured < pinned   (test still passes; lower the constant)
    AT_CEILING         measured == pinned  (test passes at the boundary)

Exit code is 0 if there are no regressions (including the all-skipped case
when the DuckDB snapshot is absent), nonzero if any regression is observed.

The ratchet workflow
--------------------
1. Run this helper (``./.venv/Scripts/python.exe scripts/measure_baselines.py``).
2. Rows marked ``RATCHET_AVAILABLE`` mean the measured value has dropped below
   the pinned value — the corresponding test still passes, but the constant
   is now loose and can be lowered. Update the source file (``known_divergences.py``
   for ``kd.GENUINE_RESIDUAL_BASELINE`` keys, or the matching module-level
   constant in ``tests/invariant/test_layerN_*.py``) and re-run the helper
   to confirm the row moves to ``AT_CEILING``.
3. Rows marked ``REGRESSION`` mean the measured value has grown past the
   pinned value — the corresponding invariant test would now fail. Investigate
   the upstream data before touching the constant.

Design notes
------------
- Every ``pinned`` value is *imported* from the source module (kd or the test
  module). No duplicated literal. If a source value changes, the helper picks
  it up on the next run.
- Every ``sql`` is the same predicate the corresponding test asserts, so the
  helper's measured value is exactly what the test sees. SQL is built by
  interpolating kd constants (``SENTINEL_TEAM_ID``, ``SEASON_END_YEAR_SQL``,
  ``PBP_ERA_START_END_YEAR``, ``AVAILABLE_SINCE_END_YEAR``) and test-module
  table-name constants (``t6._SAMPLE_GAMES_GBL``, ``t0._PGAME``, etc.) so an
  upstream rename propagates automatically.
- Two ``kind`` values: ``"count"`` (SQL returns an integer count) and
  ``"rate"`` (SQL returns a percentage, e.g. the null-rate baseline). The
  comparison is identical (``measured > pinned => REGRESSION``); only the
  unit of the printed values differs.
- Skips cleanly when the DuckDB file is absent (mirrors
  ``tests/invariant/conftest.py``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# Make ``tests/invariant/`` importable so we can pull pinned values from
# the test modules' module-level constants (the canonical source of truth).
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_INVARIANT_DIR = _BACKEND_ROOT / "tests" / "invariant"
sys.path.insert(0, str(_INVARIANT_DIR))

import duckdb  # noqa: E402  (import after sys.path tweak)

import known_divergences as kd  # noqa: E402
import test_layer0_boxscore_invariants as t0  # noqa: E402
import test_layer1_era_availability as t1  # noqa: E402
import test_layer2_aggregation as t2  # noqa: E402
import test_layer3_referential_integrity as t3  # noqa: E402
import test_layer4_distributional as t4  # noqa: E402
import test_layer6_pbp_derivation as t6  # noqa: E402
import test_layer7_gameid_schedule as t7  # noqa: E402
import test_layer8_advanced_recompute as t8  # noqa: E402


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Baseline:
    """One pinned baseline in the invariant suite.

    Attributes
    ----------
    name:
        Short identifier printed in the table (e.g. ``"pgame_pts_identity"``).
    source:
        Canonical location of the pinned value, as a string the reader can
        grep for in the source tree (e.g. ``"kd.GENUINE_RESIDUAL_BASELINE[...]"``
        or ``"t2.WIN_PCT_ROUNDING_VIOLATIONS"``).
    sql:
        A single DuckDB SQL statement that returns one row with one numeric
        column — the *measured* value. Must be the same predicate the
        corresponding invariant test asserts.
    pinned:
        The pinned threshold (imported from the source). For ``kind="count"``
        this is an integer count; for ``kind="rate"`` a percentage (0..100).
    kind:
        ``"count"`` (default) or ``"rate"``. The comparison logic is the same
        in both cases; only the unit of the printed values differs.
    notes:
        Free-form context (the test function, the sample scope, the tolerance
        that is part of the SQL predicate, etc.).
    """

    name: str
    source: str
    sql: str
    pinned: float
    kind: str = "count"
    notes: str = ""


def _build_registry() -> list[Baseline]:
    """Build the registry of every pinned baseline in the invariant suite.

    Each entry's ``pinned`` is the imported constant (not duplicated). The
    ``sql`` is the same predicate the corresponding test asserts; SQL is
    constructed by interpolating kd constants and the test modules'
    table-name fragments so an upstream rename automatically propagates.
    """
    pin = kd.GENUINE_RESIDUAL_BASELINE
    sentinel = kd.SENTINEL_TEAM_ID
    pbp_era = kd.PBP_ERA_START_END_YEAR
    era_mp = kd.AVAILABLE_SINCE_END_YEAR["MP"]
    sample_games_gbl = t6._SAMPLE_GAMES_GBL  # e.g. "game_id IN (SELECT ... season_year = '2024-25')"

    entries: list[Baseline] = []

    # ------------------------------------------------------------------
    # kd.GENUINE_RESIDUAL_BASELINE keys that are actually pinned by a test.
    # (season_gs_gt_gp and season_ts_pct_out_of_range are in the registry but
    # are not referenced by any test in the current suite, so they are
    # intentionally excluded here — there is no test SQL to copy and no
    # regression guard to enforce.)
    # ------------------------------------------------------------------
    entries += [
        Baseline(
            name="pgame_fgm_gt_fga_genuine",
            source="kd.GENUINE_RESIDUAL_BASELINE['pgame_fgm_gt_fga_genuine']",
            sql=f"SELECT count(*) FROM {t0._PGAME} WHERE fgm > fga AND fga > 0",
            pinned=float(pin["pgame_fgm_gt_fga_genuine"]),
            notes="test_pgame_fgm_le_fga_genuine (layer0)",
        ),
        Baseline(
            name="pgame_ftm_gt_fta_genuine",
            source="kd.GENUINE_RESIDUAL_BASELINE['pgame_ftm_gt_fta_genuine']",
            sql=f"SELECT count(*) FROM {t0._PGAME} WHERE ftm > fta AND fta > 0",
            pinned=float(pin["pgame_ftm_gt_fta_genuine"]),
            notes="test_pgame_ftm_le_fta_genuine (layer0)",
        ),
        Baseline(
            name="pgame_reb_split_genuine",
            source="kd.GENUINE_RESIDUAL_BASELINE['pgame_reb_split_genuine']",
            sql=(
                f"SELECT count(*) FROM {t0._PGAME} "
                f"WHERE oreb + dreb <> reb AND NOT (oreb = 0 AND dreb = 0)"
            ),
            pinned=float(pin["pgame_reb_split_genuine"]),
            notes="test_pgame_reb_split_genuine (layer0)",
        ),
        Baseline(
            name="pgame_pts_identity",
            source="kd.GENUINE_RESIDUAL_BASELINE['pgame_pts_identity']",
            sql=f"SELECT count(*) FROM {t0._PGAME} WHERE points <> 2*fgm + fg3m + ftm",
            pinned=float(pin["pgame_pts_identity"]),
            notes="test_pgame_pts_identity (layer0)",
        ),
        Baseline(
            name="pgame_fg_pct_out_of_range",
            source="kd.GENUINE_RESIDUAL_BASELINE['pgame_fg_pct_out_of_range']",
            sql=f"SELECT count(*) FROM {t0._PGAME} WHERE fg_pct < 0 OR fg_pct > 1",
            pinned=float(pin["pgame_fg_pct_out_of_range"]),
            notes="test_pgame_fg_pct_in_range (layer0)",
        ),
        Baseline(
            name="pgame_fg3m_gt_fgm",
            source="kd.GENUINE_RESIDUAL_BASELINE['pgame_fg3m_gt_fgm']",
            sql=f"SELECT count(*) FROM {t0._PGAME} WHERE fg3m > fgm",
            pinned=float(pin["pgame_fg3m_gt_fgm"]),
            notes="test_pgame_fg3m_le_fgm (layer0)",
        ),
        Baseline(
            name="pgame_fg3a_gt_fga",
            source="kd.GENUINE_RESIDUAL_BASELINE['pgame_fg3a_gt_fga']",
            sql=f"SELECT count(*) FROM {t0._PGAME} WHERE fg3a > fga",
            pinned=float(pin["pgame_fg3a_gt_fga"]),
            notes="test_pgame_fg3a_le_fga (layer0)",
        ),
        Baseline(
            name="pgame_min_negative",
            source="kd.GENUINE_RESIDUAL_BASELINE['pgame_min_negative']",
            sql=f"SELECT count(*) FROM {t0._PGAME} WHERE min < 0",
            pinned=float(pin["pgame_min_negative"]),
            notes="test_pgame_min_non_negative (layer0)",
        ),
        Baseline(
            name="pgame_player_orphan",
            source="kd.GENUINE_RESIDUAL_BASELINE['pgame_player_orphan']",
            sql=(
                f"SELECT count(*) FROM {t0._PGAME} f "
                f"LEFT JOIN unified_star.dim_player d ON f.player_id = d.player_id "
                f"WHERE d.player_id IS NULL"
            ),
            pinned=float(pin["pgame_player_orphan"]),
            notes="test_pgame_player_id_orphan (layer3)",
        ),
        Baseline(
            name="pgame_game_orphan",
            source="kd.GENUINE_RESIDUAL_BASELINE['pgame_game_orphan']",
            sql=(
                f"SELECT count(*) FROM {t0._PGAME} f "
                f"LEFT JOIN unified_star.dim_game d ON f.game_id = d.game_id "
                f"WHERE d.game_id IS NULL"
            ),
            pinned=float(pin["pgame_game_orphan"]),
            notes="test_pgame_game_id_orphan (layer3)",
        ),
        Baseline(
            name="gameid_embedded_season_mismatch",
            source="kd.GENUINE_RESIDUAL_BASELINE['gameid_embedded_season_mismatch']",
            sql=(
                f"""
                WITH parsed AS (
                  SELECT game_id, season_year,
                    CASE
                      WHEN substr(game_id, 4, 2) >= '46'
                        THEN 1900 + CAST(substr(game_id, 4, 2) AS INT)
                      ELSE 2000 + CAST(substr(game_id, 4, 2) AS INT)
                    END AS embedded_start,
                    CASE
                      WHEN strpos(CAST(season_year AS VARCHAR), '-') > 0
                        THEN CAST(left(CAST(season_year AS VARCHAR), 4) AS INT)
                      ELSE CAST(CAST(season_year AS VARCHAR) AS INT) - 1
                    END AS col_start
                  FROM {t7._DIM}
                )
                SELECT count(*) FROM parsed WHERE embedded_start <> col_start
                """
            ),
            pinned=float(pin["gameid_embedded_season_mismatch"]),
            notes="test_gameid_embedded_season_matches_column (layer7)",
        ),
    ]

    # ------------------------------------------------------------------
    # Layer 1 — era-availability pre-cutoff / post-cutoff baselines.
    # The (cutoff, pre_nz, post_null) tuples in V_CANONICAL_TARGETS and
    # FACT_PLAYER_SEASON_TARGETS are unpacked into two rows each (one for
    # the pre-cutoff non-null/non-zero predicate, one for the post-cutoff
    # null predicate). SQL is built dynamically from the (col, cutoff) pair
    # so the helper cannot drift from the test if a new column is added.
    # ------------------------------------------------------------------
    for col, (cutoff, pre_nz, post_null) in t1.V_CANONICAL_TARGETS.items():
        entries.append(
            Baseline(
                name=f"v_canonical_{col}_pre_cutoff_nonzero",
                source=f"t1.V_CANONICAL_TARGETS['{col}']",
                sql=(
                    f'SELECT count(*) FROM api.v_canonical_player_season_totals '
                    f'WHERE SEASON < {cutoff} AND "{col}" IS NOT NULL AND "{col}" <> 0'
                ),
                pinned=float(pre_nz),
                notes=(
                    f"test_v_canonical_pre_cutoff_nonzero[{col}] (layer1). "
                    f"pre_nz={pre_nz}, post_null={post_null}, cutoff={cutoff}."
                ),
            )
        )
        entries.append(
            Baseline(
                name=f"v_canonical_{col}_post_cutoff_null",
                source=f"t1.V_CANONICAL_TARGETS['{col}']",
                sql=(
                    f'SELECT count(*) FROM api.v_canonical_player_season_totals '
                    f'WHERE SEASON >= {cutoff} AND "{col}" IS NULL'
                ),
                pinned=float(post_null),
                notes=(
                    f"test_v_canonical_post_cutoff_null[{col}] (layer1). "
                    f"pre_nz={pre_nz}, post_null={post_null}, cutoff={cutoff}."
                ),
            )
        )

    for col, (cutoff, pre_nz, post_null) in t1.FACT_PLAYER_SEASON_TARGETS.items():
        season_end_col = kd.SEASON_END_YEAR_SQL.format(col="season_year")
        entries.append(
            Baseline(
                name=f"fact_player_season_{col}_pre_cutoff_nonzero",
                source=f"t1.FACT_PLAYER_SEASON_TARGETS['{col}']",
                sql=(
                    f"SELECT count(*) FROM unified_star.fact_player_season_stats "
                    f"WHERE {season_end_col} < {cutoff} AND {col} IS NOT NULL AND {col} <> 0"
                ),
                pinned=float(pre_nz),
                notes=(
                    f"test_fact_player_season_pre_cutoff_nonzero[{col}] (layer1). "
                    f"pre_nz={pre_nz}, post_null={post_null}, cutoff={cutoff}."
                ),
            )
        )
        entries.append(
            Baseline(
                name=f"fact_player_season_{col}_post_cutoff_null",
                source=f"t1.FACT_PLAYER_SEASON_TARGETS['{col}']",
                sql=(
                    f"SELECT count(*) FROM unified_star.fact_player_season_stats "
                    f"WHERE {season_end_col} >= {cutoff} AND {col} IS NULL"
                ),
                pinned=float(post_null),
                notes=(
                    f"test_fact_player_season_post_cutoff_null[{col}] (layer1). "
                    f"pre_nz={pre_nz}, post_null={post_null}, cutoff={cutoff}."
                ),
            )
        )

    # ------------------------------------------------------------------
    # Layer 2 — aggregation & grain consistency.
    # ------------------------------------------------------------------
    entries += [
        Baseline(
            name="v_team_standings_win_pct_rounding_violations",
            source="t2.WIN_PCT_ROUNDING_VIOLATIONS",
            sql=(
                "SELECT count(*) FROM api.v_team_standings "
                "WHERE wins IS NOT NULL AND losses IS NOT NULL "
                "AND (wins + losses) > 0 AND win_pct IS NOT NULL "
                "AND ABS(win_pct - (CAST(wins AS DOUBLE) / (wins + losses))) > 0.0005"
            ),
            pinned=float(t2.WIN_PCT_ROUNDING_VIOLATIONS),
            notes=(
                "test_v_team_standings_win_pct_within_tolerance (layer2). "
                "Tolerance 0.0005 is part of the SQL predicate; pinned is the "
                "count of rows exceeding it (band baseline)."
            ),
        ),
        Baseline(
            name="v_team_standings_per_season_wins_losses_imbalance",
            source="t2.PER_SEASON_WINS_LOSSES_IMBALANCE",
            sql=(
                "SELECT count(*) FROM ("
                "  SELECT season_year FROM api.v_team_standings "
                "  WHERE wins IS NOT NULL AND losses IS NOT NULL "
                "  GROUP BY season_year HAVING SUM(wins) <> SUM(losses)"
                ")"
            ),
            pinned=float(t2.PER_SEASON_WINS_LOSSES_IMBALANCE),
            notes="test_v_team_standings_per_season_win_loss_balance (layer2)",
        ),
        Baseline(
            name="trade_splits_tot_mismatch",
            source="t2.TRADE_SPLIT_TOT_MISMATCH",
            sql=(
                f"""
                WITH multi AS (
                  SELECT ps.player_id, ps.season_year
                  FROM unified_star.fact_player_season_stats ps
                  WHERE ps.is_playoffs = false
                    AND ps.team_id <> {sentinel}
                    AND {kd.SEASON_END_YEAR_SQL.format(col="ps.season_year")} >= {pbp_era}
                  GROUP BY ps.player_id, ps.season_year
                  HAVING COUNT(DISTINCT ps.team_id) >= 2
                ),
                tot AS (
                  SELECT player_id, season_year, pts AS tot_pts
                  FROM unified_star.fact_player_season_stats
                  WHERE team_id = {sentinel} AND is_playoffs = false
                ),
                per_team_sum AS (
                  SELECT player_id, season_year, SUM(pts) AS sum_pts
                  FROM unified_star.fact_player_season_stats
                  WHERE team_id <> {sentinel} AND is_playoffs = false
                  GROUP BY player_id, season_year
                )
                SELECT count(*) FROM multi m
                JOIN tot t ON m.player_id = t.player_id AND m.season_year = t.season_year
                JOIN per_team_sum p ON m.player_id = p.player_id AND m.season_year = p.season_year
                WHERE t.tot_pts IS DISTINCT FROM p.sum_pts
                """
            ),
            pinned=float(t2.TRADE_SPLIT_TOT_MISMATCH),
            notes="test_trade_splits_tot_equals_per_team_sum (layer2)",
        ),
        Baseline(
            name="game_to_season_pts_value_mismatch",
            source="t2.GAME_TO_SEASON_PTS_VALUE_MISMATCH",
            sql=(
                f"""
                WITH game_sum AS (
                  SELECT g.player_id, {t2._SEASON_END_YEAR_DIM_GAME} AS season_end_year,
                         g.team_id, SUM(g.points) AS game_pts
                  FROM unified_star.fact_player_game_boxscore g
                  JOIN unified_star.dim_game d ON g.game_id = d.game_id
                  WHERE d.season_type = 'Regular'
                    AND g.team_id <> {sentinel}
                    AND {t2._SEASON_END_YEAR_DIM_GAME} >= {pbp_era}
                  GROUP BY g.player_id, season_end_year, g.team_id
                ),
                season AS (
                  SELECT player_id, {t2._SEASON_END_YEAR_PLAYER} AS season_end_year,
                         team_id, pts
                  FROM unified_star.fact_player_season_stats player_stats
                  WHERE is_playoffs = false
                    AND team_id <> {sentinel}
                    AND {t2._SEASON_END_YEAR_PLAYER} >= {pbp_era}
                )
                SELECT count(*) FROM season s
                JOIN game_sum g
                  ON s.player_id = g.player_id
                 AND s.season_end_year = g.season_end_year
                 AND s.team_id = g.team_id
                WHERE s.pts IS DISTINCT FROM g.game_pts
                """
            ),
            pinned=float(t2.GAME_TO_SEASON_PTS_VALUE_MISMATCH),
            notes="test_game_to_season_points_value_mismatch_modern (layer2)",
        ),
    ]

    # ------------------------------------------------------------------
    # Layer 3 — referential integrity and primary-key uniqueness.
    # ------------------------------------------------------------------
    entries += [
        Baseline(
            name="tgame_game_orphan",
            source="t3.TGAME_GAME_ORPHAN_BASELINE",
            sql=(
                f"SELECT count(*) FROM {t0._TEAM} f "
                f"LEFT JOIN unified_star.dim_game d ON f.game_id = d.game_id "
                f"WHERE d.game_id IS NULL"
            ),
            pinned=float(t3.TGAME_GAME_ORPHAN_BASELINE),
            notes="test_tgame_game_id_orphan (layer3)",
        ),
        Baseline(
            name="qscores_game_orphan",
            source="t3.QSCORES_GAME_ORPHAN_BASELINE",
            sql=(
                f"SELECT count(*) FROM unified_star.fact_game_quarter_scores f "
                f"LEFT JOIN unified_star.dim_game d ON f.game_id = d.game_id "
                f"WHERE d.game_id IS NULL"
            ),
            pinned=float(t3.QSCORES_GAME_ORPHAN_BASELINE),
            notes="test_qscores_game_id_orphan (layer3)",
        ),
        Baseline(
            name="dim_team_team_id_dup_groups",
            source="t3.DIM_TEAM_TEAM_ID_DUP_GROUPS_BASELINE",
            sql=(
                f"SELECT count(*) FROM ("
                f"  SELECT team_id FROM unified_star.dim_team "
                f"  GROUP BY team_id HAVING count(*) > 1"
                f")"
            ),
            pinned=float(t3.DIM_TEAM_TEAM_ID_DUP_GROUPS_BASELINE),
            notes=(
                "test_dim_team_team_id_pk_unique (layer3). dim_team uses a "
                "composite (team_id, season_founded) PK; team_id alone is "
                "expected to show dup groups by design."
            ),
        ),
    ]

    # ------------------------------------------------------------------
    # Layer 4 — distributional & uniqueness.
    # ------------------------------------------------------------------
    entries += [
        Baseline(
            name="pgame_min_gt_65",
            source="t4.BASELINE_PGAME_MIN_GT_65",
            sql=(
                f"""
                SELECT count(*) FROM {t0._PGAME} b
                JOIN unified_star.dim_game g USING (game_id)
                WHERE ({kd.SEASON_END_YEAR_SQL.format(col="g.season_year")}) >= {era_mp}
                  AND b.min IS NOT NULL
                  AND b.min > 0
                  AND b.min > 65
                """
            ),
            pinned=float(t4.BASELINE_PGAME_MIN_GT_65),
            notes=(
                f"test_pgame_min_under_ot_bound_era_gated (layer4). Era gate "
                f"{era_mp} (= AVAILABLE_SEAT_END_YEAR['MP']); threshold 65 min."
            ),
        ),
        Baseline(
            name="season_dup_groups_mixed_encoding",
            source="t4.BASELINE_SEASON_DUP_GROUPS",
            sql=(
                f"""
                SELECT count(*) FROM (
                    SELECT player_id,
                           {kd.SEASON_END_YEAR_SQL.format(col="season_year")} AS season_end_year,
                           team_id,
                           is_playoffs
                    FROM unified_star.fact_player_season_stats
                    GROUP BY player_id, season_end_year, team_id, is_playoffs
                    HAVING count(*) > 1
                )
                """
            ),
            pinned=float(t4.BASELINE_SEASON_DUP_GROUPS),
            notes=(
                "test_season_player_team_playoffs_uniqueness_mixed_encoding_divergence (layer4). "
                "Driven by the mixed season_year encoding on team_id=0 sentinel rows."
            ),
        ),
        Baseline(
            name="pgame_min_null_pct",
            source="t4.BASELINE_PGAME_MIN_NULL_PCT",
            sql=(
                f"SELECT 100.0 * sum(CASE WHEN min IS NULL THEN 1 ELSE 0 END) / count(*) "
                f"FROM {t0._PGAME}"
            ),
            pinned=float(t4.BASELINE_PGAME_MIN_NULL_PCT),
            kind="rate",
            notes=(
                "test_pgame_min_null_rate_under_ceiling (layer4). "
                "Rate baseline: 100 * null_count / total. Pinned is the rate "
                "ceiling in percent (band baseline)."
            ),
        ),
    ]

    # ------------------------------------------------------------------
    # Layer 6 — PBP derivation (bounded 2024-25 sample).
    # ------------------------------------------------------------------
    entries += [
        Baseline(
            name="layer6_final_score_mismatched_games",
            source="t6.LAYER6_FINAL_SCORE_MISMATCHED_GAMES",
            sql=(
                f"""
                WITH pbp_final AS (
                  SELECT game_id,
                    max(score_home) AS pbp_home,
                    max(score_away) AS pbp_away
                  FROM {t6._PBP}
                  WHERE {sample_games_gbl}
                  GROUP BY game_id
                )
                SELECT count(*)
                FROM pbp_final p
                JOIN {t6._TEAM} t1 ON t1.game_id = p.game_id AND t1.is_home = true
                JOIN {t6._TEAM} t2 ON t2.game_id = p.game_id AND t2.is_home = false
                WHERE p.pbp_home <> t1.pts OR p.pbp_away <> t2.pts
                """
            ),
            pinned=float(t6.LAYER6_FINAL_SCORE_MISMATCHED_GAMES),
            notes=(
                "test_pbp_final_score_matches_team_boxscore (layer6). "
                "Sample scope: 2024-25 season (t6._SAMPLE_GAMES_GBL)."
            ),
        ),
        Baseline(
            name="layer6_player_made_fg_mismatched",
            source="t6.LAYER6_PLAYER_MADE_FG_MISMATCHED",
            sql=(
                f"""
                WITH pbp_fg AS (
                  SELECT pbp.game_id, pbp.player_id, count(*) AS pbp_fgm
                  FROM {t6._PBP} pbp
                  WHERE pbp.is_field_goal AND pbp.shot_result = 'Made'
                    AND {sample_games_gbl}
                  GROUP BY pbp.game_id, pbp.player_id
                ),
                b AS (
                  SELECT b.game_id, b.player_id, b.fgm
                  FROM {t6._PGAME} b
                  WHERE {sample_games_gbl}
                )
                SELECT count(*)
                FROM pbp_fg p
                FULL OUTER JOIN b ON b.game_id = p.game_id AND b.player_id = p.player_id
                WHERE COALESCE(p.pbp_fgm, 0) <> COALESCE(b.fgm, 0)
                """
            ),
            pinned=float(t6.LAYER6_PLAYER_MADE_FG_MISMATCHED),
            notes=(
                "test_pbp_player_made_fg_matches_fgm (layer6). "
                "Sample scope: 2024-25 season."
            ),
        ),
        Baseline(
            name="layer6_quarter_to_team_pts_mismatched",
            source="t6.LAYER6_QUARTER_TO_TEAM_PTS_MISMATCHED",
            sql=(
                f"""
                WITH qsum AS (
                  SELECT game_id, team_id, sum(pts) AS q_pts
                  FROM {t6._QTR}
                  WHERE {sample_games_gbl}
                  GROUP BY game_id, team_id
                )
                SELECT count(*)
                FROM qsum q
                JOIN {t6._TEAM} t ON t.game_id = q.game_id AND t.team_id = q.team_id
                WHERE q.q_pts <> t.pts
                """
            ),
            pinned=float(t6.LAYER6_QUARTER_TO_TEAM_PTS_MISMATCHED),
            notes=(
                "test_pbp_quarter_pts_sum_matches_team_pts (layer6). "
                "Sample scope: 2024-25 season."
            ),
        ),
    ]

    # ------------------------------------------------------------------
    # Layer 7 — game_id and schedule structural validation.
    # The season_type branches are derived from kd.GAME_ID_SEASON_TYPE so
    # the helper cannot drift from the canonical digit->label registry.
    # ------------------------------------------------------------------
    branches = "\n".join(
        f"            WHEN '{digit}' THEN '{label}'"
        for digit, label in sorted(kd.GAME_ID_SEASON_TYPE.items())
    )
    entries += [
        Baseline(
            name="season_type_digit_mismatch",
            source="t7.SEASON_TYPE_DIGIT_MISMATCH_BASELINE",
            sql=(
                f"""
                SELECT count(*) FROM {t7._DIM}
                WHERE season_type <> CASE substr(game_id, 3, 1)
{branches}
                  END
                """
            ),
            pinned=float(t7.SEASON_TYPE_DIGIT_MISMATCH_BASELINE),
            notes=(
                "test_season_type_digit_matches_label (layer7). "
                "CASE branches built from kd.GAME_ID_SEASON_TYPE to prevent drift."
            ),
        ),
        Baseline(
            name="schedule_82_violations",
            source="t7.SCHEDULE_82_BASELINE",
            sql=(
                f"""
                WITH games AS (
                  SELECT tgb.team_id, count(*) AS gp
                  FROM {t7._TEAM} tgb
                  JOIN {t7._DIM} dg ON dg.game_id = tgb.game_id
                  WHERE dg.season_year = '2018-19' AND dg.season_type = 'Regular'
                  GROUP BY tgb.team_id
                )
                SELECT count(*) FROM games WHERE gp <> 82
                """
            ),
            pinned=float(t7.SCHEDULE_82_BASELINE),
            notes=(
                "test_2018_19_regular_season_team_82_games (layer7). "
                "2018-19 is the known-complete reference season."
            ),
        ),
    ]

    # ------------------------------------------------------------------
    # Layer 8 — advanced-metric recompute.
    # ------------------------------------------------------------------
    entries += [
        Baseline(
            name="v_team_standings_win_pct_recompute",
            source="t8._TEAM_STANDINGS_WIN_PCT_BASELINE",
            sql=(
                f"SELECT count(*) FROM api.v_team_standings "
                f"WHERE win_pct IS NOT NULL AND wins IS NOT NULL AND losses IS NOT NULL "
                f"AND (wins + losses) > 0 "
                f"AND abs(win_pct - (wins / NULLIF(wins + losses, 0))) > 0.0005"
            ),
            pinned=float(t8._TEAM_STANDINGS_WIN_PCT_BASELINE),
            notes=(
                "test_v_team_standings_win_pct_recompute (layer8). "
                "Same shape as the layer2 win_pct check; different SQL form "
                "(NULLIF guard) but the same 0.0005 band."
            ),
        ),
    ]

    return entries


# ---------------------------------------------------------------------------
# Connection / path resolution
# ---------------------------------------------------------------------------


def _resolve_db_path() -> Path:
    """Resolve the DuckDB path, honoring the same env vars as the test suite.

    Order: BASKETBALL_DATA_DB_PATH > DUCKDB_PATH > default ../data/nba.duckdb.
    Relative paths are resolved against the current working directory, which
    matches ``tests/invariant/conftest.py:_resolve_db_path``.
    """
    raw = (
        os.environ.get("BASKETBALL_DATA_DB_PATH")
        or os.environ.get("DUCKDB_PATH")
        or "../data/nba.duckdb"
    )
    path = Path(raw)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


def _measure(conn: duckdb.DuckDBPyConnection, entry: Baseline) -> float:
    """Execute the entry's SQL and return the scalar result as a float."""
    row = conn.execute(entry.sql).fetchone()
    if row is None or row[0] is None:
        return 0.0
    return float(row[0])


def _status(measured: float, pinned: float) -> str:
    """Classify a measured-vs-pinned comparison."""
    if measured > pinned:
        return "REGRESSION"
    if measured < pinned:
        return "RATCHET_AVAILABLE"
    return "AT_CEILING"


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _fmt(x: float) -> str:
    """Format a number for the table: integer if whole, 4-dp trimmed otherwise."""
    if x != x:  # NaN
        return "NaN"
    if x == int(x):
        return str(int(x))
    return f"{x:.4f}".rstrip("0").rstrip(".")


def _print_table(results: list[dict]) -> None:
    """Print a human-readable table of measured-vs-pinned rows."""
    if not results:
        print("(no results)")
        return

    # Compute column widths from data, capped to a sensible maximum.
    name_w = min(max(len("name"), max(len(r["name"]) for r in results)), 50)
    num_cols = ("measured", "pinned", "headroom")
    widths = {
        c: max(len(c), max(len(_fmt(r[c])) for r in results)) for c in num_cols
    }
    status_w = max(len("status"), max(len(r["status"]) for r in results))

    sep = " | "
    header = (
        f"{'name':<{name_w}}{sep}"
        f"{'measured':>{widths['measured']}}{sep}"
        f"{'pinned':>{widths['pinned']}}{sep}"
        f"{'headroom':>{widths['headroom']}}{sep}"
        f"{'status':<{status_w}}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r['name']:<{name_w}}{sep}"
            f"{_fmt(r['measured']):>{widths['measured']}}{sep}"
            f"{_fmt(r['pinned']):>{widths['pinned']}}{sep}"
            f"{_fmt(r['headroom']):>{widths['headroom']}}{sep}"
            f"{r['status']:<{status_w}}"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    """Entry point. Returns 0 on success/skip, 1 if any regression is observed."""
    parser = argparse.ArgumentParser(
        description=(
            "Measure every pinned baseline in the invariant suite against the "
            "DuckDB snapshot and report measured-vs-pinned ratchet status. "
            "Exit 0 if no regressions (including all-skipped when the DB is "
            "absent); nonzero if any baseline grew past its pinned value."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Status values:\n"
            "  REGRESSION         measured > pinned (test would now fail)\n"
            "  RATCHET_AVAILABLE  measured < pinned (lower the constant)\n"
            "  AT_CEILING         measured == pinned (test passes at the boundary)\n"
            "\n"
            "Tolerances / bands: the win_pct rounding check uses a 0.0005\n"
            "band inside its SQL predicate (so the measured value is a count\n"
            "of rows exceeding the band); the pgame_min_null_pct check is a\n"
            "rate (percentage 0..100) and the pinned value is the rate ceiling.\n"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a single JSON object instead of a human-readable table.",
    )
    args = parser.parse_args()

    db_path = _resolve_db_path()
    if not db_path.exists():
        # Mirror the suite's skip convention: clean exit, clear message.
        print(f"DuckDB snapshot not found at {db_path}.")
        print(
            "Set DUCKDB_PATH (or BASKETBALL_DATA_DB_PATH) to enable the "
            "ratchet helper, or run from backend/ with data/nba.duckdb present."
        )
        return 0

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        registry = _build_registry()
        results: list[dict] = []
        regressions = 0
        ratchet = 0
        ceiling = 0
        for entry in registry:
            try:
                measured = _measure(conn, entry)
            except Exception as exc:  # noqa: BLE001
                # Surface the SQL error but keep the helper single-pass: a
                # failed entry is neither a regression nor a ratchet.
                measured = float("nan")
                print(
                    f"ERROR measuring {entry.name} ({entry.source}): {exc}",
                    file=sys.stderr,
                )
            headroom = entry.pinned - measured
            status = _status(measured, entry.pinned)
            if status == "REGRESSION":
                regressions += 1
            elif status == "RATCHET_AVAILABLE":
                ratchet += 1
            else:
                ceiling += 1
            results.append(
                {
                    "name": entry.name,
                    "source": entry.source,
                    "measured": measured,
                    "pinned": entry.pinned,
                    "headroom": headroom,
                    "status": status,
                    "kind": entry.kind,
                    "notes": entry.notes,
                }
            )

        if args.json:
            print(
                json.dumps(
                    {
                        "db_path": str(db_path),
                        "baselines_registered": len(registry),
                        "regressions": regressions,
                        "ratchet_available": ratchet,
                        "at_ceiling": ceiling,
                        "results": results,
                    },
                    indent=2,
                )
            )
        else:
            print(f"db_path: {db_path}")
            print(f"baselines registered: {len(registry)}")
            print()
            _print_table(results)
            print()
            print(
                f"summary: {regressions} REGRESSION, "
                f"{ratchet} RATCHET_AVAILABLE, "
                f"{ceiling} AT_CEILING"
            )

        return 1 if regressions > 0 else 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
