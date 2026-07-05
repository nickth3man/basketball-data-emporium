import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export type Row = Record<string, unknown>;

// Supplemental coach-by-season table scraped from Basketball-Reference
// franchise index pages (``data/anchors/scrape_team_coaches.py``). Retained
// only as a secondary cross-check; `fact_coach_season` in the warehouse is
// now the primary source for coach history (see teams.ts getTeamCoachHistory).
const SERVER_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const BBR_COACHES_PATH =
  process.env.BBR_COACHES_PATH ?? path.resolve(SERVER_DIR, "../../data/anchors/bbr_coaches.jsonl");
export const BBR_COACHES_AVAILABLE = existsSync(BBR_COACHES_PATH);
if (BBR_COACHES_AVAILABLE) {
  console.log(`[queries] BBR coach history file present (secondary): ${BBR_COACHES_PATH}`);
}

export function escapeDuckDbPath(pathValue: string): string {
  return pathValue.replaceAll("\\", "/").replaceAll("'", "''");
}

// dim_game has 33 corrupted All-Star rows (game_date IS NULL) whose
// season_year was parsed as 20YY instead of 19YY (e.g. "2051-52" instead of
// "1951-52"). Any query that lists/min/max's dim_game.season_year for a
// season picker or range must exclude these, or a bogus far-future season
// leaks into the UI.
export const DIM_GAME_SEASON_GUARD_SQL = `(season_type <> 'All-Star' OR game_date IS NOT NULL)`;

// Resolves a canonical NBA player_id to its Basketball-Reference id via
// map_player_bbr. A small number of bbr_player_id values have more than one
// candidate row (4 of 4,860); prefer the one flagged is_preferred.
export const PLAYER_BBR_XWALK_CTE = `player_bbr_xwalk AS (
         SELECT bbr_player_id, player_id AS nba_player_id
         FROM map_player_bbr
         QUALIFY ROW_NUMBER() OVER (
           PARTITION BY bbr_player_id
           ORDER BY is_preferred DESC, player_id
         ) = 1
       )`;

// dim_player carries resolved BBR bio columns (bbr_player_id,
// bbr_primary_position, bbr_height_inches, bbr_weight_lbs, bbr_colleges),
// but its OWN height/weight/position/birth_date columns are unreliable for
// some players (e.g. dim_player.height shows "7-09" for LeBron James, who
// is actually 6'9" — bbr_height_inches=81 is correct). src_common_player_info
// (when present) is the best source for height/weight/position/birth_date/
// country in the format real-world bios use ("6-9" not "6-09", "Center" not
// "C"); when a player has no common_player_info row, fall back to the BBR
// columns (bbr_primary_position for position, bbr_height_inches/
// bbr_weight_lbs converted for height/weight — never dim_player's own
// height/weight, which can carry the same corruption).
export const PLAYER_EXTRA_BIO_CTE = `player_extra_bio AS (
         SELECT
           TRY_CAST(person_id AS BIGINT) AS player_id,
           school,
           greatest_75_flag = 'Y' AS is_greatest_75,
           season_exp,
           position AS common_position,
           height AS common_height,
           weight AS common_weight,
           CAST(CAST(birthdate AS DATE) AS VARCHAR) AS common_birth_date,
           country AS common_country
         FROM src_common_player_info
         WHERE TRY_CAST(person_id AS BIGINT) IS NOT NULL
       )`;

// Thin passthrough over mart_player_season, aliased to the column names the
// query files already use, so callers don't all need renaming in one shot.
// Multi-team ("TOT") display logic and per-era team abbreviation resolution
// no longer come pre-baked from the source table and are handled by callers
// joining dim_team_era where needed.
export const PLAYER_SEASON_STATS_CTE = `player_season_stats AS (
         SELECT
           player_id,
           team_id,
           team_abbreviation AS source_team_abbreviation,
           season_year,
           season_type,
           gp,
           total_min,
           avg_min,
           total_pts,
           avg_pts,
           total_reb,
           avg_reb,
           total_ast,
           avg_ast,
           total_stl,
           avg_stl,
           total_blk,
           avg_blk,
           total_tov,
           avg_tov,
           total_fgm,
           total_fga,
           fg_pct,
           total_fg3m,
           total_fg3a,
           fg3_pct,
           total_ftm,
           total_fta,
           ft_pct,
           avg_off_rating,
           avg_def_rating,
           avg_net_rating,
           avg_ts_pct,
           avg_usg_pct,
           avg_pie
         FROM mart_player_season
       )`;

// mart_player_season/mart_player_career don't carry the NBA-tracking-only
// advanced rate stats (pace, pie, ast_pct, ast_ratio, oreb_pct/dreb_pct,
// reb_pct, efg_pct, fta_rate, poss) — those still need aggregation from
// fact_player_game_advanced per player/season/season_type. Minutes-weighted
// averages match how the old per-game-log aggregation worked.
export const PLAYER_ADVANCED_SEASON_CTE = `player_advanced_season AS (
         SELECT
           a.player_id,
           a.team_id,
           a.season_year,
           a.season_type,
           COUNT(*) AS gp,
           AVG(a.off_rating) AS avg_off_rating,
           AVG(a.def_rating) AS avg_def_rating,
           AVG(a.net_rating) AS avg_net_rating,
           AVG(a.ast_pct) AS avg_ast_pct,
           AVG(a.ast_to) AS avg_ast_to,
           AVG(a.ast_ratio) AS avg_ast_ratio,
           AVG(a.oreb_pct) AS avg_oreb_pct,
           AVG(a.dreb_pct) AS avg_dreb_pct,
           AVG(a.reb_pct) AS avg_reb_pct,
           AVG(a.efg_pct) AS avg_efg_pct,
           AVG(a.ts_pct) AS avg_ts_pct,
           AVG(a.usg_pct) AS avg_usg_pct,
           AVG(a.pace) AS avg_pace,
           AVG(a.pie) AS avg_pie,
           SUM(a.poss) AS total_poss,
           AVG(a.fta_rate) AS avg_fta_rate
         FROM fact_player_game_advanced a
         GROUP BY a.player_id, a.team_id, a.season_year, a.season_type
       )`;

// fact_award consolidates the old three-table BBR award UNION
// (award shares / end-of-season teams / all-star selections) into one
// table with a resolved player_id, a plain award_type string, and
// all_nba_team_number already split out as an integer (no more
// "type || number_tm" string parsing). "season" is a plain end-year string
// ("2016"), matching what the old CTE already exposed as `season`.
export const AWARD_ROWS_CTE = `award_rows AS (
         SELECT
           player_id,
           description,
           all_nba_team_number,
           season,
           month,
           week,
           conference,
           award_type,
           subtype1,
           subtype2,
           subtype3
         FROM fact_award
       )`;

// fact_draft is already denormalized (team fields, organization, and
// round_pick all resolved) — no more bridge_team_bbr/dim_team_history join
// needed. round_pick has been verified to already match a
// ROW_NUMBER()-by-overall_pick derivation per (season, round).
export const DRAFT_SOURCE_CTE = `draft_source AS (
         SELECT
           player_id AS person_id,
           player_name,
           draft_year AS season,
           round_number,
           round_pick,
           overall_pick,
           team_id,
           team_abbreviation,
           organization,
           organization_type,
           draft_type
         FROM fact_draft
       )`;

// Resolves a team's era-correct city/nickname/abbreviation for a given
// season. dim_team_era stores valid_from_year/valid_to_year as INTs, with
// 9999 as the sentinel for "still current" (not NULL) — comparisons are
// inclusive on both ends: valid_from_year <= season_start_year <= valid_to_year.
export function teamEraJoinSql(
  eraAlias: string,
  teamIdExpr: string,
  seasonStartYearExpr: string,
): string {
  return `${eraAlias} ON ${eraAlias}.team_id = ${teamIdExpr}
           AND ${seasonStartYearExpr} BETWEEN ${eraAlias}.valid_from_year AND ${eraAlias}.valid_to_year`;
}
