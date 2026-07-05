-- Template: team_coach.franchise_final_season_ortg
-- Params:
--   team_id      (int):           dim_team.team_id (the franchise).
--   final_season (str | NULL):    Override the derived final season.
--
-- Step 1: derive the franchise's final season from dim_team_era. The
-- "non-current" era row (valid_to_year < 9999) yields the season_year
-- '(valid_to_year-1)-(valid_to_year%100)'. Callers can override via
-- $final_season.
--
-- Step 2: look up the head coach for that team+season in fact_coach_season.
--
-- Step 3: compute team off_rating as AVG(off_rating) over every Regular
-- player-game for the team+season in fact_player_game_advanced. (A true
-- possession-weighted ORtg would need SUM(pts)/SUM(poss)*100; the
-- advanced box carries the per-100-possession ORtg already, so the
-- straight average is equivalent.)
WITH final_era AS (
  SELECT
    team_id,
    abbreviation,
    LPAD(CAST(valid_to_year - 1 AS VARCHAR), 4, '0')
    || '-' || RIGHT(LPAD(CAST(valid_to_year AS VARCHAR), 4, '0'), 2) AS era_season_year
  FROM dim_team_era
  WHERE
    team_id = $team_id
    AND valid_to_year < 9999
  ORDER BY valid_to_year DESC
  LIMIT 1
),

resolved AS (
  SELECT
    $team_id AS team_id,
    COALESCE($final_season, final_era.era_season_year) AS season_year,
    final_era.abbreviation AS team_abbreviation
  FROM final_era
),

team_ortg AS (
  SELECT
    fpa.team_id,
    AVG(fpa.off_rating) AS team_off_rating
  FROM fact_player_game_advanced AS fpa
  INNER JOIN resolved AS r
    ON
      fpa.team_id = r.team_id
      AND fpa.season_year = r.season_year
  WHERE
    fpa.season_type = 'Regular'
    AND fpa.off_rating IS NOT NULL
  GROUP BY fpa.team_id
)

SELECT
  fcs.coach_name,
  r.season_year,
  ROUND(o.team_off_rating, 2) AS team_off_rating,
  r.team_abbreviation
FROM resolved AS r
LEFT JOIN fact_coach_season AS fcs
  ON
    r.team_id = fcs.team_id
    AND r.season_year = fcs.season_year
LEFT JOIN team_ortg AS o
  ON r.team_id = o.team_id
ORDER BY fcs.coach_name
