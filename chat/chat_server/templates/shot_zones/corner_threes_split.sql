-- Template: shot_zones.corner_threes_split
-- Params:
--   player_id   (int):  dim_player.player_id of the shooter.
--   season_year (str):  e.g. '2016-17'
--   season_type (str, default 'Regular'): Regular | Playoffs | Cup
-- Source-backed notes:
--   `fact_shot.shot_zone_basic` does NOT carry the "Left Corner 3" /
--   "Right Corner 3" labels — those are absent from the warehouse
--   (verified via SELECT DISTINCT shot_zone_basic). Corner threes are
--   identified by `shot_zone_basic = 'Above the Break 3'` plus an
--   `loc_x` threshold of |loc_x| >= 220 (NBA Stats API convention).
--   The CASE yields canonical "Left Corner 3" / "Right Corner 3" labels
--   so the composer can phrase the comparison naturally. The HAVING
--   filter restricts the result to the two corner zones so the
--   comparison is the only thing the answer table shows.
SELECT
  CASE
    WHEN loc_x >= 220 THEN 'Right Corner 3'
    WHEN loc_x <= -220 THEN 'Left Corner 3'
  END AS shot_zone,
  COUNT(*) AS attempts,
  SUM(shot_made_flag) AS makes,
  ROUND(AVG(shot_made_flag), 4) AS pct
FROM fact_shot
WHERE
  player_id = $player_id
  AND season_year = $season_year
  AND season_type = $season_type
  AND shot_zone_basic = 'Above the Break 3'
  AND (loc_x >= 220 OR loc_x <= -220)
GROUP BY shot_zone
HAVING shot_zone IS NOT NULL
ORDER BY shot_zone
