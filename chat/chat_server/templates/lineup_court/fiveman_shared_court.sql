-- Template: lineup_court.fiveman_shared_court
-- PLAN §12 row 11: aggregate stats for a 5-man lineup that shared the
-- court in N games of a single season.
--
-- Spike outcome (PLAN §15)
-- ------------------------
-- **Shipped as a REAL template.**  Phase 6 verification showed:
--   * ``fact_lineup_player`` (canonical lineup roster) has a per-game
--     row per player-in-lineup, keyed by a
--     ``team_id-game_id-season_year`` ``group_id``.
--   * ``src_agg_lineup_efficiency`` (lineup stats source table) has
--     per-game totals — ``total_gp``, ``total_min``, ``avg_net_rating``,
--     ``total_plus_minus`` — keyed by the same ``group_id``.
--   * For the 2017-18 Warriors (Curry=201939, Thompson=202691,
--     Iguodala=2738, Durant=201142, Green=203110), 5 group_ids are
--     returned with combined ~861 minutes.  Sub-second execution.
--
-- The plan's source-backed table ``src_fact_lineup_stats`` is empty in
-- the current warehouse (verified Phase 6); ``src_agg_lineup_efficiency``
-- is the working replacement.  The template pins the exact two tables
-- it depends on so the lineage is unambiguous.
--
-- Parameter binding for the player list
-- -------------------------------------
-- DuckDB's Python client binds a Python list as a ``BIGINT[]`` when the
-- placeholder is typed: ``ANY($player_ids::BIGINT[])``.  This works for
-- boolean predicates; for ``SUM(CASE WHEN ... )`` aggregates the
-- ``list_contains`` helper is portable across both contexts.
--
-- Algorithm
-- ---------
-- 1. Filter ``fact_lineup_player`` to one season.
-- 2. Per ``group_id``, count the distinct requested player_ids present
--    in that group.  Keep groups where the count equals both
--    ``$player_count`` (entire unit is requested players) AND
--    ``COUNT(DISTINCT player_id)`` (the unit has exactly that size).
-- 3. Join ``src_agg_lineup_efficiency`` for net rating / minutes / gp.
--
-- Performance
-- -----------
-- ``season_year`` bounds the scan to a single season's
-- ``fact_lineup_player`` rows.  Sub-second locally.
WITH five_man_groups AS (
  SELECT
    fl.group_id,
    COUNT(DISTINCT fl.player_id) AS unit_size,
    SUM(CASE
      WHEN LIST_CONTAINS($player_ids::BIGINT [], fl.player_id)
        THEN 1
      ELSE 0
    END) AS matched_requested
  FROM fact_lineup_player AS fl
  WHERE fl.season_year = $season_year
  GROUP BY fl.group_id
  HAVING
    COUNT(DISTINCT fl.player_id) = $player_count
    AND SUM(CASE
      WHEN LIST_CONTAINS($player_ids::BIGINT [], fl.player_id)
        THEN 1
      ELSE 0
    END) = $player_count
)

SELECT
  ae.group_id,
  ae.total_gp,
  ae.total_min,
  ae.avg_net_rating,
  ae.total_plus_minus
FROM five_man_groups AS fmg
INNER JOIN src_agg_lineup_efficiency AS ae
  ON
    fmg.group_id = ae.group_id
    AND ae.season_year = $season_year
ORDER BY ae.total_min DESC
