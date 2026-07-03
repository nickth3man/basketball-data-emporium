import { queryObjects } from "../db.ts";
import { SEASON_FROM_GAME_ID_SQL } from "./shared.ts";
import type { Row } from "./shared.ts";

// ---------------------------------------------------------------------------
// Four Factors dashboard
//
// fact_box_score_four_factors has two team-level rows per game (player_id
// is always 0) from 1996-97 onward, each carrying the team's own AND its
// opponent's four factors for that game. Season aggregates are plain
// per-game averages — close to but not identical to possession-weighted
// season figures (GSW 2015-16 eFG%: .564 here vs .563 on BBR). The season
// label is derived from the game-id's embedded year (chars 4-5), the same
// trick the betting explorer uses; regular-season games have the '002'
// prefix. Win totals come from the `game` fact table and are NULL for the
// seasons it doesn't carry.
// ---------------------------------------------------------------------------

const TEAM_NAMES_CTE = `team_names AS (
    SELECT team_id, any_value(abbreviation) AS abbreviation, any_value(full_name) AS full_name
    FROM dim_team
    GROUP BY team_id
  )`;

export async function listFourFactorsSeasons(): Promise<string[]> {
  const rows = await queryObjects<{ season_year: string }>(
    `SELECT DISTINCT ${SEASON_FROM_GAME_ID_SQL} AS season_year
     FROM fact_box_score_four_factors
     WHERE substr(game_id, 1, 3) = '002'
     ORDER BY season_year DESC`,
  );
  return rows.map((r) => r.season_year);
}

export async function getFourFactorsTeams(season: string): Promise<Row[]> {
  return queryObjects(
    `WITH ${TEAM_NAMES_CTE},
     ff AS (
       SELECT
         team_id,
         COUNT(*)::INTEGER AS gp,
         AVG(effective_field_goal_percentage) AS efg_pct,
         AVG(team_turnover_percentage) AS tov_pct,
         AVG(offensive_rebound_percentage) AS oreb_pct,
         AVG(free_throw_attempt_rate) AS ft_rate,
         AVG(opp_effective_field_goal_percentage) AS opp_efg_pct,
         AVG(opp_team_turnover_percentage) AS opp_tov_pct,
         AVG(opp_offensive_rebound_percentage) AS opp_oreb_pct,
         AVG(opp_free_throw_attempt_rate) AS opp_ft_rate
       FROM fact_box_score_four_factors
       WHERE substr(game_id, 1, 3) = '002' AND ${SEASON_FROM_GAME_ID_SQL} = ?
       GROUP BY team_id
     ),
     wins AS (
       SELECT team_id, SUM(win)::INTEGER AS wins
       FROM (
         SELECT team_id_home AS team_id,
                CASE WHEN any_value(wl_home) = 'W' THEN 1 ELSE 0 END AS win
         FROM game
         WHERE substr(game_id, 1, 3) = '002' AND ${SEASON_FROM_GAME_ID_SQL} = ?
         GROUP BY game_id, team_id_home
         UNION ALL
         SELECT team_id_away,
                CASE WHEN any_value(wl_home) = 'L' THEN 1 ELSE 0 END
         FROM game
         WHERE substr(game_id, 1, 3) = '002' AND ${SEASON_FROM_GAME_ID_SQL} = ?
         GROUP BY game_id, team_id_away
       )
       GROUP BY team_id
     )
     SELECT
       f.team_id,
       t.abbreviation AS team_abbreviation,
       t.full_name AS team_name,
       f.gp,
       w.wins,
       f.efg_pct,
       RANK() OVER (ORDER BY f.efg_pct DESC)::INTEGER AS efg_rank,
       f.tov_pct,
       RANK() OVER (ORDER BY f.tov_pct ASC)::INTEGER AS tov_rank,
       f.oreb_pct,
       RANK() OVER (ORDER BY f.oreb_pct DESC)::INTEGER AS oreb_rank,
       f.ft_rate,
       RANK() OVER (ORDER BY f.ft_rate DESC)::INTEGER AS ft_rate_rank,
       f.opp_efg_pct,
       RANK() OVER (ORDER BY f.opp_efg_pct ASC)::INTEGER AS opp_efg_rank,
       f.opp_tov_pct,
       RANK() OVER (ORDER BY f.opp_tov_pct DESC)::INTEGER AS opp_tov_rank,
       f.opp_oreb_pct,
       RANK() OVER (ORDER BY f.opp_oreb_pct ASC)::INTEGER AS opp_oreb_rank,
       f.opp_ft_rate,
       RANK() OVER (ORDER BY f.opp_ft_rate ASC)::INTEGER AS opp_ft_rate_rank
     FROM ff f
     JOIN team_names t USING (team_id)
     LEFT JOIN wins w USING (team_id)
     ORDER BY w.wins DESC NULLS LAST, f.efg_pct DESC`,
    [season, season, season],
  );
}

export async function getFourFactorsLeague(): Promise<Row[]> {
  return queryObjects(
    `SELECT
       ${SEASON_FROM_GAME_ID_SQL} AS season_year,
       (COUNT(*) / 2)::INTEGER AS games,
       AVG(effective_field_goal_percentage) AS efg_pct,
       AVG(team_turnover_percentage) AS tov_pct,
       AVG(offensive_rebound_percentage) AS oreb_pct,
       AVG(free_throw_attempt_rate) AS ft_rate
     FROM fact_box_score_four_factors
     WHERE substr(game_id, 1, 3) = '002'
     GROUP BY season_year
     ORDER BY season_year`,
  );
}

export async function getGameFourFactors(gameId: string): Promise<Row[]> {
  return queryObjects(
    `WITH ${TEAM_NAMES_CTE},
     game_sides AS (
       SELECT game_id, any_value(team_id_home) AS team_id_home, any_value(team_id_away) AS team_id_away
       FROM game
       WHERE game_id = ?
       GROUP BY game_id
     )
     SELECT
       f.team_id,
       t.abbreviation AS team_abbreviation,
       t.full_name AS team_name,
       CASE
         WHEN g.team_id_home = f.team_id THEN 'Home'
         WHEN g.team_id_away = f.team_id THEN 'Away'
       END AS side,
       f.effective_field_goal_percentage AS efg_pct,
       f.team_turnover_percentage AS tov_pct,
       f.offensive_rebound_percentage AS oreb_pct,
       f.free_throw_attempt_rate AS ft_rate
     FROM fact_box_score_four_factors f
     LEFT JOIN game_sides g ON g.game_id = f.game_id
     LEFT JOIN team_names t ON t.team_id = f.team_id
     WHERE f.game_id = ?
     ORDER BY side NULLS LAST`,
    [gameId, gameId],
  );
}
