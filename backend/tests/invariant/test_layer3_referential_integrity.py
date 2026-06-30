"""Layer 3 — Referential integrity and primary-key uniqueness.

These checks ensure every foreign key in the ``unified_star`` fact tables resolves
to a parent row in its dimension table, and that the dimension tables have no
duplicate primary keys. Together with Layer 1 (row-count sanity) and Layer 2
(value-domain / intra-row algebra) this completes the data-correctness
verification surface for the basketball-data-emporium snapshot.

Implementation: classic anti-join via ``LEFT JOIN ... WHERE dim.pk IS NULL`` so
that NULLs and missing rows are counted the same way (NULL never joins). All
checks run read-only against the DuckDB snapshot — no writes.

This module is intentionally **pinned to reality**: a check that observes > 0
orphans is asserted against a *measured* module-level constant with a
commentary explaining the cause. Targets are 0; lowering a constant here is how
an upstream ETL fix gets ratcheted in.
"""

from __future__ import annotations

import known_divergences as kd


# ---------------------------------------------------------------------------
# Measured baselines (Layer 3).
# Measured against data/nba.duckdb on 2026-06-29. Pin to reality; ratchet down
# as upstream ETL closes the gaps.
# ---------------------------------------------------------------------------

# fact_team_game_boxscore.game_id -> dim_game orphans: 10 rows across exactly
# 5 distinct game_ids, all from the 2024-25 season (00224xxxxxx prefix). The
# same 5 game_ids are missing from dim_game entirely, so they leak into both
# fact_team_game_boxscore (one row per side -> 10 rows) and
# fact_game_quarter_scores (4 quarters x 2 sides x 5 games -> 40 rows).
# Upstream fix: backfill dim_game for those 5 ids.
TGAME_GAME_ORPHAN_BASELINE: int = 10

# fact_game_quarter_scores.game_id -> dim_game orphans: 40 rows. Same 5
# missing game_ids as the tgame orphans above (4 periods x 2 teams x 5 games
# = 8 rows/game, verified: each game has periods 1-4 for both team sides).
QSCORES_GAME_ORPHAN_BASELINE: int = 40

# dim_team has a *composite* primary key (team_id, season_founded) — the
# snapshot encodes franchise evolution as multiple rows per team_id (each
# refounded/reactivated era). Testing team_id in isolation is therefore
# expected to surface duplicates: 22 distinct team_ids each have 2-6 rows
# (total 140 rows / 97 distinct team_ids). This is by design, not corruption.
DIM_TEAM_TEAM_ID_DUP_GROUPS_BASELINE: int = 22


# ---------------------------------------------------------------------------
# Foreign-key orphan checks
# ---------------------------------------------------------------------------


def test_pgame_player_id_orphan(count) -> None:
    """Layer 3: fact_player_game_boxscore.player_id -> dim_player orphans.

    Player rows whose player_id does not resolve in dim_player. Pinned to the
    kd genuine-residual baseline (regression guard); target is 0.
    """
    observed = count(
        "SELECT count(*) FROM unified_star.fact_player_game_boxscore f "
        "LEFT JOIN unified_star.dim_player d ON f.player_id = d.player_id "
        "WHERE d.player_id IS NULL"
    )
    assert observed <= kd.GENUINE_RESIDUAL_BASELINE["pgame_player_orphan"]


def test_pgame_game_id_orphan(count) -> None:
    """Layer 3: fact_player_game_boxscore.game_id -> dim_game orphans.

    Boxscore rows whose game_id does not resolve in dim_game. Spread across
    194 distinct pre-1996 era game_ids (the era before dim_game coverage is
    complete). Pinned to the kd genuine-residual baseline; target is 0.
    """
    observed = count(
        "SELECT count(*) FROM unified_star.fact_player_game_boxscore f "
        "LEFT JOIN unified_star.dim_game d ON f.game_id = d.game_id "
        "WHERE d.game_id IS NULL"
    )
    assert observed <= kd.GENUINE_RESIDUAL_BASELINE["pgame_game_orphan"]


def test_season_player_id_orphan_clean(count) -> None:
    """Layer 3: fact_player_season_stats.player_id -> dim_player orphans.

    Clean invariant — every player_id referenced from the season-stats fact
    must resolve in dim_player.
    """
    observed = count(
        "SELECT count(*) FROM unified_star.fact_player_season_stats f "
        "LEFT JOIN unified_star.dim_player d ON f.player_id = d.player_id "
        "WHERE d.player_id IS NULL"
    )
    assert observed == 0


def test_season_team_id_orphan_excluding_sentinel_clean(count) -> None:
    """Layer 3: fact_player_season_stats.team_id -> dim_team (non-sentinel).

    Clean invariant *after* excluding the SENTINEL_TEAM_ID rows. The sentinel
    represents "team unresolved / combined" (the server-side analogue of
    Basketball-Reference's TOT row) and is documented in known_divergences.
    """
    observed = count(
        "SELECT count(*) FROM unified_star.fact_player_season_stats f "
        "LEFT JOIN unified_star.dim_team d ON f.team_id = d.team_id "
        f"WHERE d.team_id IS NULL AND f.team_id <> {kd.SENTINEL_TEAM_ID}"
    )
    assert observed == 0


def test_season_team_id_orphans_are_all_sentinel(count) -> None:
    """Layer 3: confirms every season-stats team_id orphan IS the sentinel.

    Sanity companion to the previous test: proves that the bulk of season-
    stats team_id orphans (~31730 rows) are 100% explained by the
    SENTINEL_TEAM_ID row, not by genuine FK corruption. If this ever
    regresses, the previous test would still pass while real corruption
    hides behind the sentinel — this test catches that.
    """
    total_orphans = count(
        "SELECT count(*) FROM unified_star.fact_player_season_stats f "
        "LEFT JOIN unified_star.dim_team d ON f.team_id = d.team_id "
        "WHERE d.team_id IS NULL"
    )
    sentinel_orphans = count(
        "SELECT count(*) FROM unified_star.fact_player_season_stats f "
        "LEFT JOIN unified_star.dim_team d ON f.team_id = d.team_id "
        f"WHERE d.team_id IS NULL AND f.team_id = {kd.SENTINEL_TEAM_ID}"
    )
    assert total_orphans == sentinel_orphans
    # And confirm the sentinel set is non-empty — otherwise the test above
    # is vacuously true and would silently pass after a sentinel ETL removal.
    assert sentinel_orphans > 0


def test_tgame_team_id_orphan_clean(count) -> None:
    """Layer 3: fact_team_game_boxscore.team_id -> dim_team orphans.

    Clean invariant — every team_id referenced from the team-game boxscore
    fact must resolve in dim_team.
    """
    observed = count(
        "SELECT count(*) FROM unified_star.fact_team_game_boxscore f "
        "LEFT JOIN unified_star.dim_team d ON f.team_id = d.team_id "
        "WHERE d.team_id IS NULL"
    )
    assert observed == 0


def test_tgame_game_id_orphan(count) -> None:
    """Layer 3: fact_team_game_boxscore.game_id -> dim_game orphans.

    10 orphan rows across exactly 5 distinct 2024-25 game_ids that are
    entirely missing from dim_game (same 5 ids as the qscores orphan).
    Pinned to the measured baseline; target is 0 (backfill dim_game).
    """
    observed = count(
        "SELECT count(*) FROM unified_star.fact_team_game_boxscore f "
        "LEFT JOIN unified_star.dim_game d ON f.game_id = d.game_id "
        "WHERE d.game_id IS NULL"
    )
    assert observed <= TGAME_GAME_ORPHAN_BASELINE


def test_qscores_game_id_orphan(count) -> None:
    """Layer 3: fact_game_quarter_scores.game_id -> dim_game orphans.

    40 orphan rows — the same 5 missing 2024-25 game_ids as the tgame
    orphan, each contributing 1-4 quarter rows for both teams. Pinned to
    the measured baseline; target is 0 (backfill dim_game).
    """
    observed = count(
        "SELECT count(*) FROM unified_star.fact_game_quarter_scores f "
        "LEFT JOIN unified_star.dim_game d ON f.game_id = d.game_id "
        "WHERE d.game_id IS NULL"
    )
    assert observed <= QSCORES_GAME_ORPHAN_BASELINE


def test_qscores_team_id_orphan_clean(count) -> None:
    """Layer 3: fact_game_quarter_scores.team_id -> dim_team orphans.

    Clean invariant — every team_id referenced from the quarter-scores
    fact must resolve in dim_team.
    """
    observed = count(
        "SELECT count(*) FROM unified_star.fact_game_quarter_scores f "
        "LEFT JOIN unified_star.dim_team d ON f.team_id = d.team_id "
        "WHERE d.team_id IS NULL"
    )
    assert observed == 0


# ---------------------------------------------------------------------------
# Primary-key uniqueness checks
# ---------------------------------------------------------------------------


def test_dim_player_player_id_pk_unique(count) -> None:
    """Layer 3: dim_player.player_id primary key uniqueness.

    Clean invariant — count of player_ids that appear more than once must be
    zero. dim_player uses player_id as a single-column primary key.
    """
    observed = count(
        "SELECT count(*) FROM ("
        "  SELECT player_id FROM unified_star.dim_player "
        "  GROUP BY player_id HAVING count(*) > 1"
        ")"
    )
    assert observed == 0


def test_dim_team_team_id_pk_unique(count) -> None:
    """Layer 3: dim_team.team_id primary key uniqueness.

    dim_team's *real* primary key is the composite (team_id, season_founded)
    — the snapshot encodes franchise evolution (refounded / reactivated eras)
    as multiple rows per team_id. Therefore 22 distinct team_ids each have
    2-6 rows (140 total rows / 97 distinct team_ids), which is by design and
    NOT corruption. Pinned to the measured baseline; target is 0 *only* if
    the schema is changed to (team_id) being a singleton.
    """
    observed = count(
        "SELECT count(*) FROM ("
        "  SELECT team_id FROM unified_star.dim_team "
        "  GROUP BY team_id HAVING count(*) > 1"
        ")"
    )
    assert observed <= DIM_TEAM_TEAM_ID_DUP_GROUPS_BASELINE


def test_dim_game_game_id_pk_unique(count) -> None:
    """Layer 3: dim_game.game_id primary key uniqueness.

    Clean invariant — count of game_ids that appear more than once must be
    zero. dim_game uses game_id as a single-column primary key.
    """
    observed = count(
        "SELECT count(*) FROM ("
        "  SELECT game_id FROM unified_star.dim_game "
        "  GROUP BY game_id HAVING count(*) > 1"
        ")"
    )
    assert observed == 0
