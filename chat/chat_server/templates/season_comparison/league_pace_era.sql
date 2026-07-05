-- Template: season_comparison.league_pace_era
-- Params:
--   season_a    (str):            Canonical season_year, e.g. '1998-99'.
--   season_b    (str):            Canonical season_year, e.g. '2022-23'.
--   season_type (str, 'Regular'): Regular | Playoffs | Cup.
--
-- Average pace per 100 possessions across every player-game in each
-- season. Caveat: this is a player-game average, not a true team-game
-- league aggregate. Use as a proxy for the league pace era.
SELECT
  season_year,
  ROUND(AVG(pace), 3) AS avg_pace,
  COUNT(*) AS sample_games
FROM fact_player_game_advanced
WHERE
  season_year IN ($season_a, $season_b)
  AND season_type = $season_type
  AND pace IS NOT NULL
GROUP BY season_year
ORDER BY season_year
