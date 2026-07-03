import { queryObjects } from "../db.ts";
import { PLAYER_SEASON_STATS_CTE, type Row } from "./shared.ts";

// ---------------------------------------------------------------------------
// League Leaders
//
// fact_season_leader is a season-level aggregate (one row per season/split/
// stat_key — no player_id); the row-per-leader table is agg_league_leaders,
// which already carries player_id, season_year, season_type, gp, the
// per-game rate stats (avg_pts, avg_reb, ...) and the league ranks
// (pts_rank, reb_rank, ...). All-Time is recomputed from fact_player_career
// (NBA Regular Season, summed per player_id) rather than reading
// agg_all_time_leaders — that table's totals are inflated/incorrect vs BBR
// (e.g. LeBron shows 62,564 instead of the BBR-citable ~42k+).
// ---------------------------------------------------------------------------

export async function listLeaderSeasons(): Promise<string[]> {
  const rows = await queryObjects<{ season_year: string }>(
    `SELECT DISTINCT season_year
     FROM agg_league_leaders
     WHERE season_type = 'Regular'
     ORDER BY season_year DESC`,
  );
  return rows.map((r) => r.season_year);
}

export async function listLeaderStatKeys(): Promise<string[]> {
  const rows = await queryObjects<{ stat_key: string }>(
    `SELECT DISTINCT stat_key
     FROM (
       SELECT 'pts' AS stat_key FROM agg_league_leaders WHERE avg_pts IS NOT NULL
       UNION ALL SELECT 'reb' FROM agg_league_leaders WHERE avg_reb IS NOT NULL
       UNION ALL SELECT 'ast' FROM agg_league_leaders WHERE avg_ast IS NOT NULL
       UNION ALL SELECT 'stl' FROM agg_league_leaders WHERE avg_stl IS NOT NULL
       UNION ALL SELECT 'blk' FROM agg_league_leaders WHERE avg_blk IS NOT NULL
     )
     ORDER BY stat_key`,
  );
  return rows.map((r) => r.stat_key);
}

const LEADER_STAT_COLUMNS: Record<string, { avg: string; rank: string }> = {
  pts: { avg: "avg_pts", rank: "pts_rank" },
  reb: { avg: "avg_reb", rank: "reb_rank" },
  ast: { avg: "avg_ast", rank: "ast_rank" },
  stl: { avg: "avg_stl", rank: "stl_rank" },
  blk: { avg: "avg_blk", rank: "blk_rank" },
};

export async function getSeasonLeaders(
  season: string,
  statKey: string,
  limit = 25,
): Promise<Row[]> {
  // Whitelist stat keys — agg_league_leaders stores each as a separate
  // rank/avg column, so we can't parameterise the column name. Falling
  // back to 'pts' on unknown input keeps the endpoint resilient.
  const cols = LEADER_STAT_COLUMNS[statKey] ?? LEADER_STAT_COLUMNS.pts;
  return queryObjects(
    `WITH ${PLAYER_SEASON_STATS_CTE},
     leader_team AS (
       SELECT
         player_id,
         season_year,
         COUNT(DISTINCT team_id) AS team_count,
         MIN(team_id) AS team_id,
         MAX(source_team_abbreviation) AS source_team_abbreviation
       FROM player_season_stats
       WHERE season_type = 'Regular'
       GROUP BY player_id, season_year
     ),
     leader_team_display AS (
       SELECT
         lt.player_id,
         lt.season_year,
         CASE
           WHEN lt.team_count > 1 THEN 'TOT'
           ELSE COALESCE(th.abbreviation, lt.source_team_abbreviation)
         END AS team_abbreviation
       FROM leader_team lt
       LEFT JOIN dim_team_history th
         ON th.team_id = lt.team_id
         AND left(lt.season_year, 4) >= th.valid_from
         AND (th.valid_to IS NULL OR left(lt.season_year, 4) < th.valid_to)
       QUALIFY ROW_NUMBER() OVER (
         PARTITION BY lt.player_id, lt.season_year
         ORDER BY
           CASE WHEN th.team_id IS NOT NULL THEN 0 ELSE 1 END,
           th.valid_from DESC NULLS LAST
       ) = 1
     )
     SELECT
       l.player_id,
       p.full_name,
       l.season_year,
       l.season_type,
       l.gp,
       l.${cols.avg} AS stat_value,
       l.${cols.rank} AS stat_rank,
       ltd.team_abbreviation
     FROM agg_league_leaders l
     JOIN dim_player p ON p.player_id = l.player_id AND p.is_current
     LEFT JOIN leader_team_display ltd
       ON ltd.player_id = l.player_id
       AND ltd.season_year = l.season_year
     WHERE l.season_year = ?
       AND l.season_type = 'Regular'
       AND l.${cols.rank} IS NOT NULL
     ORDER BY l.${cols.rank} ASC
     LIMIT ?`,
    [season, limit],
  );
}

export async function getAllTimeLeaders(
  statKey: "pts" | "ast" | "reb" = "pts",
  limit = 50,
): Promise<Row[]> {
  // Recompute from fact_player_career (NBA, Regular Season only — the
  // schema's `league_id` value for NBA is the literal string 'NBA', and
  // career_type for regular-season rows is 'Regular Season'). Excludes
  // Playoffs/Cup so totals are BBR-comparable. All-time ranks are
  // computed per-stat; the chosen `statKey` determines the ordering and
  // which column is the "value" surfaced to the client.
  return queryObjects(
    `WITH career_totals AS (
       SELECT
         player_id,
         SUM(pts)::BIGINT AS pts,
         SUM(ast)::BIGINT AS ast,
         SUM(reb)::BIGINT AS reb,
         SUM(gp)::BIGINT AS gp
       FROM fact_player_career
       WHERE league_id = 'NBA'
         AND career_type = 'Regular Season'
       GROUP BY player_id
       HAVING SUM(gp) > 0
     ),
     ranked AS (
       SELECT
         player_id,
         pts,
         ast,
         reb,
         gp,
         RANK() OVER (ORDER BY ${statKey} DESC NULLS LAST) AS stat_rank
       FROM career_totals
     )
     SELECT
       r.stat_rank,
       r.player_id,
       p.full_name,
       r.${statKey} AS stat_value,
       r.pts,
       r.ast,
       r.reb,
       r.gp
     FROM ranked r
     JOIN dim_player p ON p.player_id = r.player_id AND p.is_current
     ORDER BY r.stat_rank ASC
     LIMIT ?`,
    [limit],
  );
}
