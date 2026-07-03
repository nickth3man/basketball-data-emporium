import type { DuckDBValue } from "@duckdb/node-api";
import { queryObjects } from "../db.ts";
import { DRAFT_SOURCE_CTE, PLAYER_SEASON_STATS_CTE, type Row } from "./shared.ts";

// ---------------------------------------------------------------------------
// Draft
// ---------------------------------------------------------------------------

export async function listDraftYears(): Promise<string[]> {
  const rows = await queryObjects<{ season: string }>(
    `WITH ${DRAFT_SOURCE_CTE}
     SELECT DISTINCT season FROM draft_source ORDER BY season DESC`,
  );
  return rows.map((r) => r.season);
}

export async function getDraftYear(season: string): Promise<Row[]> {
  return queryObjects(
    `WITH ${DRAFT_SOURCE_CTE}
     SELECT *
     FROM draft_source
     WHERE season = ?
     ORDER BY overall_pick`,
    [season],
  );
}

// ---------------------------------------------------------------------------
// Draft Value / Career Success
//
// Draft rows come from BBR staging rather than draft_history /
// analytics_draft_value, both of which can be stale or duplicated around
// forfeited picks. Career totals are recomputed from the same resolved BBR
// season CTE used by player profiles; analytics_draft_value is only a fallback
// for historical players that have source names but no dim_player mapping.
// ---------------------------------------------------------------------------

const DRAFT_VALUE_SORT_COLUMNS: ReadonlySet<string> = new Set([
  "career_ppg",
  "career_rpg",
  "career_apg",
  "career_gp",
  "career_fg_pct",
  "career_fg3_pct",
  "seasons_played",
]);

export async function listDraftValueRounds(): Promise<number[]> {
  const rows = await queryObjects<{ round_number: number }>(
    `WITH ${DRAFT_SOURCE_CTE}
     SELECT DISTINCT round_number FROM draft_source ORDER BY round_number`,
  );
  return rows.map((r) => Number(r.round_number));
}

export async function getDraftValueBoard(opts?: {
  round?: number;
  sortBy?: string;
  limit?: number;
}): Promise<Row[]> {
  const sortBy =
    opts?.sortBy && DRAFT_VALUE_SORT_COLUMNS.has(opts.sortBy) ? opts.sortBy : "career_ppg";
  const limit = opts?.limit ?? 50;
  const conditions: string[] = [];
  const params: DuckDBValue[] = [];
  if (opts?.round !== undefined && Number.isFinite(opts.round)) {
    conditions.push("d.round_number = ?");
    params.push(opts.round);
  }
  const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";
  return queryObjects(
    `WITH ${PLAYER_SEASON_STATS_CTE},
     ${DRAFT_SOURCE_CTE},
     career_totals AS (
       SELECT
         player_id,
         SUM(gp) AS career_gp,
         SUM(total_pts) AS career_pts,
         SUM(total_pts) / NULLIF(SUM(gp), 0) AS career_ppg,
         SUM(total_reb) / NULLIF(SUM(gp), 0) AS career_rpg,
         SUM(total_ast) / NULLIF(SUM(gp), 0) AS career_apg,
         SUM(total_fgm) / NULLIF(SUM(total_fga), 0) AS career_fg_pct,
         SUM(total_fg3m) / NULLIF(SUM(total_fg3a), 0) AS career_fg3_pct,
         COUNT(DISTINCT season_year) AS seasons_played,
         MIN(season_year) AS first_season,
         MAX(season_year) AS last_season
       FROM player_season_stats
       WHERE season_type = 'Regular'
       GROUP BY player_id
     ),
     legacy_value AS (
       SELECT *
       FROM analytics_draft_value
       QUALIFY ROW_NUMBER() OVER (
         PARTITION BY season, overall_pick, lower(player_name)
         ORDER BY career_gp DESC NULLS LAST, person_id
       ) = 1
     )
     SELECT
       d.person_id AS player_id,
       d.player_name AS source_player_name,
       COALESCE(p.full_name, d.player_name) AS full_name,
       d.season,
       d.round_number,
       d.round_pick,
       d.overall_pick,
       d.team_id,
       d.team_abbreviation,
       COALESCE(bb.pos, lv.position) AS position,
       COALESCE(cpi.country, lv.country) AS country,
       COALESCE(c.career_gp, lv.career_gp) AS career_gp,
       COALESCE(c.career_pts, lv.career_pts) AS career_pts,
       COALESCE(c.career_ppg, lv.career_ppg) AS career_ppg,
       COALESCE(c.career_rpg, lv.career_rpg) AS career_rpg,
       COALESCE(c.career_apg, lv.career_apg) AS career_apg,
       COALESCE(c.career_fg_pct, lv.career_fg_pct) AS career_fg_pct,
       COALESCE(c.career_fg3_pct, lv.career_fg3_pct) AS career_fg3_pct,
       COALESCE(c.seasons_played, lv.seasons_played) AS seasons_played,
       COALESCE(c.first_season, lv.first_season) AS first_season,
       COALESCE(c.last_season, lv.last_season) AS last_season
     FROM draft_source d
     LEFT JOIN dim_player p ON p.player_id = d.person_id AND p.is_current
     LEFT JOIN common_player_info cpi ON TRY_CAST(cpi.person_id AS BIGINT) = d.person_id
     LEFT JOIN stg_bref_player_career_info bb ON bb.bref_player_id = d.bref_player_id
     LEFT JOIN career_totals c ON c.player_id = d.person_id
     LEFT JOIN legacy_value lv
       ON lv.season = d.season
       AND lv.overall_pick = d.overall_pick
       AND lower(lv.player_name) = lower(d.player_name)
     ${where}
     ORDER BY ${sortBy} DESC NULLS LAST, d.overall_pick ASC
     LIMIT ?`,
    [...params, limit],
  );
}
