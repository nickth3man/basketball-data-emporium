-- Template: player_game_conditional.career_conditional_aggregate
-- Params:
--   season_type (str, 'Regular'): Regular | Playoffs | Cup.
--   top_n       (int, 5):         Rows to return.
--
-- Career aggregation of assists in games where the player scored zero
-- points. SUM(ast) over (fact_player_game_box WHERE pts = 0) per player,
-- ordered DESC.
SELECT
  fb.player_id,
  dp.full_name,
  COUNT(*) AS games_scored_zero,
  CAST(SUM(fb.ast) AS BIGINT) AS total_ast
FROM fact_player_game_box AS fb
INNER JOIN dim_player AS dp
  ON fb.player_id = dp.player_id
WHERE
  fb.season_type = $season_type
  AND fb.pts = 0
GROUP BY fb.player_id, dp.full_name
ORDER BY total_ast DESC
LIMIT $top_n
