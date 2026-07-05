-- Template: player_game_conditional.milestone_age
-- Params:
--   season_type (str, 'Regular'): Regular | Playoffs | Cup.
--   top_n       (int, 1):         How many rows to return (youngest first).
--
-- Finds the youngest player to record a triple-double (PTS, REB, AST >= 10)
-- by joining game box scores to the player's birth date and computing age
-- in days at the time of the game.
SELECT
  fb.player_id,
  dp.full_name,
  CAST(fb.game_date AS VARCHAR) AS game_date,
  DATE_DIFF('day', dp.birth_date, fb.game_date) AS age_in_days,
  fb.pts,
  fb.reb,
  fb.ast
FROM fact_player_game_box AS fb
INNER JOIN dim_player AS dp
  ON fb.player_id = dp.player_id
WHERE
  fb.pts >= 10
  AND fb.reb >= 10
  AND fb.ast >= 10
  AND fb.season_type = $season_type
  AND dp.birth_date IS NOT NULL
ORDER BY age_in_days ASC, fb.game_date ASC
LIMIT $top_n
