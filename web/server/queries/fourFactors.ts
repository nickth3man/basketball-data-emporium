import { queryObjects } from "../db.ts";
import { DIM_GAME_SEASON_GUARD_SQL } from "./shared.ts";
import type { Row } from "./shared.ts";

// ---------------------------------------------------------------------------
// Four Factors dashboard
//
// src_fact_box_score_four_factors_team has two team-level rows per game from
// 1996-97 onward, each carrying the team's own AND its opponent's four
// factors for that game. Season aggregates are plain per-game averages —
// close to but not identical to possession-weighted season figures (GSW
// 2015-16 eFG%: .564 here vs .563 on BBR). Season/season_type/wins now come
// from dim_game instead of parsing the game id.
// ---------------------------------------------------------------------------

// dim_team has known duplicate is_current=true rows for several relocated
// franchises (e.g. both "SEA"/Seattle SuperSonics and "OKC"/Oklahoma City
// Thunder rows marked current for team_id 1610612760) — dim_team_era does
// not have this problem (verified: zero teams with >1 current row), so it
// is the reliable source for "this team's current name" lookups.
const TEAM_NAMES_CTE = `team_names AS (
    SELECT team_id, abbreviation, nickname AS full_name
    FROM dim_team_era
    WHERE is_current
  )`;

// src_fact_box_score_four_factors_team has 2-3 duplicate rows per
// (game_id, team_id) that differ only in team_city (a franchise-name-history
// join fanout from ingest) — every numeric stat is identical across the
// duplicates, so DISTINCT on the stat columns collapses them to one row.
const FOUR_FACTORS_DEDUP_CTE = `ff_dedup AS (
    SELECT DISTINCT
      game_id, team_id,
      effective_field_goal_percentage, team_turnover_percentage,
      offensive_rebound_percentage, free_throw_attempt_rate,
      opp_effective_field_goal_percentage, opp_team_turnover_percentage,
      opp_offensive_rebound_percentage, opp_free_throw_attempt_rate
    FROM src_fact_box_score_four_factors_team
  )`;

export async function listFourFactorsSeasons(): Promise<string[]> {
  const rows = await queryObjects<{ season_year: string }>(
    `SELECT DISTINCT g.season_year
     FROM src_fact_box_score_four_factors_team f
     JOIN dim_game g ON g.game_id = f.game_id
     WHERE g.season_type = 'Regular' AND (${DIM_GAME_SEASON_GUARD_SQL})
     ORDER BY season_year DESC`,
  );
  return rows.map((r) => r.season_year);
}

export async function getFourFactorsTeams(season: string): Promise<Row[]> {
  return queryObjects(
    `WITH ${TEAM_NAMES_CTE},
     ${FOUR_FACTORS_DEDUP_CTE},
     ff AS (
       SELECT
         f.team_id,
         COUNT(*)::INTEGER AS gp,
         AVG(f.effective_field_goal_percentage) AS efg_pct,
         AVG(f.team_turnover_percentage) AS tov_pct,
         AVG(f.offensive_rebound_percentage) AS oreb_pct,
         AVG(f.free_throw_attempt_rate) AS ft_rate,
         AVG(f.opp_effective_field_goal_percentage) AS opp_efg_pct,
         AVG(f.opp_team_turnover_percentage) AS opp_tov_pct,
         AVG(f.opp_offensive_rebound_percentage) AS opp_oreb_pct,
         AVG(f.opp_free_throw_attempt_rate) AS opp_ft_rate
       FROM ff_dedup f
       JOIN dim_game g ON g.game_id = f.game_id
       WHERE g.season_type = 'Regular' AND g.season_year = ?
       GROUP BY f.team_id
     ),
     wins AS (
       SELECT team_id, SUM(win)::INTEGER AS wins
       FROM (
         SELECT home_team_id AS team_id, CASE WHEN winner_team_id = home_team_id THEN 1 ELSE 0 END AS win
         FROM dim_game
         WHERE season_type = 'Regular' AND season_year = ?
         UNION ALL
         SELECT away_team_id, CASE WHEN winner_team_id = away_team_id THEN 1 ELSE 0 END
         FROM dim_game
         WHERE season_type = 'Regular' AND season_year = ?
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
    `WITH ${FOUR_FACTORS_DEDUP_CTE}
     SELECT
       g.season_year,
       (COUNT(*) / 2)::INTEGER AS games,
       AVG(f.effective_field_goal_percentage) AS efg_pct,
       AVG(f.team_turnover_percentage) AS tov_pct,
       AVG(f.offensive_rebound_percentage) AS oreb_pct,
       AVG(f.free_throw_attempt_rate) AS ft_rate
     FROM ff_dedup f
     JOIN dim_game g ON g.game_id = f.game_id
     WHERE g.season_type = 'Regular' AND (${DIM_GAME_SEASON_GUARD_SQL})
     GROUP BY g.season_year
     ORDER BY g.season_year`,
  );
}

export async function getGameFourFactors(gameId: string): Promise<Row[]> {
  return queryObjects(
    `WITH ${TEAM_NAMES_CTE},
     ${FOUR_FACTORS_DEDUP_CTE}
     SELECT
       f.team_id,
       t.abbreviation AS team_abbreviation,
       t.full_name AS team_name,
       CASE
         WHEN g.home_team_id = f.team_id THEN 'Home'
         WHEN g.away_team_id = f.team_id THEN 'Away'
       END AS side,
       f.effective_field_goal_percentage AS efg_pct,
       f.team_turnover_percentage AS tov_pct,
       f.offensive_rebound_percentage AS oreb_pct,
       f.free_throw_attempt_rate AS ft_rate
     FROM ff_dedup f
     LEFT JOIN dim_game g ON g.game_id = f.game_id
     LEFT JOIN team_names t ON t.team_id = f.team_id
     WHERE f.game_id = ?
     ORDER BY side NULLS LAST`,
    [gameId],
  );
}
