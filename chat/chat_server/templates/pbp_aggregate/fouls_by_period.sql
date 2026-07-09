-- Template: pbp_aggregate.fouls_by_period
-- Most offensive fouls committed in a given period.
--
-- Offensive-foul definition
-- -------------------------
-- We restrict to ``action_type = 'Foul'`` AND
-- ``LOWER(sub_type) IN ('offensive', 'offensive charge')``.  ``LOWER()``
-- is required because the PBP source alternates between Pascal-case
-- ("Foul"/"Offensive") and lowercase ("foul"/"offensive") across
-- seasons — verified during Phase 6 implementation.  This captures both
-- the canonical offensive foul and the offensive-charge variant.
--
-- ``Turnover|Foul`` is NOT included: those are take-fouls (e.g. to stop
-- the clock), not offensive fouls committed by a player.  Counting them
-- here would inflate the totals without matching the benchmark
-- question's intent.
--
-- Performance
-- -----------
-- The query is bounded by ``season_year`` AND ``period`` filters —
-- one season's period N is a few hundred thousand rows at most.  The
-- pre-aggregation CTE collapses to one row per player before joining
-- dim_player, so the final result is bounded by ``top_n``.
WITH offensive_fouls AS (
  SELECT
    pbp.player_id,
    COUNT(*) AS offensive_fouls
  FROM fact_pbp_event AS pbp
  INNER JOIN dim_game AS g ON pbp.game_id = g.game_id
  WHERE
    g.season_year = $season_year
    AND pbp.period = $period
    AND LOWER(pbp.action_type) = 'foul'
    AND LOWER(pbp.sub_type) IN ('offensive', 'offensive charge')
    AND pbp.player_id IS NOT NULL
  GROUP BY pbp.player_id
)

SELECT
  of.player_id,
  dp.full_name,
  of.offensive_fouls
FROM offensive_fouls AS of
INNER JOIN dim_player AS dp ON of.player_id = dp.player_id
ORDER BY of.offensive_fouls DESC, dp.full_name ASC
LIMIT $top_n
