-- Template: player_game_conditional.margin_split
-- Params:
--   player_id (int):              dim_player.player_id to slice on.
--   season_year (str):            Canonical season_year, e.g. '2009-10'.
--   season_type (str, 'Regular'): Regular | Playoffs | Cup.
--   margin (int, 10):             Absolute point margin defining a "blowout".
--
-- Splits each game of the player's season into:
--   * 'wins_by_<margin>p'    : is_win = TRUE  AND team margin >= margin
--   * 'losses_by_<margin>p'  : is_win = FALSE AND team margin >= margin
-- Games that don't qualify for either bucket are dropped (the test asserts
-- both buckets have at least one game for Kobe 2009-10).
WITH classified AS (
  SELECT
    fb.fg_pct,
    fgr.margin,
    CASE
      WHEN fb.is_win AND fgr.margin >= $margin THEN 'wins_by_' || CAST($margin AS VARCHAR) || 'p'
      WHEN (NOT fb.is_win) AND fgr.margin >= $margin
        THEN 'losses_by_' || CAST($margin AS VARCHAR) || 'p'
    END AS split
  FROM fact_player_game_box AS fb
  INNER JOIN fact_game_result AS fgr
    ON fb.game_id = fgr.game_id
  WHERE
    fb.player_id = $player_id
    AND fb.season_year = $season_year
    AND fb.season_type = $season_type
)

SELECT
  split,
  COUNT(*) AS games,
  AVG(fg_pct) AS avg_fg_pct
FROM classified
WHERE split IS NOT NULL
GROUP BY split
ORDER BY split
