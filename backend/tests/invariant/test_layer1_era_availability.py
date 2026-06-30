"""Layer 1 — era-aware stat availability.

Verifies that every stat in ``AVAILABLE_SINCE_END_YEAR`` is NULL on rows whose
ending year is before the official NBA start-of-tracking year for that stat.
Pre-cutoff values stored as ``0`` are the *0-as-NULL artifact*; pre-cutoff values
stored as non-zero (e.g. partial real BRef data that pre-dates the canonical
cutoff) are *genuine residual* — tracked but tolerated with a pinned baseline.

Targets
-------
1. ``api.v_canonical_player_season_totals`` (SEASON = integer ending year):
   ORB, DRB, STL, BLK (1974); TOV (1978); 3P, 3PA (1980); GS (1982);
   TRB (1951); MP (1952).
2. ``unified_star.fact_player_season_stats`` (mixed season_year encoding):
   stl, blk (1974).  Uses ``kd.SEASON_END_YEAR_SQL`` to normalise to the
   ending year because the table stores a mix of "1947" and "1972-73".

For each (table, column) we measure two counts and pin assertions to them:

* ``pre_nonnull_nonzero``  -- rows where ending_year < cutoff AND col is
  non-null AND col <> 0.  This is "real data before the stat was tracked"
  (genuine residual; baseline = measured value).
* ``post_null``            -- rows where ending_year >= cutoff AND col IS
  NULL.  After a stat starts being tracked, missing values are real
  coverage holes (baseline = measured value, ideally 0).

When *all* pre-cutoff non-null rows are zero, those rows are the *0-as-NULL
artifact* (the upstream store substituted 0 for NULL).  We still assert
``pre_nonnull_nonzero`` against its baseline (which equals 0 in that case)
and document the artifact count in a comment.

Run::

    cd backend && ./.venv/Scripts/python.exe -m pytest \\
        tests/invariant/test_layer1_era_availability.py -q --no-header
"""

from __future__ import annotations

import known_divergences as kd
import pytest


# ---------------------------------------------------------------------------
# Module-level baselines (measured against data/nba.duckdb on 2026-06-29).
# Each comment records (a) the measured pre-cutoff non-zero count (the
# genuine-residual baseline) and (b) the post-cutoff null count, plus any
# artifact (pre-cutoff non-null but zero) for context.
# ---------------------------------------------------------------------------

# api.v_canonical_player_season_totals (SEASON = integer ending year).
# Format: name -> (cutoff, pre_nonnull_nonzero_baseline, post_null_baseline).
V_CANONICAL_TARGETS: dict[str, tuple[int, int, int]] = {
    # ORB cutoff 1974: pre_nonnull=0 (no artifact), post_null=0 -> CLEAN.
    "ORB":  (1974, 0, 0),
    # DRB cutoff 1974: pre_nonnull=0 (no artifact), post_null=0 -> CLEAN.
    "DRB":  (1974, 0, 0),
    # STL cutoff 1974: pre_nonnull=0 (no artifact), post_null=0 -> CLEAN.
    "STL":  (1974, 0, 0),
    # BLK cutoff 1974: pre_nonnull=0 (no artifact), post_null=0 -> CLEAN.
    "BLK":  (1974, 0, 0),
    # TOV cutoff 1978: pre_nonnull=0 (no artifact), post_null=0 -> CLEAN.
    "TOV":  (1978, 0, 0),
    # 3P cutoff 1980: pre_nonnull=0 (no artifact), post_null=0 -> CLEAN.
    "3P":   (1980, 0, 0),
    # 3PA cutoff 1980: pre_nonnull=0 (no artifact), post_null=0 -> CLEAN.
    "3PA":  (1980, 0, 0),
    # GS cutoff 1982:
    #   pre_nonnull = 451 rows  (zero=119, nonzero=332)
    #   The 332 non-zero rows are BRef data that pre-dates the kd cutoff and
    #   were kept in the canonical view (BRef started tracking GS for some
    #   players from 1970-71).  Genuine residual: 332.
    #   The 119 zero-valued non-null rows are the 0-as-NULL artifact (a real
    #   GS=0 stat should be NULL pre-1982); tolerated for now.
    #   post_null=0 -> CLEAN.
    "GS":   (1982, 332, 0),
    # TRB cutoff 1951: pre_nonnull=0 (no artifact), post_null=0 -> CLEAN.
    "TRB":  (1951, 0, 0),
    # MP cutoff 1952: pre_nonnull=0 (no artifact), post_null=0 -> CLEAN.
    "MP":   (1952, 0, 0),
}

# unified_star.fact_player_season_stats (mixed season_year encoding).
# Pre-1974 STL/BLK anomaly:
#   STL pre-1974: 24 non-null rows in season 1972-73 (all team_id=0 sentinel,
#     "TOT/merged" rows).  Of those 23 are non-zero (real pre-tracking data
#     pulled in from BRef/ABA sources) and 1 is stl=0 (player gp=14, blk=2).
#   BLK pre-1974: 26 non-null rows (1 from 1971-72, 25 from 1972-73, all
#     team_id=0).  Of those 25 are non-zero and 1 is blk=0.
#   These are the documented "pre-1974 steals anomaly in fact_player_season_stats"
#   called out in the spec; tolerated with the measured baseline.
FACT_PLAYER_SEASON_TARGETS: dict[str, tuple[int, int, int]] = {
    # STL cutoff 1974: pre_nonnull=24 (zero=1, nonzero=23), post_null=0.
    "stl":  (1974, 23, 0),
    # BLK cutoff 1974: pre_nonnull=26 (zero=1, nonzero=25), post_null=0.
    "blk":  (1974, 25, 0),
}


def _end_year_sql(col: str) -> str:
    """Normalise a season column to its ending year (handles both encodings)."""
    return kd.SEASON_END_YEAR_SQL.format(col=col)


# ---------------------------------------------------------------------------
# v_canonical_player_season_totals -- per-stat parametrised tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "col,cutoff,pre_baseline,post_baseline",
    [
        (col, cutoff, pre_nz, post_null)
        for col, (cutoff, pre_nz, post_null) in V_CANONICAL_TARGETS.items()
    ],
    ids=list(V_CANONICAL_TARGETS.keys()),
)
def test_v_canonical_pre_cutoff_nonzero(
    count, col: str, cutoff: int, pre_baseline: int, post_baseline: int,
) -> None:
    """[Layer 1] Pre-cutoff non-null & non-zero rows stay within baseline.

    Rows where ``SEASON < cutoff`` (the canonical view's SEASON is already an
    integer ending year) AND the column is non-null and non-zero are *genuine
    residual* — real stat data that pre-dates the official tracking start.  The
    measured baseline is pinned in ``V_CANONICAL_TARGETS``; this test fails the
    moment the upstream load produces more pre-cutoff non-zero data than the
    recorded baseline.
    """
    observed = count(
        f'SELECT count(*) FROM api.v_canonical_player_season_totals '
        f'WHERE SEASON < {cutoff} AND "{col}" IS NOT NULL AND "{col}" <> 0'
    )
    assert observed <= pre_baseline, (
        f"v_canonical.{col} pre-cutoff non-null & non-zero: "
        f"observed={observed} > baseline={pre_baseline} "
        f"(cutoff={cutoff}, target=0 pending upstream fix)"
    )


@pytest.mark.parametrize(
    "col,cutoff,pre_baseline,post_baseline",
    [
        (col, cutoff, pre_nz, post_null)
        for col, (cutoff, pre_nz, post_null) in V_CANONICAL_TARGETS.items()
    ],
    ids=list(V_CANONICAL_TARGETS.keys()),
)
def test_v_canonical_post_cutoff_null(
    count, col: str, cutoff: int, pre_baseline: int, post_baseline: int,
) -> None:
    """[Layer 1] Post-cutoff NULL rows stay within baseline.

    Once a stat is officially tracked (ending year >= cutoff), any NULL value
    is a coverage hole.  For every column in this layer the measured baseline
    is 0 — the canonical view is complete from the cutoff onward.
    """
    observed = count(
        f'SELECT count(*) FROM api.v_canonical_player_season_totals '
        f'WHERE SEASON >= {cutoff} AND "{col}" IS NULL'
    )
    assert observed <= post_baseline, (
        f"v_canonical.{col} post-cutoff NULL: "
        f"observed={observed} > baseline={post_baseline} (cutoff={cutoff})"
    )


# ---------------------------------------------------------------------------
# unified_star.fact_player_season_stats -- per-stat parametrised tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "col,cutoff,pre_baseline,post_baseline",
    [
        (col, cutoff, pre_nz, post_null)
        for col, (cutoff, pre_nz, post_null) in FACT_PLAYER_SEASON_TARGETS.items()
    ],
    ids=list(FACT_PLAYER_SEASON_TARGETS.keys()),
)
def test_fact_player_season_pre_cutoff_nonzero(
    count, col: str, cutoff: int, pre_baseline: int, post_baseline: int,
) -> None:
    """[Layer 1] Pre-cutoff non-null & non-zero rows stay within baseline.

    ``season_year`` is a mixed encoding ("1947" or "1972-73"); ``SEASON_END_YEAR_SQL``
    normalises it to the integer ending year.  The pre-1974 non-null STL/BLK rows
    are all ``team_id = 0`` (the SENTINEL_TEAM_ID for combined/TOT rows); they
    contain real pre-tracking BRef/ABA data that should arguably be NULL but
    cannot be removed without an upstream ETL change.
    """
    end_year = _end_year_sql("season_year")
    observed = count(
        f"SELECT count(*) FROM unified_star.fact_player_season_stats "
        f"WHERE {end_year} < {cutoff} AND {col} IS NOT NULL AND {col} <> 0"
    )
    assert observed <= pre_baseline, (
        f"fact_player_season_stats.{col} pre-cutoff non-null & non-zero: "
        f"observed={observed} > baseline={pre_baseline} "
        f"(cutoff={cutoff}, target=0 pending upstream fix)"
    )


@pytest.mark.parametrize(
    "col,cutoff,pre_baseline,post_baseline",
    [
        (col, cutoff, pre_nz, post_null)
        for col, (cutoff, pre_nz, post_null) in FACT_PLAYER_SEASON_TARGETS.items()
    ],
    ids=list(FACT_PLAYER_SEASON_TARGETS.keys()),
)
def test_fact_player_season_post_cutoff_null(
    count, col: str, cutoff: int, pre_baseline: int, post_baseline: int,
) -> None:
    """[Layer 1] Post-cutoff NULL rows stay within baseline.

    Once a stat is officially tracked, NULL in the season fact is a coverage
    hole.  Baseline = 0 (clean) for both stl and blk in the current snapshot.
    """
    end_year = _end_year_sql("season_year")
    observed = count(
        f"SELECT count(*) FROM unified_star.fact_player_season_stats "
        f"WHERE {end_year} >= {cutoff} AND {col} IS NULL"
    )
    assert observed <= post_baseline, (
        f"fact_player_season_stats.{col} post-cutoff NULL: "
        f"observed={observed} > baseline={post_baseline} (cutoff={cutoff})"
    )


# ---------------------------------------------------------------------------
# Targeted characterization of the pre-1974 STL/BLK anomaly.
# ---------------------------------------------------------------------------

def test_fact_stl_pre1974_anomaly_isolated_to_team_zero(
    count,
) -> None:
    """[Layer 1] All pre-1974 non-null STL rows are ``team_id = 0`` (TOT/merged).

    This is the structural fingerprint of the documented "pre-1974 steals
    anomaly in fact_player_season_stats": 24 rows in season 1972-73 (and
    nothing earlier) where STL is populated even though the cutoff is 1974.
    Every such row has ``team_id = 0`` (the SENTINEL_TEAM_ID); no real team
    row carries pre-1974 STL data.  This test pins the structural property
    and fails loudly if the anomaly ever leaks into a concrete team_id.
    """
    end_year = _end_year_sql("season_year")
    rows_with_real_team = count(
        f"SELECT count(*) FROM unified_star.fact_player_season_stats "
        f"WHERE {end_year} < 1974 AND stl IS NOT NULL AND team_id <> 0"
    )
    assert rows_with_real_team == 0, (
        "Pre-1974 non-null STL rows must be confined to team_id=0 sentinel; "
        f"observed {rows_with_real_team} rows with a real team_id"
    )


def test_fact_stl_pre1974_anomaly_count_and_zero_split(count) -> None:
    """[Layer 1] Pre-1974 STL anomaly: 24 non-null, 23 non-zero, 1 zero.

    Documents the exact composition called out in the spec ("~24 rows"):
    ``pre_nonnull = 24``, ``pre_nonnull_zero = 1`` (player with gp=14, a
    plausible real 0-steals season), ``pre_nonnull_nonzero = 23`` (the
    measured genuine-residual baseline).  If the anomaly ever grows, the
    parametrised ``test_fact_player_season_pre_cutoff_nonzero`` test will
    fail first; this test pins the absolute composition.
    """
    end_year = _end_year_sql("season_year")
    pre_nonnull = count(
        f"SELECT count(*) FROM unified_star.fact_player_season_stats "
        f"WHERE {end_year} < 1974 AND stl IS NOT NULL"
    )
    pre_nonnull_nonzero = count(
        f"SELECT count(*) FROM unified_star.fact_player_season_stats "
        f"WHERE {end_year} < 1974 AND stl IS NOT NULL AND stl <> 0"
    )
    pre_nonnull_zero = count(
        f"SELECT count(*) FROM unified_star.fact_player_season_stats "
        f"WHERE {end_year} < 1974 AND stl IS NOT NULL AND stl = 0"
    )
    # Measured 2026-06-29: pre_nonnull=24, pre_nonnull_nonzero=23, pre_nonnull_zero=1.
    assert pre_nonnull == 24
    assert pre_nonnull_nonzero == 23
    assert pre_nonnull_zero == 1
    # Cross-check: nonzero + zero == nonnull.
    assert pre_nonnull_nonzero + pre_nonnull_zero == pre_nonnull


def test_fact_blk_pre1974_anomaly_count_and_zero_split(count) -> None:
    """[Layer 1] Pre-1974 BLK anomaly: 26 non-null, 25 non-zero, 1 zero.

    Companion to the STL characterization: the same 1972-73 TOT rows also
    populate BLK (1 row from 1971-72 is BLK-only).  Measured values pin
    the genuine-residual baseline; if upstream changes the count, the
    parametrised pre-cutoff test fires first.
    """
    end_year = _end_year_sql("season_year")
    pre_nonnull = count(
        f"SELECT count(*) FROM unified_star.fact_player_season_stats "
        f"WHERE {end_year} < 1974 AND blk IS NOT NULL"
    )
    pre_nonnull_nonzero = count(
        f"SELECT count(*) FROM unified_star.fact_player_season_stats "
        f"WHERE {end_year} < 1974 AND blk IS NOT NULL AND blk <> 0"
    )
    pre_nonnull_zero = count(
        f"SELECT count(*) FROM unified_star.fact_player_season_stats "
        f"WHERE {end_year} < 1974 AND blk IS NOT NULL AND blk = 0"
    )
    # Measured 2026-06-29: pre_nonnull=26, pre_nonnull_nonzero=25, pre_nonnull_zero=1.
    assert pre_nonnull == 26
    assert pre_nonnull_nonzero == 25
    assert pre_nonnull_zero == 1
    assert pre_nonnull_nonzero + pre_nonnull_zero == pre_nonnull


# ---------------------------------------------------------------------------
# Documented 0-as-NULL artifact for the canonical view's GS column.
# Pre-1982 GS non-null rows: 332 non-zero (genuine, see V_CANONICAL_TARGETS)
# plus 119 zero-valued non-null rows.  We assert the 332 baseline via the
# parametrised test; here we only pin the artifact count so any future
# change is documented rather than silent.
# ---------------------------------------------------------------------------

def test_v_canonical_gs_pre1982_zero_artifact_is_documented(count) -> None:
    """[Layer 1] Pin the GS pre-1982 0-as-NULL artifact composition (119 rows).

    Out of 451 pre-1982 non-null GS rows, 119 are zero-valued.  Per the
    layer-1 contract, a real GS=0 stat recorded before GS was officially
    tracked should be NULL.  The 332 non-zero rows are genuine residual
    (BRef started tracking GS for some players from 1970-71) and are
    asserted by ``test_v_canonical_pre_cutoff_nonzero`` with a baseline of
    332.  This test pins the artifact count itself so any future ETL
    change (in either direction) is observed; if upstream eliminates the
    119 zero rows, lower the equality target here (and the comment below)
    to ratchet the pin.
    """
    observed = count(
        'SELECT count(*) FROM api.v_canonical_player_season_totals '
        'WHERE SEASON < 1982 AND "GS" IS NOT NULL AND "GS" = 0'
    )
    # Measured 2026-06-29: 119.  Exact-equality pin on the artifact count
    # (the parametrised ``<>0`` test is the regression guard for the 332
    # genuine-residual rows).  This pin fires if the artifact grows OR
    # shrinks, prompting an update to the comment and the target together.
    assert observed == 119
