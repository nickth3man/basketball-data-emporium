import { queryObjects } from "../db.ts";
import { PLAYER_SEASON_STATS_CTE, type Row } from "./shared.ts";

// ---------------------------------------------------------------------------
// League Leaders
//
// mart_league_leaders is a prebuilt, pre-ranked mart, but only covers
// pts/reb/ast. stl/blk have no prebuilt rank column, so they're ranked live
// over mart_player_season (same RANK() OVER (...) pattern used for the
// player-profile "Champ" badges) — this keeps the 5-stat taxonomy the app
// has always exposed instead of shrinking it to match the narrower mart.
// All-Time leaders are recomputed from mart_player_career (regular-season
// totals) rather than a legacy all-time-leaders table whose totals were
// inflated/incorrect vs BBR.
// ---------------------------------------------------------------------------

const MART_LEADER_STAT_COLUMNS: Record<string, { avg: string; rank: string }> = {
  pts: { avg: "avg_pts", rank: "rank_pts" },
  reb: { avg: "avg_reb", rank: "rank_reb" },
  ast: { avg: "avg_ast", rank: "rank_ast" },
};

const LIVE_RANK_STAT_COLUMNS: Record<string, string> = {
  stl: "avg_stl",
  blk: "avg_blk",
};

export async function listLeaderSeasons(): Promise<string[]> {
  const rows = await queryObjects<{ season_year: string }>(
    `SELECT DISTINCT season_year
     FROM mart_league_leaders
     WHERE season_type = 'Regular'
     ORDER BY season_year DESC`,
  );
  return rows.map((r) => r.season_year);
}

export function listLeaderStatKeys(): string[] {
  // pts/reb/ast come from the prebuilt mart; stl/blk are computed live over
  // mart_player_season — both are always available, so this is a fixed list
  // (adding more stats is just adding another LIVE_RANK_STAT_COLUMNS entry).
  return ["ast", "blk", "pts", "reb", "stl"];
}

export async function getSeasonLeaders(
  season: string,
  statKey: string,
  limit = 25,
): Promise<Row[]> {
  const seasonStartYear = Number(season.slice(0, 4));

  if (LIVE_RANK_STAT_COLUMNS[statKey]) {
    const col = LIVE_RANK_STAT_COLUMNS[statKey];
    return queryObjects(
      `WITH ${PLAYER_SEASON_STATS_CTE},
       leader_team AS (
         SELECT
           player_id,
           COUNT(DISTINCT team_id) AS team_count,
           MIN(team_id) AS team_id,
           MAX(source_team_abbreviation) AS source_team_abbreviation,
           SUM(gp) AS gp,
           SUM(${col} * gp) / NULLIF(SUM(gp), 0) AS stat_value
         FROM player_season_stats
         WHERE season_type = 'Regular' AND season_year = ?
         GROUP BY player_id
       ),
       ranked AS (
         SELECT
           *,
           RANK() OVER (ORDER BY stat_value DESC NULLS LAST) AS stat_rank
         FROM leader_team
       )
       SELECT
         r.player_id,
         p.full_name,
         ? AS season_year,
         'Regular' AS season_type,
         r.gp,
         r.stat_value,
         r.stat_rank,
         CASE
           WHEN r.team_count > 1 THEN 'TOT'
           ELSE COALESCE(era.abbreviation, r.source_team_abbreviation)
         END AS team_abbreviation
       FROM ranked r
       JOIN dim_player p ON p.player_id = r.player_id
       LEFT JOIN dim_team_era era
         ON era.team_id = r.team_id
         AND ? BETWEEN era.valid_from_year AND era.valid_to_year
       WHERE r.stat_rank <= ?
       ORDER BY r.stat_rank ASC`,
      [season, season, seasonStartYear, limit],
    );
  }

  const cols = MART_LEADER_STAT_COLUMNS[statKey] ?? MART_LEADER_STAT_COLUMNS.pts;
  return queryObjects(
    `SELECT
       l.player_id,
       p.full_name,
       l.season_year,
       l.season_type,
       l.gp,
       l.${cols.avg} AS stat_value,
       l.${cols.rank} AS stat_rank,
       COALESCE(era.abbreviation, cur.abbreviation) AS team_abbreviation
     FROM mart_league_leaders l
     JOIN dim_player p ON p.player_id = l.player_id
     LEFT JOIN dim_team_era era
       ON era.team_id = l.team_id
       AND ? BETWEEN era.valid_from_year AND era.valid_to_year
     LEFT JOIN dim_team_era cur ON cur.team_id = l.team_id AND cur.is_current
     WHERE l.season_year = ?
       AND l.season_type = 'Regular'
       AND l.${cols.rank} IS NOT NULL
     ORDER BY l.${cols.rank} ASC
     LIMIT ?`,
    [seasonStartYear, season, limit],
  );
}

export async function getAllTimeLeaders(
  statKey: "pts" | "ast" | "reb" = "pts",
  limit = 50,
): Promise<Row[]> {
  // mart_player_career only carries per-game rate averages for ast/reb (not
  // career totals like it does for pts) — total ast/reb are derived from
  // career_apg/career_rpg * career_gp, which is exact given those averages
  // were themselves computed from the same totals.
  const statValueExpr =
    statKey === "pts"
      ? "career_pts"
      : statKey === "ast"
        ? "ROUND(career_apg * career_gp)"
        : "ROUND(career_rpg * career_gp)";
  return queryObjects(
    `WITH ranked AS (
       SELECT
         player_id,
         career_pts AS pts,
         ROUND(career_apg * career_gp) AS ast,
         ROUND(career_rpg * career_gp) AS reb,
         career_gp AS gp,
         RANK() OVER (ORDER BY ${statValueExpr} DESC NULLS LAST) AS stat_rank
       FROM mart_player_career
       WHERE career_gp > 0
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
     JOIN dim_player p ON p.player_id = r.player_id
     ORDER BY r.stat_rank ASC
     LIMIT ?`,
    [limit],
  );
}
