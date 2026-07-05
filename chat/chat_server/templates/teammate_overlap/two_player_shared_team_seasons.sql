-- Template: teammate_overlap.two_player_shared_team_seasons
-- Params:
--   player_a_id (int): dim_player.player_id for player A
--   player_b_id (int): dim_player.player_id for player B
--   season_type (str, default 'Regular'): Regular | Playoffs | Cup
-- Source-backed notes:
--   `bridge_player_team_season` does NOT exist in the warehouse (verified
--   at load time). Team-season membership is derived from
--   `fact_player_game_box` via DISTINCT (team_id, season_year). Rows with
--   NULL `team_id` (All-Star games / league-wide events) are filtered out
--   because they would otherwise produce a phantom shared "team" of
--   opposing All-Stars.
WITH player_a_team_seasons AS (
  SELECT DISTINCT
    team_id,
    season_year
  FROM fact_player_game_box
  WHERE
    player_id = $player_a_id
    AND season_type = $season_type
    AND team_id IS NOT NULL
),

player_b_team_seasons AS (
  SELECT DISTINCT
    team_id,
    season_year
  FROM fact_player_game_box
  WHERE
    player_id = $player_b_id
    AND season_type = $season_type
    AND team_id IS NOT NULL
),

shared_team_seasons AS (
  SELECT
    team_id,
    season_year
  FROM player_a_team_seasons
  INTERSECT
  SELECT
    team_id,
    season_year
  FROM player_b_team_seasons
),

teammates AS (
  SELECT DISTINCT
    fpgb.player_id,
    fpgb.team_id,
    fpgb.season_year
  FROM fact_player_game_box AS fpgb
  WHERE
    fpgb.season_type = $season_type
    AND (fpgb.team_id, fpgb.season_year) IN (
      SELECT
        team_id,
        season_year
      FROM shared_team_seasons
    )
    AND fpgb.player_id NOT IN ($player_a_id, $player_b_id)
)

SELECT
  dp.player_id,
  dp.full_name,
  dp.is_active,
  t.team_id,
  t.season_year
FROM teammates AS t
INNER JOIN dim_player AS dp ON t.player_id = dp.player_id
ORDER BY t.season_year DESC, dp.full_name ASC
