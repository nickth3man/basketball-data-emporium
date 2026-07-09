-- Template: season_comparison.player_team_split
-- Params:
--   player_id   (int):            dim_player.player_id.
--   season_year (str):            Canonical season_year.
--   season_type (str, 'Regular'): Regular | Playoffs | Cup.
--
-- Evidence query for the NOT-ANSWERABLE case:
-- returns the mart_player_season rows that DO exist for the player in
-- the given season. In the canonical warehouse, this will be exactly
-- one team partition (PHI for Harden 2022-23) — which is the proof that
-- the trade event isn't captured.
SELECT
  mps.player_id,
  dp.full_name,
  mps.team_id,
  mps.team_abbreviation,
  mps.season_year,
  mps.season_type,
  mps.gp,
  mps.avg_pts
FROM mart_player_season AS mps
INNER JOIN dim_player AS dp
  ON mps.player_id = dp.player_id
WHERE
  mps.player_id = $player_id
  AND mps.season_year = $season_year
  AND mps.season_type = $season_type
ORDER BY mps.team_id, mps.season_type
