"""Layer 7 — game_id and schedule structural validation.

Layer: data (unified_star.dim_game + fact_team_game_boxscore)

Verifies the structural integrity of NBA game identifiers and the schedule
they index. Every NBA game is identified by a 10-digit string of the form
``00{T}{YYYYYY}`` where:

  * The first two characters are always ``"00"``.
  * Position 3 is the **season-type digit** (1=Preseason, 2=Regular,
    3=All-Star, 4=Playoffs, 5=Play-In, 6=Cup / NBA Cup).
  * Positions 4-5 are the **two-digit starting year** of the season
    (e.g. ``19`` for 2019-20).
  * Positions 6-10 are a per-type serial number.

The contract under test (data-verification-methodology.md, Layer 7):

  1. ``dim_game.game_id`` is unique and matches the 10-digit pattern
     ``^00[1-6][0-9]{7}$`` for every row.
  2. ``fact_team_game_boxscore`` contains exactly **two** rows per game
     (one per team).
  3. The two-digit start-year embedded in ``game_id`` matches the
     ``season_year`` column (genuine-residual baseline 33 — pre-2024
     games whose id was assigned against a different season).
  4. The season-type digit at position 3 maps (mostly) to
     ``dim_game.season_type`` via ``kd.GAME_ID_SEASON_TYPE``.
  5. The modern schedule is complete: every team plays 82 Regular-season
     games in a known-complete season (2018-19).

All baselines were measured against ``data/nba.duckdb`` on 2026-06-29.

The three assertion kinds used across the suite (see
``known_divergences.py`` for the full taxonomy):

  * CLEAN invariant     -> ``assert count(...) == 0``
  * GENUINE residual    -> ``assert count(...) <= kd.GENUINE_RESIDUAL_BASELINE[key]``
  * MEASURED BASELINE   -> a module-level constant in this file
                          (regression guard, target = 0)
"""
from __future__ import annotations

import known_divergences as kd


_DIM = "unified_star.dim_game"
_TEAM = "unified_star.fact_team_game_boxscore"

# ---------------------------------------------------------------------------
# Module-level measured baselines
# ---------------------------------------------------------------------------
# Measured 2026-06-29. Documented breakdown for ``season_type_digit_mismatch``
# (111 total):
#
#   * 66 — 2023-24 NBA Cup group play. The In-Season Tournament round-robin
#     games were assigned game_ids with type digit "2" (they are part of the
#     regular-season schedule) but correctly labeled ``season_type = 'Cup'``.
#     The season_type column is the canonical label; the digit is misleading.
#
#   * 37 — Play-In tournament. These games have type digit "5" but are
#     labeled ``season_type = 'Regular'`` in the snapshot (the season_type
#     column does not surface "Play-In" as a distinct label).
#
#   * 7  — 2025-26 All-Star / Rising Stars weekend (Feb 13-15, 2026). Type
#     digit "3" but labeled ``season_type = 'Regular'`` (same Play-In
#     convention issue: the column does not surface "All-Star").
#
#   * 1  — 2024-25 NBA Cup opener (game_id 0062400001, 2024-12-17). Type
#     digit "6" but labeled ``season_type = 'Regular'``. Single outlier
#     (likely a Cup qualifier exhibition).
#
# kd.GAME_ID_SEASON_TYPE maps digit -> label, but the snapshot's
# season_type column is incomplete (missing All-Star and Play-In). The
# digit-label mapping therefore has a hard floor around 111 rows; lowering
# the baseline requires either expanding ``season_type`` to include the
# missing labels or doing the mapping at read time.

SEASON_TYPE_DIGIT_MISMATCH_BASELINE: int = 111

# Schedule completeness: 2018-19 is a known-clean season (zero
# COVID-shortening, pre-NBA-Cup). Measured 2026-06-29: 30/30 teams at
# 82 Regular games, zero exceptions. Used as a regression guard.

SCHEDULE_82_BASELINE: int = 0


# ---------------------------------------------------------------------------
# (1) Format: game_id matches ^00[1-6][0-9]{7}$
# ---------------------------------------------------------------------------

def test_dim_game_id_format(count) -> None:
    """CLEAN — every ``dim_game.game_id`` matches the 10-digit NBA pattern.

    The pattern is ``^00[1-6][0-9]{7}$``: prefix ``"00"``, season-type digit
    in 1..6 (Preseason/Regular/All-Star/Playoffs/Play-In/Cup), then seven
    trailing digits (embedded start-year + per-type serial).

    Measured 2026-06-29: 0 non-matches across all 73,246 dim_game rows.
    """
    assert count(
        f"SELECT count(*) FROM {_DIM} "
        f"WHERE NOT regexp_matches(CAST(game_id AS VARCHAR), '^00[1-6][0-9]{{7}}$')"
    ) == 0


# ---------------------------------------------------------------------------
# (2) Uniqueness: dim_game.game_id is unique
# ---------------------------------------------------------------------------

def test_dim_game_id_unique(count) -> None:
    """CLEAN — ``dim_game.game_id`` is unique (no duplicate rows).

    Measured 2026-06-29: 73,246 rows, 73,246 distinct game_ids.
    """
    assert count(
        f"SELECT count(*) FROM ("
        f"  SELECT game_id FROM {_DIM} GROUP BY game_id HAVING count(*) > 1"
        f")"
    ) == 0


# ---------------------------------------------------------------------------
# (3) Two team rows per game in fact_team_game_boxscore
# ---------------------------------------------------------------------------

def test_fact_team_game_two_rows(count) -> None:
    """CLEAN — every game in ``fact_team_game_boxscore`` has exactly 2 team rows.

    The team boxscore is one row per (game_id, team_id); every regular
    NBA game has two participants. A row-count other than 2 indicates
    either a missing team or a phantom game.

    Measured 2026-06-29: 0 violations across all 75,980 rows
    (37,990 unique game_ids).
    """
    assert count(
        f"SELECT count(*) FROM ("
        f"  SELECT game_id FROM {_TEAM} "
        f"  GROUP BY game_id HAVING count(DISTINCT team_id) <> 2"
        f")"
    ) == 0


# ---------------------------------------------------------------------------
# (4) Embedded season matches season_year
# ---------------------------------------------------------------------------

def test_gameid_embedded_season_matches_column(count) -> None:
    """GENUINE residual — embedded start-year in game_id matches ``season_year``.

    The two digits at ``substr(game_id, 4, 2)`` encode the season's start
    year using the 1946 pivot: ``1900+YY`` if ``YY >= 46`` else ``2000+YY``
    (e.g. ``46`` -> 1946, ``19`` -> 2019, ``45`` -> 2045). The value is
    compared to ``kd.season_start_year(season_year)`` so the
    hyphenated-form ("2019-20" -> 2019) and integer-form (2020 -> 2019)
    encodings both normalize correctly.

    Measured 2026-06-29: 33 mismatches, all of the form
    ``game_id starts with 00119...`` paired with ``season_year = '2020-21'``
    (a 2019 bubble/postponement era where the id was stamped against the
    previous season). Baseline pinned in
    ``kd.GENUINE_RESIDUAL_BASELINE['gameid_embedded_season_mismatch']``.
    """
    assert count(
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
          FROM {_DIM}
        )
        SELECT count(*) FROM parsed WHERE embedded_start <> col_start
        """
    ) <= kd.GENUINE_RESIDUAL_BASELINE["gameid_embedded_season_mismatch"]


# ---------------------------------------------------------------------------
# (5) Season-type digit vs label
# ---------------------------------------------------------------------------

def test_season_type_digit_matches_label(count) -> None:
    """MEASURED BASELINE — ``game_id[3]`` maps to ``season_type`` via kd.

    The season-type digit at position 3 is mapped to its label through
    ``kd.GAME_ID_SEASON_TYPE`` (``1``->``Preseason``, ``2``->``Regular``,
    ``3``->``All-Star``, ``4``->``Playoffs``, ``5``->``Play-In``,
    ``6``->``Cup``) and compared to ``dim_game.season_type``.

    Measured 2026-06-29: 111 mismatches. Full breakdown in the
    ``SEASON_TYPE_DIGIT_MISMATCH_BASELINE`` comment block at the top of
    this module. The 66 Cup-group-play + 37 Play-In + 7 All-Star + 1 Cup
    opener rows are explained by the ``season_type`` column's missing
    All-Star / Play-In labels and by NBA Cup group play sharing the
    Regular-season id space. Lowering the baseline requires an upstream
    fix (expand ``season_type`` labels or normalize at the id-digit level).
    """
    # Derive the CASE branches from ``kd.GAME_ID_SEASON_TYPE`` so the test
    # cannot drift from the canonical digit->label registry (the contract
    # names kd as the source of truth).
    branches = "\n".join(
        f"            WHEN '{digit}' THEN '{label}'"
        for digit, label in sorted(kd.GAME_ID_SEASON_TYPE.items())
    )
    assert count(
        f"""
        SELECT count(*) FROM {_DIM}
        WHERE season_type <> CASE substr(game_id, 3, 1)
{branches}
          END
        """
    ) <= SEASON_TYPE_DIGIT_MISMATCH_BASELINE


# ---------------------------------------------------------------------------
# (6) Optional schedule completeness: every team plays 82 Regular games
#     in a known-complete modern season (2018-19).
# ---------------------------------------------------------------------------

def test_2018_19_regular_season_team_82_games(count) -> None:
    """MEASURED BASELINE — 2018-19: every team played exactly 82 Regular games.

    2018-19 is a known-complete season: pre-COVID, pre-NBA-Cup, full
    30-team schedule. Used as a regression guard against schedule
    completeness drift.

    Measured 2026-06-29: 30/30 teams at 82 Regular games, 0 exceptions.
    Pinned to ``SCHEDULE_82_BASELINE`` (= 0) at module scope.
    """
    # Sanity guard against vacuous pass: the per-team assertion below is
    # ``count(teams WHERE gp<>82) <= 0``, which would also be satisfied by
    # an *empty* result set if the season_year/season_type filter ever
    # stopped matching rows (e.g. a schema re-encode). Pin the matched team
    # count to the modern NBA size (30) so a silent filter regression
    # fails loudly here instead of passing the next assertion vacuously.
    teams_matched = count(
        f"""
        WITH games AS (
          SELECT tgb.team_id, count(*) AS gp
          FROM {_TEAM} tgb
          JOIN {_DIM} dg ON dg.game_id = tgb.game_id
          WHERE dg.season_year = '2018-19' AND dg.season_type = 'Regular'
          GROUP BY tgb.team_id
        )
        SELECT count(DISTINCT team_id) FROM games
        """
    )
    assert teams_matched == 30, (
        f"Expected exactly 30 teams in 2018-19 Regular; got {teams_matched}. "
        "A drop to 0 would make the per-team check below pass vacuously."
    )
    assert count(
        f"""
        WITH games AS (
          SELECT tgb.team_id, count(*) AS gp
          FROM {_TEAM} tgb
          JOIN {_DIM} dg ON dg.game_id = tgb.game_id
          WHERE dg.season_year = '2018-19' AND dg.season_type = 'Regular'
          GROUP BY tgb.team_id
        )
        SELECT count(*) FROM games WHERE gp <> 82
        """
    ) <= SCHEDULE_82_BASELINE
