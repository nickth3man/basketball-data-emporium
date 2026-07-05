-- Template: player_game_conditional.rare_stat_line
-- Params:
--   season_type (str, 'Regular'): Regular | Playoffs | Cup.
--   min_stat    (int, 10):        Threshold for each of PTS/REB/AST/BLK.
--
-- Lists every game in the requested season_type where a player hit the
-- threshold in each of PTS, REB, AST, and BLK. Default min_stat=10
-- matches the canonical quadruple-double definition (PLAN §12 row 17).
SELECT
  fb.player_id,
  dp.full_name,
  CAST(fb.game_date AS VARCHAR) AS game_date,
  fb.pts,
  fb.reb,
  fb.ast,
  fb.blk,
  fb.season_year
FROM fact_player_game_box AS fb
INNER JOIN dim_player AS dp
  ON fb.player_id = dp.player_id
WHERE
  fb.pts >= $min_stat
  AND fb.reb >= $min_stat
  AND fb.ast >= $min_stat
  AND fb.blk >= $min_stat
  AND fb.season_type = $season_type
ORDER BY fb.game_date, dp.full_name
