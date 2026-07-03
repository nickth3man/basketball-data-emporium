import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export type Row = Record<string, unknown>;

// Supplemental jersey-history table scraped from Basketball-Reference
// per-season team-roster pages. The scraper lives at
// ``data/anchors/scrape_team_rosters.py``; its output is a JSONL file
// with one record per (player_id, team_id, season_year, jersey_num).
// The file is read on every player-profile request via DuckDB's
// ``read_json_auto`` — at <300 rows the per-request cost is
// negligible, and a read on every call means re-running the scraper
// (which writes a new file and atomically replaces the old one) is
// picked up without a server restart. The path resolves relative to
// the server directory so it works regardless of CWD
// (npm scripts, tsx watch, production start, etc.). ``BBR_JERSEYS_PATH``
// overrides the default for tests / alternate deployments. When the
// file is missing (fresh checkout, before the scraper has been run)
// the BBR CTE is omitted entirely — the bridge fallback still works.
const SERVER_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const BBR_JERSEYS_PATH =
  process.env.BBR_JERSEYS_PATH ?? path.resolve(SERVER_DIR, "../../data/anchors/bbr_jerseys.jsonl");
export const BBR_JERSEYS_AVAILABLE = existsSync(BBR_JERSEYS_PATH);
if (BBR_JERSEYS_AVAILABLE) {
  // One-time diagnostic so the dev can tell from the server log
  // whether the BBR layer is wired in. The path is logged, not
  // interpolated into SQL, so no escaping needed.
  console.log(`[queries] BBR jersey fallback enabled: ${BBR_JERSEYS_PATH}`);
}

// Supplemental coach-by-season table scraped from Basketball-Reference
// franchise index pages (``data/anchors/scrape_team_coaches.py``), same
// JSONL-at-query-time pattern as the jersey table above. dim_coach only has
// rows for the current season, so this is the only source for historical
// coach-by-season data.
export const BBR_COACHES_PATH =
  process.env.BBR_COACHES_PATH ?? path.resolve(SERVER_DIR, "../../data/anchors/bbr_coaches.jsonl");
export const BBR_COACHES_AVAILABLE = existsSync(BBR_COACHES_PATH);
if (BBR_COACHES_AVAILABLE) {
  console.log(`[queries] BBR coach history enabled: ${BBR_COACHES_PATH}`);
}

export const BBR_SEASON_YEAR_SQL =
  "CAST(season - 1 AS VARCHAR) || '-' || lpad(CAST(season % 100 AS VARCHAR), 2, '0')";

export function escapeDuckDbPath(pathValue: string): string {
  return pathValue.replaceAll("\\", "/").replaceAll("'", "''");
}

export const PLAYER_BBR_XWALK_CTE = `player_bbr_xwalk AS (
         SELECT bbr_player_id, nba_player_id
         FROM (
           SELECT
             b.bbr_player_id,
             b.nba_player_id,
             ROW_NUMBER() OVER (
               PARTITION BY b.bbr_player_id
               ORDER BY COALESCE(g.gp, 0) DESC, b.nba_player_id
             ) AS rn
           FROM bridge_player_bbr b
           LEFT JOIN (
             SELECT player_id, COUNT(*) AS gp
             FROM fact_player_game_log
             GROUP BY player_id
           ) g ON g.player_id = b.nba_player_id
         )
         WHERE rn = 1
       )`;

export const PLAYER_BREF_BIO_CTE = `bref_player_bio AS (
         SELECT
           b.nba_player_id AS player_id,
           ci.bref_player_id,
           ci.pos AS position,
           CASE
             WHEN TRY_CAST(ci.ht_in_in AS BIGINT) IS NULL THEN NULL
             ELSE CAST(FLOOR(TRY_CAST(ci.ht_in_in AS BIGINT) / 12) AS BIGINT)::VARCHAR
                  || '-' ||
                  CAST(TRY_CAST(ci.ht_in_in AS BIGINT) % 12 AS BIGINT)::VARCHAR
           END AS height,
           TRY_CAST(ci.wt AS BIGINT) AS weight,
           CAST(ci.birth_date AS VARCHAR) AS birth_date,
           ci.colleges AS school,
           ci."from" AS from_year,
           ci."to" AS to_year
         FROM bridge_player_bbr b
         JOIN stg_bref_player_career_info ci ON ci.bref_player_id = b.bbr_player_id
         QUALIFY ROW_NUMBER() OVER (
           PARTITION BY b.nba_player_id
           ORDER BY ci."to" DESC NULLS LAST, ci."from" DESC NULLS LAST, ci.bref_player_id
         ) = 1
       )`;

export const PLAYER_SEASON_STATS_CTE = `${PLAYER_BBR_XWALK_CTE},
       resolved_player_season_source AS (
         SELECT COALESCE(r.person_id, x.nba_player_id) AS player_id, r.*
         FROM fact_player_season_stat_resolved r
         LEFT JOIN player_bbr_xwalk x
           ON r.person_id IS NULL AND x.bbr_player_id = r.slug
         WHERE COALESCE(r.person_id, x.nba_player_id) IS NOT NULL
       ),
       player_season_stats AS (
         SELECT
           player_id,
           team_id,
           team_abbrev AS source_team_abbreviation,
           ${BBR_SEASON_YEAR_SQL} AS season_year,
           CASE WHEN is_playoffs THEN 'Playoffs' ELSE 'Regular' END AS season_type,
           CAST(gp AS BIGINT) AS gp,
           CAST(min AS DOUBLE) AS total_min,
           min / NULLIF(gp, 0) AS avg_min,
           CAST(pts AS DOUBLE) AS total_pts,
           pts / NULLIF(gp, 0) AS avg_pts,
           CAST(COALESCE(trb, orb + drb) AS DOUBLE) AS total_reb,
           COALESCE(trb, orb + drb) / NULLIF(gp, 0) AS avg_reb,
           CAST(ast AS DOUBLE) AS total_ast,
           ast / NULLIF(gp, 0) AS avg_ast,
           CAST(stl AS DOUBLE) AS total_stl,
           stl / NULLIF(gp, 0) AS avg_stl,
           CAST(blk AS DOUBLE) AS total_blk,
           blk / NULLIF(gp, 0) AS avg_blk,
           CAST(tov AS DOUBLE) AS total_tov,
           tov / NULLIF(gp, 0) AS avg_tov,
           CAST(fg AS DOUBLE) AS total_fgm,
           CAST(fga AS DOUBLE) AS total_fga,
           fg / NULLIF(fga, 0) AS fg_pct,
           CAST(tp AS DOUBLE) AS total_fg3m,
           CAST(tpa AS DOUBLE) AS total_fg3a,
           tp / NULLIF(tpa, 0) AS fg3_pct,
           CAST(ft AS DOUBLE) AS total_ftm,
           CAST(fta AS DOUBLE) AS total_fta,
           ft / NULLIF(fta, 0) AS ft_pct,
           ortg AS avg_off_rating,
           drtg AS avg_def_rating,
           ortg - drtg AS avg_net_rating,
           pts / NULLIF(2 * (fga + 0.44 * fta), 0) AS avg_ts_pct,
           usgp / 100.0 AS avg_usg_pct,
           (fg + 0.5 * tp) / NULLIF(fga, 0) AS avg_efg_pct,
           astp / 100.0 AS avg_ast_pct,
           orbp / 100.0 AS avg_oreb_pct,
           drbp / 100.0 AS avg_dreb_pct,
           trbp / 100.0 AS avg_reb_pct,
           tovp / 100.0 AS avg_tov_pct,
           per,
           ows,
           dws,
           ows + dws AS ws,
           obpm,
           dbpm,
           obpm + dbpm AS bpm,
           vorp
         FROM resolved_player_season_source
       )`;

export const PLAYER_AWARD_ROWS_CTE = `${PLAYER_BBR_XWALK_CTE},
       bref_name_span_xwalk AS (
         SELECT normalized_player_name, nba_player_id, "from" AS from_year, "to" AS to_year
         FROM stg_bref_player_career_info
         WHERE nba_player_id IS NOT NULL
         QUALIFY ROW_NUMBER() OVER (
           PARTITION BY normalized_player_name, "from", "to"
           ORDER BY nba_player_id
         ) = 1
       ),
       award_rows AS (
         SELECT *
         FROM (
           SELECT
             COALESCE(s.nba_player_id, x.nba_player_id, nx.nba_player_id) AS player_id,
             s.player AS source_player_name,
             s.award || CASE WHEN s.winner THEN ' winner' ELSE ' (received votes)' END AS description,
             CAST(NULL AS BIGINT) AS all_nba_team_number,
             CAST(s.season AS VARCHAR) AS season,
             CAST(NULL AS VARCHAR) AS month,
             CAST(NULL AS VARCHAR) AS week,
             CAST(NULL AS VARCHAR) AS conference,
             s.award AS award_type,
             CASE WHEN s.winner THEN 'Selected' END AS subtype1,
             CAST(s.share AS VARCHAR) AS subtype2,
             CAST(NULL AS VARCHAR) AS subtype3
           FROM stg_bref_player_award_shares s
           LEFT JOIN player_bbr_xwalk x
             ON x.bbr_player_id = s.bref_player_id
           LEFT JOIN bref_name_span_xwalk nx
             ON s.nba_player_id IS NULL
             AND s.bref_player_id IS NULL
             AND nx.normalized_player_name = s.normalized_player_name
             AND s.season BETWEEN nx.from_year AND nx.to_year
           WHERE s.winner
             AND s.award IN ('nba mvp', 'nba roy', 'nba dpoy', 'nba mip', 'nba smoy')

           UNION ALL

           SELECT
             COALESCE(t.nba_player_id, x.nba_player_id, nx.nba_player_id) AS player_id,
             t.player AS source_player_name,
             t.type || ' ' || t.number_tm || ' Team' AS description,
             CASE t.number_tm WHEN '1st' THEN 1 WHEN '2nd' THEN 2 WHEN '3rd' THEN 3 END AS all_nba_team_number,
             CAST(t.season AS VARCHAR) AS season,
             NULL AS month,
             NULL AS week,
             NULL AS conference,
             t.type AS award_type,
             NULL AS subtype1,
             NULL AS subtype2,
             NULL AS subtype3
           FROM stg_bref_end_of_season_teams t
           LEFT JOIN player_bbr_xwalk x
             ON x.bbr_player_id = t.bref_player_id
           LEFT JOIN bref_name_span_xwalk nx
             ON t.nba_player_id IS NULL
             AND t.bref_player_id IS NULL
             AND nx.normalized_player_name = t.normalized_player_name
             AND t.season BETWEEN nx.from_year AND nx.to_year
           WHERE t.lg = 'NBA'
             AND t.type IN ('All-NBA', 'All-Rookie', 'All-Defense')

           UNION ALL

           SELECT DISTINCT
             COALESCE(a.nba_player_id, x.nba_player_id, nx.nba_player_id) AS player_id,
             a.player AS source_player_name,
             'NBA All-Star' AS description,
             CAST(NULL AS BIGINT) AS all_nba_team_number,
             CAST(a.season AS VARCHAR) AS season,
             NULL AS month,
             NULL AS week,
             NULL AS conference,
             'All-Star' AS award_type,
             NULL AS subtype1,
             NULL AS subtype2,
             NULL AS subtype3
           FROM stg_bref_all_star_selections a
           LEFT JOIN player_bbr_xwalk x
             ON x.bbr_player_id = a.bref_player_id
           LEFT JOIN bref_name_span_xwalk nx
             ON a.nba_player_id IS NULL
             AND a.bref_player_id IS NULL
             AND nx.normalized_player_name = a.normalized_player_name
             AND a.season BETWEEN nx.from_year AND nx.to_year
           WHERE a.lg = 'NBA'
         )
       )`;

export const DRAFT_SOURCE_CTE = `draft_source AS (
         WITH draft_org_fallback AS (
           SELECT
             season,
             overall_pick,
             lower(player_name) AS player_name_key,
             organization,
             organization_type
           FROM fact_draft_history
           WHERE organization IS NOT NULL OR organization_type IS NOT NULL
           QUALIFY ROW_NUMBER() OVER (
             PARTITION BY season, overall_pick, lower(player_name)
             ORDER BY CASE WHEN draft_type = 'Draft' THEN 0 ELSE 1 END
           ) = 1
         )
         SELECT
           TRY_CAST(d.nba_player_id AS BIGINT) AS person_id,
           d.player AS player_name,
           CAST(d.season AS VARCHAR) AS season,
           CAST(d.round AS BIGINT) AS round_number,
           CAST(
             ROW_NUMBER() OVER (PARTITION BY d.season, d.round ORDER BY d.overall_pick)
             AS BIGINT
           ) AS round_pick,
           CAST(d.overall_pick AS BIGINT) AS overall_pick,
           tb.team_id,
           COALESCE(d.tm, tb.team_abbreviation, th.abbreviation) AS team_abbreviation,
           COALESCE(d.college, org.organization) AS organization,
           CASE
             WHEN d.college IS NOT NULL THEN 'College'
             ELSE org.organization_type
           END AS organization_type,
           d.bref_player_id
         FROM stg_bref_draft_pick_history d
         LEFT JOIN draft_org_fallback org
           ON org.season = CAST(d.season AS VARCHAR)
           AND org.overall_pick = d.overall_pick
           AND org.player_name_key = lower(d.player)
         LEFT JOIN bridge_team_bbr tb
           ON tb.season = d.season
           AND tb.bbr_abbreviation = d.tm
           AND tb.lg IN (d.lg, 'NBA')
         LEFT JOIN dim_team_history th
           ON th.team_id = tb.team_id
           AND CAST(d.season AS VARCHAR) >= th.valid_from
           AND (th.valid_to IS NULL OR CAST(d.season AS VARCHAR) < th.valid_to)
         WHERE d.lg IN ('NBA', 'BAA')
       )`;
