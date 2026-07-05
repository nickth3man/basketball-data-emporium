import type { DuckDBValue } from "@duckdb/node-api";
import { queryObjects } from "../db.ts";
import { DRAFT_SOURCE_CTE, type Row } from "./shared.ts";

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
// mart_draft_value is a prebuilt mart joining draft picks to career totals —
// no more separate BBR staging + career-recompute + legacy-fallback dance.
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
    `SELECT DISTINCT round_number FROM mart_draft_value ORDER BY round_number`,
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
    `SELECT
       d.person_id AS player_id,
       d.player_name AS source_player_name,
       COALESCE(p.full_name, d.player_name) AS full_name,
       d.season,
       d.round_number,
       d.round_pick,
       d.overall_pick,
       d.team_id,
       t.abbreviation AS team_abbreviation,
       d.position,
       d.country,
       d.career_gp,
       d.career_pts,
       d.career_ppg,
       d.career_rpg,
       d.career_apg,
       d.career_fg_pct,
       d.career_fg3_pct,
       d.seasons_played,
       d.first_season,
       d.last_season
     FROM mart_draft_value d
     LEFT JOIN dim_player p ON p.player_id = d.person_id
     LEFT JOIN dim_team_era t ON t.team_id = d.team_id AND t.is_current
     ${where}
     ORDER BY ${sortBy} DESC NULLS LAST, d.overall_pick ASC
     LIMIT ?`,
    [...params, limit],
  );
}
