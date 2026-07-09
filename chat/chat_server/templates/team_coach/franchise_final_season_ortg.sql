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
-- Step 3: team offensive rating is read directly from
-- src_fact_bref_team_season_summary.o_rtg -- the Basketball-Reference
-- team-season summary row that mirrors the per-team BBR page (e.g. the
-- 2007-08 Seattle SuperSonics page shows ORtg = 100.5). We join on
-- team_id and on season_end_year = CAST(SUBSTR(season_year,1,4) AS INT)+1,
-- and filter to Regular-season rows (playoffs = false). This is the
-- canonical team-level ORtg, not a player-game reconstruction -- prior
-- AVG(off_rating) over fact_player_game_advanced was an approximation
-- that disagreed with the BBR team page.
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
    s.team_id,
    s.o_rtg AS team_off_rating
  FROM src_fact_bref_team_season_summary AS s
  INNER JOIN resolved AS r
    ON
      s.team_id = r.team_id
      AND s.season_end_year = CAST(SUBSTR(r.season_year, 1, 4) AS INTEGER) + 1
  WHERE
    s.playoffs = false
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