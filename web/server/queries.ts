import { HONOR_LABELS } from "../src/awards.ts";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { queryObjects } from "./db.ts";
import type { DuckDBValue } from "@duckdb/node-api";
import { colorForEra } from "./teamColorEras.ts";

type Row = Record<string, unknown>;

// Supplemental jersey-history table scraped from Basketball-Reference
// per-season team-roster pages. The scraper lives at
// ``data/anchors/scrape_team_rosters.py``; its output is a JSONL file
// with one record per (player_id, team_id, season_year, jersey_num).
// The file is read on every player-profile request via DuckDB's
// ``read_json_auto`` — at <300 rows the per-request cost is
// negligible, and a read on every call means re-running the scraper
// (which writes a new file and atomically replaces the old one) is
// picked up without a server restart. The path resolves relative to
// this file (web/server/queries.ts) so it works regardless of CWD
// (npm scripts, tsx watch, production start, etc.). ``BBR_JERSEYS_PATH``
// overrides the default for tests / alternate deployments. When the
// file is missing (fresh checkout, before the scraper has been run)
// the BBR CTE is omitted entirely — the bridge fallback still works.
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BBR_JERSEYS_PATH =
  process.env.BBR_JERSEYS_PATH ?? path.resolve(__dirname, "../../data/anchors/bbr_jerseys.jsonl");
const BBR_JERSEYS_AVAILABLE = existsSync(BBR_JERSEYS_PATH);
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
const BBR_COACHES_PATH =
  process.env.BBR_COACHES_PATH ?? path.resolve(__dirname, "../../data/anchors/bbr_coaches.jsonl");
const BBR_COACHES_AVAILABLE = existsSync(BBR_COACHES_PATH);
if (BBR_COACHES_AVAILABLE) {
  console.log(`[queries] BBR coach history enabled: ${BBR_COACHES_PATH}`);
}

const BBR_SEASON_YEAR_SQL =
  "CAST(season - 1 AS VARCHAR) || '-' || lpad(CAST(season % 100 AS VARCHAR), 2, '0')";

const PLAYER_BBR_XWALK_CTE = `player_bbr_xwalk AS (
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

const PLAYER_BREF_BIO_CTE = `bref_player_bio AS (
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

const PLAYER_SEASON_STATS_CTE = `${PLAYER_BBR_XWALK_CTE},
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

const PLAYER_AWARD_ROWS_CTE = `${PLAYER_BBR_XWALK_CTE},
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

const DRAFT_SOURCE_CTE = `draft_source AS (
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

// Jersey-history SQL template. ``$BBR_CTE`` is replaced with the BBR
// CTE (or empty when the file is missing); ``$BBR_UNION`` is replaced
// with the corresponding ranked source branch (or empty). The body is the
// long, hand-tuned gaps-and-islands query that the previous
// per-row / per-season logic depends on; details are in the inline
// comments above the call site. We avoid an embedded template string
// here because that would intermingle SQL and JS-quoting concerns —
// a plain string with two placeholders, spliced in buildJerseyQuery,
// is the cleanest split.
const JERSEY_SQL_TEMPLATE = `WITH ${PLAYER_SEASON_STATS_CTE},
       per_game AS (
         SELECT
           TRY_CAST(ip.team_id AS BIGINT) AS team_id,
           TRIM(ip.jersey_num) AS jersey_num,
           CASE WHEN MONTH(g.game_date) >= 8
             THEN CAST(YEAR(g.game_date) AS VARCHAR) || '-' || RIGHT(CAST(YEAR(g.game_date) + 1 AS VARCHAR), 2)
             ELSE CAST(YEAR(g.game_date) - 1 AS VARCHAR) || '-' || RIGHT(CAST(YEAR(g.game_date) AS VARCHAR), 2)
           END AS season_year
         FROM inactive_players ip
         JOIN game g ON g.game_id = ip.game_id
         WHERE ip.player_id = ? AND TRIM(ip.jersey_num) != ''
           AND EXISTS (
             SELECT 1
             FROM player_season_stats inactive_season
             WHERE inactive_season.player_id = ip.player_id
               AND inactive_season.team_id = TRY_CAST(ip.team_id AS BIGINT)
               AND inactive_season.season_year = CASE WHEN MONTH(g.game_date) >= 8
                 THEN CAST(YEAR(g.game_date) AS VARCHAR) || '-' || RIGHT(CAST(YEAR(g.game_date) + 1 AS VARCHAR), 2)
                 ELSE CAST(YEAR(g.game_date) - 1 AS VARCHAR) || '-' || RIGHT(CAST(YEAR(g.game_date) AS VARCHAR), 2)
               END
               AND inactive_season.season_type = 'Regular'
               AND inactive_season.gp > 0
           )
       ),
       per_season_ip AS (
         SELECT season_year, team_id, jersey_num, count(*) AS n
         FROM per_game
         GROUP BY 1, 2, 3
         QUALIFY ROW_NUMBER() OVER (PARTITION BY season_year, team_id ORDER BY n DESC) = 1
       ),
       $BBR_CTE
       bridge_dedup AS (
         SELECT DISTINCT
           team_id,
           TRIM(jersey_number) AS jersey_num,
           season_year
         FROM bridge_player_team_season
         WHERE player_id = ?
           AND jersey_number IS NOT NULL
           AND TRIM(jersey_number) != ''
           AND EXISTS (
             SELECT 1
             FROM player_season_stats bridge_season
             WHERE bridge_season.player_id = bridge_player_team_season.player_id
               AND bridge_season.team_id = TRY_CAST(bridge_player_team_season.team_id AS BIGINT)
               AND bridge_season.season_year = bridge_player_team_season.season_year
               AND bridge_season.season_type = 'Regular'
           )
           $BRIDGE_BBR_EXCLUSION
       ),
       trusted_candidates AS (
         SELECT team_id, jersey_num, season_year, 1 AS source_priority FROM per_season_ip
         $BBR_UNION
       ),
       exact_trusted AS (
         SELECT team_id, jersey_num, season_year, source_priority
         FROM trusted_candidates
         QUALIFY ROW_NUMBER() OVER (
           PARTITION BY team_id, season_year
           ORDER BY source_priority
         ) = 1
       ),
       trusted_team_numbers AS (
         SELECT
           team_id,
           MIN(jersey_num) AS jersey_num,
           MAX(TRY_CAST(LEFT(season_year, 4) AS INTEGER)) AS last_trusted_start_year,
           COUNT(DISTINCT jersey_num) AS distinct_jersey_nums
         FROM trusted_candidates
         GROUP BY team_id
       ),
       player_team_seasons AS (
         SELECT DISTINCT TRY_CAST(team_id AS BIGINT) AS team_id, season_year
         FROM player_season_stats
         WHERE player_id = ?
           AND season_type = 'Regular'
           AND gp > 0
       ),
       inferred_from_trusted AS (
         SELECT pts.team_id, trusted.jersey_num, pts.season_year, 3 AS source_priority
         FROM player_team_seasons pts
         JOIN trusted_team_numbers trusted ON trusted.team_id = pts.team_id
         WHERE trusted.distinct_jersey_nums = 1
           AND TRY_CAST(LEFT(pts.season_year, 4) AS INTEGER) <= trusted.last_trusted_start_year
           AND NOT EXISTS (
             SELECT 1
             FROM exact_trusted exact
             WHERE exact.team_id = pts.team_id AND exact.season_year = pts.season_year
           )
       ),
       bridge_fill AS (
         SELECT bridge.team_id, bridge.jersey_num, bridge.season_year, 4 AS source_priority
         FROM bridge_dedup bridge
         WHERE NOT EXISTS (
             SELECT 1
             FROM exact_trusted exact
             WHERE exact.team_id = bridge.team_id AND exact.season_year = bridge.season_year
           )
           AND NOT EXISTS (
             SELECT 1
             FROM inferred_from_trusted inferred
             WHERE inferred.team_id = bridge.team_id AND inferred.season_year = bridge.season_year
           )
       ),
       combined_candidates AS (
         SELECT team_id, jersey_num, season_year, source_priority FROM exact_trusted
         UNION ALL
         SELECT team_id, jersey_num, season_year, source_priority FROM inferred_from_trusted
         UNION ALL
         SELECT team_id, jersey_num, season_year, source_priority FROM bridge_fill
       ),
       valid_candidates AS (
         SELECT *
         FROM combined_candidates candidate
         WHERE EXISTS (
           SELECT 1
           FROM dim_team_history history_team
           WHERE history_team.team_id = candidate.team_id
         )
       ),
       combined AS (
         SELECT team_id, jersey_num, season_year
         FROM valid_candidates
         QUALIFY ROW_NUMBER() OVER (
           PARTITION BY team_id, season_year
           ORDER BY source_priority
         ) = 1
       ),
       combined_with_first AS (
         SELECT team_id, jersey_num, season_year,
                MIN(season_year) OVER (PARTITION BY team_id, jersey_num) AS first_season
         FROM combined
       ),
       grouped AS (
         SELECT
           team_id, jersey_num, season_year,
           ROW_NUMBER() OVER (ORDER BY season_year, first_season, team_id, jersey_num)
             - ROW_NUMBER() OVER (PARTITION BY team_id, jersey_num ORDER BY season_year) AS stint_group
         FROM combined_with_first
       ),
       stint_bounds AS (
         SELECT team_id, jersey_num, stint_group, MIN(season_year) AS stint_first_season
         FROM grouped
         GROUP BY team_id, jersey_num, stint_group
       )
       SELECT
         g.team_id,
         g.jersey_num,
         g.season_year,
         g.stint_group,
         COALESCE(dt.abbreviation, th.abbreviation) AS abbreviation,
         COALESCE(dt.full_name, th.nickname) AS team_name
       FROM grouped g
       JOIN stint_bounds sb
         ON sb.team_id = g.team_id AND sb.jersey_num = g.jersey_num AND sb.stint_group = g.stint_group
       JOIN dim_team_history th
         ON th.team_id = g.team_id
         AND (g.season_year >= th.valid_from OR th.is_current)
         AND (th.valid_to IS NULL OR g.season_year < th.valid_to)
       LEFT JOIN dim_team dt
         ON dt.team_id = g.team_id
         AND TRY_CAST(LEFT(g.season_year, 4) AS INTEGER) >= TRY_CAST(dt.year_founded AS INTEGER)
       QUALIFY ROW_NUMBER() OVER (
         PARTITION BY g.team_id, g.jersey_num, g.stint_group, g.season_year
         ORDER BY
           CASE WHEN dt.team_id IS NOT NULL THEN 0 ELSE 1 END,
           CASE WHEN dt.year_founded IS NULL THEN 1 ELSE 0 END,
           TRY_CAST(dt.year_founded AS INTEGER) DESC,
           CASE WHEN g.season_year >= th.valid_from AND (th.valid_to IS NULL OR g.season_year < th.valid_to) THEN 0 ELSE 1 END,
           th.valid_from ASC
       ) = 1
        ORDER BY sb.stint_first_season, g.season_year, g.team_id, g.jersey_num`;

/** Builds the SQL for the per-player jersey history query, optionally
 *  splicing in the BBR-scraped roster CTE when its JSONL file
 *  is present. Returns the SQL string and the parameter array (in
 *  the order DuckDB will bind them). The BBR CTE and UNION are
 *  omitted entirely when the file is missing so a fresh checkout
 *  with no scraped data still gets a working bridge-fallback query. */
function buildJerseyQuery(playerId: number): { sql: string; params: DuckDBValue[] } {
  if (!BBR_JERSEYS_AVAILABLE) {
    // Empty placeholders yield the same shape as the bridge-only query
    // that shipped before BBR was introduced. The three ``?``s bind to
    // per_game, bridge_dedup, and player_team_seasons.
    return {
      sql: JERSEY_SQL_TEMPLATE.replace("$BBR_CTE", "")
        .replace("$BRIDGE_BBR_EXCLUSION", "")
        .replace("$BBR_UNION", ""),
      params: [playerId, playerId, playerId],
    };
  }
  // SQL-string-escape the path: DuckDB's read_json_auto takes a string
  // literal, not a parameter. Forward slashes work on every platform
  // (including Windows) so we normalize, then double any embedded
  // single quotes.
  const safePath = BBR_JERSEYS_PATH.replaceAll("\\", "/").replaceAll("'", "''");
  const bbrCte = `bbr_raw AS (
          SELECT
            TRY_CAST(player_id AS BIGINT) AS player_id,
            TRY_CAST(team_id AS BIGINT) AS team_id,
            TRIM(jersey_num) AS jersey_num,
            season_year
          FROM read_json_auto('${safePath}')
          WHERE team_id IS NOT NULL
            AND season_year IS NOT NULL
        ),
        bbr_covered_team_seasons AS (
          SELECT team_id, season_year
          FROM bbr_raw
          GROUP BY team_id, season_year
          HAVING COUNT(DISTINCT player_id) >= 5
        ),
        bbr_dedup AS (
          SELECT DISTINCT
            team_id,
            jersey_num,
            season_year
          FROM bbr_raw
          WHERE player_id = ?
            AND jersey_num IS NOT NULL
            AND jersey_num != ''
        ),
        `;
  const bridgeBbrExclusion = `
           AND NOT EXISTS (
             SELECT 1
             FROM bbr_covered_team_seasons bbr_coverage
             WHERE bbr_coverage.team_id = TRY_CAST(bridge_player_team_season.team_id AS BIGINT)
               AND bbr_coverage.season_year = bridge_player_team_season.season_year
           )`;
  const bbrUnion = `
         UNION ALL
         SELECT team_id, jersey_num, season_year, 2 AS source_priority FROM bbr_dedup`;
  const sql = JERSEY_SQL_TEMPLATE.replace("$BBR_CTE", bbrCte)
    .replace("$BRIDGE_BBR_EXCLUSION", bridgeBbrExclusion)
    .replace("$BBR_UNION", bbrUnion);
  return { sql, params: [playerId, playerId, playerId, playerId] };
}

// ---------------------------------------------------------------------------
// Players
//
// dim_player is a slowly-changing-dimension table (one row per team stint),
// so every lookup filters to is_current=true to get exactly one row per
// player. Player-facing season/career stats are read from the imported clean
// BBR-resolved season table, and awards are read from BBR staging rows via the
// player bridge rather than the lossy legacy award fact.
// ---------------------------------------------------------------------------

export async function searchPlayers(q: string): Promise<Row[]> {
  const trimmed = q.trim();
  // The empty-query case now only powers the Players tab's small curated
  // default list (the header search always passes a real query), so it's
  // capped low rather than returning the full alphabetical roster.
  const limit = trimmed ? 25 : 12;
  return queryObjects(
    `WITH player_signal AS (
       SELECT player_id, COUNT(*) AS game_count
       FROM fact_player_game_log
       GROUP BY player_id
     ),
     current_players AS (
       SELECT
         p.player_id,
         p.full_name,
         p.position,
         p.is_active,
         th.abbreviation AS team_abbreviation,
         COALESCE(ps.game_count, 0) AS game_count,
         COUNT(*) OVER (PARTITION BY p.full_name) AS same_name_count
       FROM dim_player p
       LEFT JOIN dim_team_history th ON th.team_id = p.team_id AND th.is_current
       LEFT JOIN player_signal ps ON ps.player_id = p.player_id
       WHERE p.is_current
         AND (length(?) = 0 OR p.full_name ILIKE ?)
     )
     SELECT player_id, full_name, position, is_active, team_abbreviation
     FROM current_players
     WHERE same_name_count = 1 OR game_count > 0
     ORDER BY full_name
     LIMIT ?`,
    [trimmed, `%${trimmed}%`, limit],
  );
}

// ---------------------------------------------------------------------------
// Home page: featured (random current) player
//
// Career line is recomputed from the imported clean BBR season table rather
// than read from the legacy aggregate layer, which is unreliable.
// ---------------------------------------------------------------------------

export async function getFeaturedPlayer(): Promise<Row | null> {
  const rows = await queryObjects(
    `WITH ${PLAYER_SEASON_STATS_CTE},
     featured AS (
       SELECT player_id FROM dim_player WHERE is_current ORDER BY random() LIMIT 1
     ),
     career AS (
       SELECT
         player_id,
         SUM(gp) AS career_gp,
         SUM(total_pts) / NULLIF(SUM(gp), 0) AS career_ppg
       FROM player_season_stats
       WHERE season_type = 'Regular'
       GROUP BY player_id
     )
     SELECT
       p.player_id, p.full_name, p.position, th.abbreviation AS team_abbreviation,
       c.career_gp,
       c.career_ppg
     FROM featured f
     JOIN dim_player p ON p.player_id = f.player_id AND p.is_current
     LEFT JOIN dim_team_history th ON th.team_id = p.team_id AND th.is_current
     LEFT JOIN career c ON c.player_id = p.player_id`,
  );
  return rows[0] ?? null;
}

// ---------------------------------------------------------------------------
// Per-36 / Per-48 (per-100-possession-style rate) tables
// ---------------------------------------------------------------------------

export async function getPlayerPerRates(playerId: number): Promise<{ per36: Row[]; per48: Row[] }> {
  const [per36, per48] = await Promise.all([
    queryObjects(
      `WITH ${PLAYER_SEASON_STATS_CTE}
       SELECT
         s.player_id,
         s.team_id,
         s.season_year,
         s.season_type,
         s.gp,
         s.avg_min,
         COALESCE(th.abbreviation, s.source_team_abbreviation) AS team_abbreviation,
         s.total_pts * 36 / NULLIF(s.total_min, 0) AS pts_per36,
         s.total_reb * 36 / NULLIF(s.total_min, 0) AS reb_per36,
         s.total_ast * 36 / NULLIF(s.total_min, 0) AS ast_per36,
         s.total_stl * 36 / NULLIF(s.total_min, 0) AS stl_per36,
         s.total_blk * 36 / NULLIF(s.total_min, 0) AS blk_per36,
         s.total_tov * 36 / NULLIF(s.total_min, 0) AS tov_per36,
         false AS is_cup_final_only
       FROM player_season_stats s
       LEFT JOIN dim_team_history th ON th.team_id = s.team_id
       WHERE s.player_id = ?
       QUALIFY ROW_NUMBER() OVER (
         PARTITION BY s.team_id, s.season_year, s.season_type
         ORDER BY
           CASE WHEN s.season_year >= th.valid_from AND (th.valid_to IS NULL OR s.season_year < th.valid_to)
                THEN 0 ELSE 1 END,
           th.valid_from ASC
       ) = 1
       ORDER BY s.season_year, s.season_type`,
      [playerId],
    ),
    queryObjects(
      `WITH ${PLAYER_SEASON_STATS_CTE}
       SELECT
         s.player_id,
         s.team_id,
         s.season_year,
         s.season_type,
         s.gp,
         s.avg_min,
         COALESCE(th.abbreviation, s.source_team_abbreviation) AS team_abbreviation,
         s.total_pts * 48 / NULLIF(s.total_min, 0) AS pts_per48,
         s.total_reb * 48 / NULLIF(s.total_min, 0) AS reb_per48,
         s.total_ast * 48 / NULLIF(s.total_min, 0) AS ast_per48,
         s.total_stl * 48 / NULLIF(s.total_min, 0) AS stl_per48,
         s.total_blk * 48 / NULLIF(s.total_min, 0) AS blk_per48,
         s.total_tov * 48 / NULLIF(s.total_min, 0) AS tov_per48,
         false AS is_cup_final_only
       FROM player_season_stats s
       LEFT JOIN dim_team_history th ON th.team_id = s.team_id
       WHERE s.player_id = ?
       QUALIFY ROW_NUMBER() OVER (
         PARTITION BY s.team_id, s.season_year, s.season_type
         ORDER BY
           CASE WHEN s.season_year >= th.valid_from AND (th.valid_to IS NULL OR s.season_year < th.valid_to)
                THEN 0 ELSE 1 END,
           th.valid_from ASC
       ) = 1
       ORDER BY s.season_year, s.season_type`,
      [playerId],
    ),
  ]);
  return { per36, per48 };
}

// ---------------------------------------------------------------------------
// Advanced stats per season
//
// BBR-derived value metrics come from fact_player_season_stat_resolved; NBA
// tracking-only context (pace/PIE/AST ratio) is overlaid from clean per-game
// advanced facts where available. Same era-matched team_abbreviation trick as
// getPlayerProfile so trades in a single season show the right era
// abbreviation (1996-97 Seattle SuperSonics vs today's Thunder, etc.).
// ---------------------------------------------------------------------------

export async function getPlayerAdvancedStats(playerId: number): Promise<Row[]> {
  return queryObjects(
    `WITH ${PLAYER_SEASON_STATS_CTE},
     nba_tracking AS (
       SELECT
         a.player_id,
         a.team_id,
         a.season_year,
         gl.season_type,
         SUM(a.off_rating * a.poss) / NULLIF(SUM(a.poss), 0) AS avg_off_rating,
         SUM(a.def_rating * a.poss) / NULLIF(SUM(a.poss), 0) AS avg_def_rating,
         SUM(a.net_rating * a.poss) / NULLIF(SUM(a.poss), 0) AS avg_net_rating,
         SUM(a.ts_pct * a.poss) / NULLIF(SUM(a.poss), 0) AS avg_ts_pct,
         SUM(a.usg_pct * a.poss) / NULLIF(SUM(a.poss), 0) AS avg_usg_pct,
         SUM(a.efg_pct * a.poss) / NULLIF(SUM(a.poss), 0) AS avg_efg_pct,
         SUM(a.ast_pct * a.poss) / NULLIF(SUM(a.poss), 0) AS avg_ast_pct,
         SUM(a.ast_ratio * a.poss) / NULLIF(SUM(a.poss), 0) AS avg_ast_ratio,
         SUM(a.oreb_pct * a.poss) / NULLIF(SUM(a.poss), 0) AS avg_oreb_pct,
         SUM(a.dreb_pct * a.poss) / NULLIF(SUM(a.poss), 0) AS avg_dreb_pct,
         SUM(a.reb_pct * a.poss) / NULLIF(SUM(a.poss), 0) AS avg_reb_pct,
         SUM(a.pace * a.poss) / NULLIF(SUM(a.poss), 0) AS avg_pace,
         SUM(a.pie * a.poss) / NULLIF(SUM(a.poss), 0) AS avg_pie
       FROM fact_player_game_advanced a
       JOIN fact_player_game_log gl
         ON gl.game_id = a.game_id
         AND gl.player_id = a.player_id
         AND gl.team_id = a.team_id
       WHERE a.player_id = ?
       GROUP BY a.player_id, a.team_id, a.season_year, gl.season_type
     )
     SELECT
       s.player_id,
       s.season_year,
       s.season_type,
       s.gp,
       COALESCE(n.avg_off_rating, s.avg_off_rating) AS avg_off_rating,
       COALESCE(n.avg_def_rating, s.avg_def_rating) AS avg_def_rating,
       COALESCE(n.avg_net_rating, s.avg_net_rating) AS avg_net_rating,
       COALESCE(n.avg_ts_pct, s.avg_ts_pct) AS avg_ts_pct,
       COALESCE(n.avg_usg_pct, s.avg_usg_pct) AS avg_usg_pct,
       COALESCE(n.avg_efg_pct, s.avg_efg_pct) AS avg_efg_pct,
       COALESCE(n.avg_ast_pct, s.avg_ast_pct) AS avg_ast_pct,
       n.avg_ast_ratio,
       COALESCE(n.avg_oreb_pct, s.avg_oreb_pct) AS avg_oreb_pct,
       COALESCE(n.avg_dreb_pct, s.avg_dreb_pct) AS avg_dreb_pct,
       COALESCE(n.avg_reb_pct, s.avg_reb_pct) AS avg_reb_pct,
       s.avg_tov_pct,
       n.avg_pace,
       n.avg_pie,
       s.per,
       s.ows,
       s.dws,
       s.ws,
       s.obpm,
       s.dbpm,
       s.bpm,
       s.vorp,
       th.abbreviation AS team_abbreviation,
       s.team_id,
       false AS is_cup_final_only
     FROM player_season_stats s
     LEFT JOIN nba_tracking n
       ON n.player_id = s.player_id
       AND n.team_id = s.team_id
       AND n.season_year = s.season_year
       AND n.season_type = s.season_type
     LEFT JOIN dim_team_history th ON th.team_id = s.team_id
     WHERE s.player_id = ?
     QUALIFY ROW_NUMBER() OVER (
       PARTITION BY s.team_id, s.season_year, s.season_type
       ORDER BY
         CASE WHEN s.season_year >= th.valid_from AND (th.valid_to IS NULL OR s.season_year < th.valid_to)
              THEN 0 ELSE 1 END,
         th.valid_from ASC
     ) = 1
     ORDER BY s.season_year, s.season_type`,
    [playerId, playerId],
  );
}

// ---------------------------------------------------------------------------
// Per-100-possession player view
//
// BBR imports carry real per-100 possession rows for the regular season. Those
// rows are preferred over the old pace-estimated aggregate path, which depended
// on the corrupted season aggregate and had no pre-tracking-era coverage.
// ---------------------------------------------------------------------------

export async function getPlayerPer100(playerId: number): Promise<Row[]> {
  return queryObjects(
    `WITH ${PLAYER_BBR_XWALK_CTE},
     per100 AS (
       SELECT
         COALESCE(p.nba_player_id, x.nba_player_id) AS player_id,
         bt.team_id,
         CAST(p.season - 1 AS VARCHAR) || '-' || lpad(CAST(p.season % 100 AS VARCHAR), 2, '0') AS season_year,
         'Regular' AS season_type,
         p.team AS source_team_abbreviation,
         p.g AS gp,
         p.mp AS total_min,
         CAST(NULL AS DOUBLE) AS avg_pace,
         false AS is_cup_final_only,
         p.pts_per_100_poss AS pts_per100,
         p.trb_per_100_poss AS reb_per100,
         p.ast_per_100_poss AS ast_per100,
         p.stl_per_100_poss AS stl_per100,
         p.blk_per_100_poss AS blk_per100,
         p.tov_per_100_poss AS tov_per100,
         p.fg_per_100_poss AS fgm_per100,
         p.fga_per_100_poss AS fga_per100,
         p.x3p_per_100_poss AS fg3m_per100,
         p.x3pa_per_100_poss AS fg3a_per100,
         p.ft_per_100_poss AS ftm_per100,
         p.fta_per_100_poss AS fta_per100
       FROM stg_bref_per_100_poss p
       LEFT JOIN player_bbr_xwalk x
         ON x.bbr_player_id = p.bref_player_id
       LEFT JOIN bridge_team_bbr bt
         ON bt.season = p.season
         AND bt.bbr_abbreviation = p.team
         AND bt.lg = p.lg
       WHERE p.lg = 'NBA'
         AND p.team NOT IN ('TOT', '2TM', '3TM', '4TM', '5TM')
     )
     SELECT
       p.season_year,
       p.season_type,
       COALESCE(th.abbreviation, bt.team_abbreviation, p.source_team_abbreviation) AS team_abbreviation,
       p.team_id,
       p.gp,
       p.total_min,
       p.avg_pace,
       p.is_cup_final_only,
       p.pts_per100,
       p.reb_per100,
       p.ast_per100,
       p.stl_per100,
       p.blk_per100,
       p.tov_per100,
       p.fgm_per100,
       p.fga_per100,
       p.fg3m_per100,
       p.fg3a_per100,
       p.ftm_per100,
       p.fta_per100
     FROM per100 p
     LEFT JOIN bridge_team_bbr bt
       ON bt.team_id = p.team_id
       AND bt.season = CAST(SUBSTRING(p.season_year, 1, 4) AS INTEGER) + 1
     LEFT JOIN dim_team_history th ON th.team_id = p.team_id
     WHERE p.player_id = ?
     QUALIFY ROW_NUMBER() OVER (
       PARTITION BY p.team_id, p.season_year, p.season_type
       ORDER BY
         CASE WHEN p.season_year >= th.valid_from AND (th.valid_to IS NULL OR p.season_year < th.valid_to)
              THEN 0 ELSE 1 END,
         th.valid_from ASC
     ) = 1
     ORDER BY p.season_year, p.season_type`,
    [playerId],
  );
}

// ---------------------------------------------------------------------------
// Career/game highs
//
// fact_player_game_log has one row per player-game with the box-score columns
// needed here. Each stat's high is found independently (a single game rarely
// holds every career high at once), each paired with the date/team on which it
// happened, BBR-style ("50 pts on 3/2/2001 vs LAL").
// ---------------------------------------------------------------------------

const GAME_HIGH_STATS: { key: string; label: string }[] = [
  { key: "pts", label: "Points" },
  { key: "reb", label: "Rebounds" },
  { key: "ast", label: "Assists" },
  { key: "stl", label: "Steals" },
  { key: "blk", label: "Blocks" },
  { key: "fg3m", label: "3-Pointers Made" },
  { key: "fgm", label: "Field Goals Made" },
  { key: "ftm", label: "Free Throws Made" },
];

export async function getPlayerHighs(playerId: number): Promise<Row[]> {
  const unions = GAME_HIGH_STATS.map(
    (s) =>
      `(SELECT '${s.label}' AS stat, ${s.key} AS value, game_date, team_abbreviation
        FROM fact_player_game_log
        WHERE player_id = ? AND ${s.key} IS NOT NULL
        ORDER BY ${s.key} DESC, TRY_CAST(game_date AS DATE) ASC
        LIMIT 1)`,
  );
  return queryObjects(
    unions.join(" UNION ALL "),
    GAME_HIGH_STATS.map(() => playerId),
  );
}

// ---------------------------------------------------------------------------
// Recent games
//
// fact_player_game_log has the box score but its own wl column is coded
// "0"/"1" rather than "W"/"L". fact_team_game_log has the canonical W/L, so
// it's joined in twice — once for the player's own team's result, once for
// the opponent row — mirroring the self-join already used for the team
// page's recentGames query above.
// ---------------------------------------------------------------------------

export async function getPlayerRecentGames(playerId: number): Promise<Row[]> {
  const rows = await queryObjects(
    `WITH player_games AS (
       SELECT * FROM fact_player_game_log
       WHERE player_id = ?
       QUALIFY ROW_NUMBER() OVER (PARTITION BY game_id ORDER BY game_date DESC) = 1
     ),
     team_games AS (
       SELECT * FROM fact_team_game_log
       QUALIFY ROW_NUMBER() OVER (PARTITION BY game_id, team_id ORDER BY game_date DESC) = 1
     )
     SELECT
       pg.game_id, pg.game_date, pg.season_type, pg.season_year,
       pg.team_abbreviation, pg.team_id,
       og.team_abbreviation AS opponent, og.team_id AS opponent_team_id,
       CASE WHEN pg.matchup LIKE '% vs. %' THEN 'Home' ELSE 'Away' END AS location,
       tg.wl AS result,
       pg.min, pg.pts, pg.reb, pg.ast, pg.stl, pg.blk,
       pg.fgm, pg.fga, pg.fg3m, pg.fg3a, pg.ftm, pg.fta, pg.plus_minus
     FROM player_games pg
     LEFT JOIN team_games tg ON tg.game_id = pg.game_id AND tg.team_id = pg.team_id
     LEFT JOIN team_games og ON og.game_id = pg.game_id AND og.team_id <> pg.team_id
     ORDER BY TRY_CAST(pg.game_date AS DATE) DESC
     LIMIT 10`,
    [playerId],
  );
  return rows.map((r) => {
    // season_year is stored like "2025-26"; colorForEra wants the calendar
    // start year, same conversion used for jersey stints above.
    const calendarYear =
      typeof r.season_year === "string" ? Number(r.season_year.slice(0, 4)) : NaN;
    const opponentTeamId = Number(r.opponent_team_id);
    const color = colorForEra(
      typeof r.opponent === "string" ? r.opponent : "",
      calendarYear,
      Number.isFinite(opponentTeamId) ? opponentTeamId : undefined,
    );
    return { ...r, opponent_primary_color: color.primary, opponent_trim_color: color.trim };
  });
}

// ---------------------------------------------------------------------------
// Shooting-location splits
//
// agg_shot_location_season is a near-empty aggregate (only player_id,
// season_year, fgm, season_fgm_rank). agg_shot_zones has the right total shot
// count, but its source labels are incomplete: corner threes and the LC/RC
// wing bands are missing, and some 3PT rows carry impossible ranges such as
// "Less Than 8 ft.". Derive the NBA Stats-style zone taxonomy from
// fact_shot_chart.loc_x/loc_y instead:
//   - loc_x/loc_y are tenths of feet from the hoop center.
//   - Corner 3s are outside the straight three-point lines at +/-22 ft.
//   - The straight corner line meets the 23'9" arc about 9 ft above the hoop.
//   - Restricted Area follows the official four-foot semicircle; the NBA
//     Stats range label for it is still "Less Than 8 ft.".
// League-wide averages per (season, derived zone) are joined in so the UI can
// show a BBR-style "league avg" column without a separate feature.
// ---------------------------------------------------------------------------

export async function getPlayerShotSplits(playerId: number): Promise<Row[]> {
  return queryObjects(
    `WITH player_contexts AS (
       SELECT DISTINCT season_year, season_type
       FROM fact_player_game_log
       WHERE player_id = ?
     ),
     shot_base AS (
       SELECT
         f.player_id,
         gl.season_year,
         gl.season_type,
         f.shot_type,
         f.loc_x,
         f.loc_y,
         f.shot_made_flag,
         sqrt(f.loc_x * f.loc_x + f.loc_y * f.loc_y) / 10.0 AS shot_distance
       FROM fact_shot_chart f
       JOIN fact_player_game_log gl
         ON gl.player_id = f.player_id
         AND gl.game_id = f.game_id
         AND gl.season_year = f.season_year
       JOIN player_contexts pc
         ON pc.season_year = gl.season_year
         AND pc.season_type = gl.season_type
     ),
     classified AS (
       SELECT
         player_id,
         season_year,
         season_type,
         CASE
           WHEN shot_distance >= 35 THEN 'Backcourt'
           WHEN shot_type = '3PT Field Goal' AND loc_x <= -220 AND loc_y <= 90 THEN 'Left Corner 3'
           WHEN shot_type = '3PT Field Goal' AND loc_x >= 220 AND loc_y <= 90 THEN 'Right Corner 3'
           WHEN shot_type = '3PT Field Goal' THEN 'Above the Break 3'
           WHEN shot_distance <= 4 THEN 'Restricted Area'
           WHEN shot_distance < 8 THEN 'In The Paint (Non-RA)'
           WHEN shot_distance < 16 AND loc_y >= 0 AND (abs(loc_x) <= 80 OR loc_y >= 80)
             THEN 'In The Paint (Non-RA)'
           ELSE 'Mid-Range'
         END AS shot_zone_basic,
         CASE
           WHEN shot_distance >= 35 THEN 'Back Court(BC)'
           WHEN shot_type = '3PT Field Goal' AND loc_x <= -220 AND loc_y <= 90 THEN 'Left Side(L)'
           WHEN shot_type = '3PT Field Goal' AND loc_x >= 220 AND loc_y <= 90 THEN 'Right Side(R)'
           WHEN shot_type = '3PT Field Goal' AND loc_x < -80 THEN 'Left Side Center(LC)'
           WHEN shot_type = '3PT Field Goal' AND loc_x > 80 THEN 'Right Side Center(RC)'
           WHEN shot_type = '3PT Field Goal' THEN 'Center(C)'
           WHEN shot_distance <= 4 THEN 'Center(C)'
           WHEN shot_distance < 8 THEN 'Center(C)'
           WHEN shot_distance < 16 AND loc_y >= 0 AND (abs(loc_x) <= 80 OR loc_y >= 80) AND loc_x < -80
             THEN 'Left Side(L)'
           WHEN shot_distance < 16 AND loc_y >= 0 AND (abs(loc_x) <= 80 OR loc_y >= 80) AND loc_x > 80
             THEN 'Right Side(R)'
           WHEN shot_distance < 16 AND loc_y >= 0 AND (abs(loc_x) <= 80 OR loc_y >= 80)
             THEN 'Center(C)'
           WHEN abs(loc_x) <= 80 THEN 'Center(C)'
           WHEN loc_x < 0 AND loc_y < 80 THEN 'Left Side(L)'
           WHEN loc_x < 0 THEN 'Left Side Center(LC)'
           WHEN loc_x > 0 AND loc_y < 80 THEN 'Right Side(R)'
           ELSE 'Right Side Center(RC)'
         END AS shot_zone_area,
         CASE
           WHEN shot_distance >= 35 THEN 'Back Court Shot'
           WHEN shot_type = '3PT Field Goal' THEN '24+ ft.'
           WHEN shot_distance < 8 THEN 'Less Than 8 ft.'
           WHEN shot_distance < 16 THEN '8-16 ft.'
           ELSE '16-24 ft.'
         END AS shot_zone_range,
         shot_made_flag,
         shot_distance
       FROM shot_base
     ),
     player_zones AS (
       SELECT
         season_year,
         season_type,
         shot_zone_basic,
         shot_zone_area,
         shot_zone_range,
         COUNT(*) AS attempts,
         SUM(shot_made_flag) AS makes,
         SUM(shot_made_flag) / NULLIF(COUNT(*), 0) AS fg_pct,
         AVG(shot_distance) AS avg_distance
       FROM classified
       WHERE player_id = ?
       GROUP BY season_year, season_type, shot_zone_basic, shot_zone_area, shot_zone_range
     ),
     league_avg AS (
       SELECT
         season_year,
         season_type,
         shot_zone_basic,
         SUM(shot_made_flag) AS league_makes,
         COUNT(*) AS league_attempts
       FROM classified
       GROUP BY season_year, season_type, shot_zone_basic
     )
     SELECT
       pz.season_year,
       pz.season_type,
       pz.shot_zone_basic,
       pz.shot_zone_area,
       pz.shot_zone_range,
       pz.attempts,
       pz.makes,
       pz.fg_pct,
       pz.avg_distance,
       la.league_makes / NULLIF(la.league_attempts, 0) AS league_fg_pct
     FROM player_zones pz
     LEFT JOIN league_avg la
       ON la.season_year = pz.season_year
       AND la.season_type = pz.season_type
       AND la.shot_zone_basic = pz.shot_zone_basic
     ORDER BY pz.season_year, pz.season_type, pz.shot_zone_basic, pz.shot_zone_area, pz.shot_zone_range`,
    [playerId, playerId],
  );
}

// ---------------------------------------------------------------------------
// On/off splits
//
// agg_on_off_splits carries both player- and team-level rows (entity_type);
// only the player rows are surfaced here.
// ---------------------------------------------------------------------------

export async function getPlayerOnOffSplits(playerId: number): Promise<Row[]> {
  return queryObjects(
    `SELECT season_year, season_type, on_off, gp, min, pts, reb, ast, off_rating, def_rating, net_rating
     FROM agg_on_off_splits
     WHERE entity_type = 'player' AND entity_id = ?
     ORDER BY season_year, season_type, on_off`,
    [playerId],
  );
}

// ---------------------------------------------------------------------------
// Form tracker (rolling averages)
//
// agg_player_rolling has precomputed 5/10/20-game rolling pts/reb/ast for
// every player game back to 1946, one clean row per (player_id, game_id);
// the window INCLUDES the current game (verified: Kobe's 81-point night on
// 2006-01-22 shows pts_roll5 = 48.8 = avg of that game plus the prior four).
// Per-game actuals come from analytics_player_game_complete, which has the
// known agg-layer fan-out (duplicate player-game rows), so it is deduped
// with any_value() before joining.
// ---------------------------------------------------------------------------

export async function getPlayerFormTracker(playerId: number, limit = 40): Promise<Row[]> {
  return queryObjects(
    `WITH game_line AS (
       SELECT
         game_id,
         any_value(pts) AS pts,
         any_value(reb) AS reb,
         any_value(ast) AS ast,
         any_value(team_abbreviation) AS team_abbreviation,
         any_value(season_year) AS season_year
       FROM analytics_player_game_complete
       WHERE player_id = ?
       GROUP BY game_id
     )
     SELECT
       r.game_id,
       r.game_date,
       g.season_year,
       g.team_abbreviation,
       g.pts,
       g.reb,
       g.ast,
       r.pts_roll5,
       r.pts_roll10,
       r.pts_roll20,
       r.reb_roll5,
       r.reb_roll10,
       r.ast_roll5,
       r.ast_roll10
     FROM agg_player_rolling r
     LEFT JOIN game_line g ON g.game_id = r.game_id
     WHERE r.player_id = ?
     ORDER BY r.game_date DESC
     LIMIT ?`,
    [playerId, playerId, limit],
  );
}

// ---------------------------------------------------------------------------
// Draft combine measurements
//
// The four stg_draft_combine* staging tables are queried directly (never
// promoted to warehouse tables) and matched on player_id; a player only has
// a match if they attended an NBA combine (draft classes back to ~2000).
// ---------------------------------------------------------------------------

export async function getPlayerDraftCombine(playerId: number): Promise<Row | null> {
  const rows = await queryObjects(
    `SELECT
       c.season,
       c.height_wo_shoes, c.height_w_shoes, c.weight, c.wingspan, c.standing_reach,
       c.body_fat_pct, c.hand_length, c.hand_width,
       d.standing_vertical_leap, d.max_vertical_leap, d.lane_agility_time,
       d.modified_lane_agility_time, d.three_quarter_sprint, d.bench_press
     FROM stg_draft_combine c
     LEFT JOIN stg_draft_combine_drills d ON d.player_id = c.player_id AND d.season = c.season
     WHERE c.player_id = ?
     ORDER BY c.season DESC
     LIMIT 1`,
    [playerId],
  );
  return rows[0] ?? null;
}

// ---------------------------------------------------------------------------
// Similar players
//
// A lightweight "similarity score" over career per-game rate stats
// (recomputed the same games-weighted way as the career summary above), not
// a BBR-equivalent model — just nearest-neighbor by Euclidean distance
// across points/rebounds/assists/steals/blocks/3PM per game plus true
// shooting-ish efficiency, restricted to players with at least 100 career
// games so single-season call-ups don't dominate the neighbor list.
// ---------------------------------------------------------------------------

export async function getSimilarPlayers(playerId: number, limit = 10): Promise<Row[]> {
  return queryObjects(
    `WITH ${PLAYER_SEASON_STATS_CTE},
     career AS (
       SELECT
         player_id,
         SUM(gp) AS career_gp,
         SUM(total_pts) / NULLIF(SUM(gp), 0) AS ppg,
         SUM(total_reb) / NULLIF(SUM(gp), 0) AS rpg,
         SUM(total_ast) / NULLIF(SUM(gp), 0) AS apg,
         SUM(total_stl) / NULLIF(SUM(gp), 0) AS spg,
         SUM(total_blk) / NULLIF(SUM(gp), 0) AS bpg,
         SUM(total_fg3m) / NULLIF(SUM(gp), 0) AS fg3mpg
       FROM player_season_stats
       WHERE season_type = 'Regular'
       GROUP BY player_id
       HAVING SUM(gp) >= 100
     ),
     target AS (SELECT * FROM career WHERE player_id = ?)
     SELECT
       p.player_id, p.full_name, p.position,
       c.career_gp, c.ppg, c.rpg, c.apg, c.spg, c.bpg, c.fg3mpg,
       SQRT(
         POWER(c.ppg - t.ppg, 2) + POWER(c.rpg - t.rpg, 2) + POWER(c.apg - t.apg, 2) +
         POWER(c.spg - t.spg, 2) * 4 + POWER(c.bpg - t.bpg, 2) * 4 + POWER(c.fg3mpg - t.fg3mpg, 2)
       ) AS distance
     FROM career c
     CROSS JOIN target t
     JOIN dim_player p ON p.player_id = c.player_id AND p.is_current
     WHERE c.player_id != t.player_id
     ORDER BY distance ASC
     LIMIT ?`,
    [playerId, limit],
  );
}

export interface Badge {
  season: string;
  label: string;
}

export interface JerseyStint {
  team_id: number;
  abbreviation: string;
  team_name: string;
  jersey_num: string;
  start_year: number;
  end_year: number;
  primary: string;
  trim: string;
}

interface JerseySeasonRow {
  team_id: number;
  jersey_num: string;
  season_year: string;
  stint_group: number;
  abbreviation: string;
  team_name: string;
}

/** Splits each (team, number) stint further wherever the team's jersey
 *  color changed mid-stint (e.g. a rebrand with no relocation/rename, like
 *  Detroit's 1996-97 switch to teal — same team_id, same player could keep
 *  the same number straight through it). Operates on the season-year start
 *  (e.g. "2003-04" -> 2003) since that's what TEAM_COLOR_ERAS.from uses. */
function splitJerseyStintsByColorEra(rows: JerseySeasonRow[]): JerseyStint[] {
  const stints: JerseyStint[] = [];
  let current: {
    row: JerseySeasonRow;
    color: { primary: string; trim: string };
    firstYear: number;
    lastYear: number;
  } | null = null;

  for (const row of rows) {
    const calendarYear = Number(row.season_year.slice(0, 4));
    const color = colorForEra(row.abbreviation, calendarYear, row.team_id);
    const sameRun =
      current?.row.team_id === row.team_id &&
      current?.row.abbreviation === row.abbreviation &&
      current?.row.team_name === row.team_name &&
      current?.row.jersey_num === row.jersey_num &&
      current?.row.stint_group === row.stint_group &&
      current?.color.primary === color.primary &&
      current?.color.trim === color.trim;

    if (sameRun && current) {
      current.lastYear = calendarYear;
    } else {
      if (current) stints.push(finalizeJerseyStint(current));
      current = { row, color, firstYear: calendarYear, lastYear: calendarYear };
    }
  }
  if (current) stints.push(finalizeJerseyStint(current));
  return stints;
}

function finalizeJerseyStint(current: {
  row: JerseySeasonRow;
  color: { primary: string; trim: string };
  firstYear: number;
  lastYear: number;
}): JerseyStint {
  return {
    team_id: current.row.team_id,
    abbreviation: current.row.abbreviation,
    team_name: current.row.team_name,
    jersey_num: current.row.jersey_num,
    start_year: current.firstYear,
    end_year: current.lastYear + 1,
    primary: current.color.primary,
    trim: current.color.trim,
  };
}

export interface PlayerProfile {
  bio: Row | null;
  career: Row | null;
  seasons: Row[];
  awards: Row[];
  draft: Row | null;
  hallOfFameYear: number | null;
  isGreatest75: boolean;
  allStarCount: number;
  careerEfgPct: number | null;
  badges: Badge[];
  jerseyHistory: JerseyStint[];
}

/** Formats an award end-year ("1969") as a BBR-style season range ("1968-69").
 *  League-leader badges already use season-year range form. */
function seasonRangeFromEndYear(yearLike: unknown): string {
  const year = Number(yearLike);
  if (!Number.isFinite(year)) return String(yearLike);
  return `${year - 1}-${String(year).slice(-2)}`;
}

export async function getPlayerProfile(playerId: number): Promise<PlayerProfile> {
  const [
    bioRows,
    commonInfoRows,
    career,
    seasons,
    awards,
    draft,
    hofRows,
    allStarRows,
    honorRows,
    leaderRows,
    jerseyRows,
  ] = await Promise.all([
    queryObjects(
      `WITH ${PLAYER_BREF_BIO_CTE}
       SELECT
         p.* REPLACE (
           COALESCE(bb.position, p.position) AS position,
           COALESCE(bb.height, p.height) AS height,
           COALESCE(bb.weight, p.weight) AS weight,
           COALESCE(bb.birth_date, p.birth_date) AS birth_date
         ),
         bb.school,
         th.abbreviation AS team_abbreviation,
         th.nickname AS team_name
       FROM dim_player p
       LEFT JOIN bref_player_bio bb ON bb.player_id = p.player_id
       LEFT JOIN dim_team_history th ON th.team_id = p.team_id AND th.is_current
       WHERE p.player_id = ? AND p.is_current
       LIMIT 1`,
      [playerId],
    ),
    // common_player_info carries BBR-header fields dim_player doesn't have:
    // school (college *or* high school — the source doesn't distinguish),
    // country, full position name, and season_exp (career length in years).
    queryObjects(
      `SELECT * FROM common_player_info WHERE TRY_CAST(person_id AS BIGINT) = ? LIMIT 1`,
      [playerId],
    ),
    // Legacy agg_player_career / agg_player_season rows are not used for
    // player-facing career totals. BBR's resolved season table carries clean
    // regular-season totals across all eras, with bridge fallback for source
    // rows whose original player id was unresolved.
    queryObjects(
      `WITH ${PLAYER_SEASON_STATS_CTE}
       SELECT
         SUM(gp) AS career_gp,
         SUM(total_min) AS career_min,
         SUM(total_pts) AS career_pts,
         SUM(total_reb) AS career_reb,
         SUM(total_ast) AS career_ast,
         SUM(total_stl) AS career_stl,
         SUM(total_blk) AS career_blk,
         SUM(total_pts) / NULLIF(SUM(gp), 0) AS career_ppg,
         SUM(total_reb) / NULLIF(SUM(gp), 0) AS career_rpg,
         SUM(total_ast) / NULLIF(SUM(gp), 0) AS career_apg,
         SUM(total_fgm) / NULLIF(SUM(total_fga), 0) AS career_fg_pct,
         SUM(total_fg3m) / NULLIF(SUM(total_fg3a), 0) AS career_fg3_pct,
         SUM(total_ftm) / NULLIF(SUM(total_fta), 0) AS career_ft_pct,
         (SUM(total_fgm) + 0.5 * SUM(total_fg3m)) / NULLIF(SUM(total_fga), 0) AS career_efg_pct
       FROM player_season_stats
       WHERE player_id = ? AND season_type = 'Regular'`,
      [playerId],
    ),
    queryObjects(
      // The BBR source team abbreviation can differ from this app's
      // dim_team_history abbreviation (e.g. BRK vs BKN, GS vs GSW), so display
      // abbreviations are re-derived from team_id and season era.
      `WITH ${PLAYER_SEASON_STATS_CTE}
       SELECT s.* EXCLUDE (source_team_abbreviation),
              COALESCE(th.abbreviation, s.source_team_abbreviation) AS team_abbreviation,
              false AS is_cup_final_only
       FROM player_season_stats s
       LEFT JOIN dim_team_history th ON th.team_id = s.team_id
       WHERE s.player_id = ?
       QUALIFY ROW_NUMBER() OVER (
         PARTITION BY s.team_id, s.season_year, s.season_type
         ORDER BY
           CASE WHEN s.season_year >= th.valid_from AND (th.valid_to IS NULL OR s.season_year < th.valid_to)
                THEN 0 ELSE 1 END,
           th.valid_from ASC
       ) = 1
       ORDER BY s.season_year, s.season_type`,
      [playerId],
    ),
    // Player-facing awards come from the lossless BBR staging tables via the
    // player bridge. Only real selections/winners are surfaced.
    queryObjects(
      `WITH ${PLAYER_AWARD_ROWS_CTE}
       SELECT season, award_type, description
       FROM award_rows
       WHERE player_id = ?
       ORDER BY season, award_type`,
      [playerId],
    ),
    queryObjects(
      `WITH ${DRAFT_SOURCE_CTE}
       SELECT *
       FROM draft_source
       WHERE person_id = ?
       ORDER BY season
       LIMIT 1`,
      [playerId],
    ),
    // stg_team_retired is mislabeled — its actual content is Hall of Fame
    // induction records, not retired jersey numbers. Verified against known
    // inductions (Magic Johnson 2002, Kobe Bryant 2020, Dirk Nowitzki 2023);
    // its "jersey" column is unpopulated (NULL) so it can't supply retired-
    // number banners. A player can have multiple rows here (one per team
    // they were affiliated with) but `year` is identical across them.
    queryObjects(`SELECT DISTINCT year FROM stg_team_retired WHERE playerid = ? LIMIT 1`, [
      playerId,
    ]),
    queryObjects(
      `WITH ${PLAYER_AWARD_ROWS_CTE}
       SELECT COUNT(*) AS n
       FROM award_rows
       WHERE player_id = ? AND award_type = 'All-Star'`,
      [playerId],
    ),
    // Award rows are already filtered to real selections/winners in the CTE.
    queryObjects(
      `WITH ${PLAYER_AWARD_ROWS_CTE}
       SELECT season, award_type
       FROM award_rows
       WHERE player_id = ?
         AND award_type IN (
           'All-NBA', 'All-Rookie', 'All-Defense',
           'nba mvp', 'nba roy', 'nba dpoy', 'nba mip', 'nba smoy'
         )
       ORDER BY season`,
      [playerId],
    ),
    // League-leader "Champ" badges (e.g. BBR's "1974-75 TRB Champ"), regular
    // season only — statistical titles aren't awarded for the playoffs.
    // Recompute ranks from the clean BBR season rows rather than
    // agg_league_leaders.
    queryObjects(
      `WITH ${PLAYER_SEASON_STATS_CTE},
       ranked AS (
         SELECT
           player_id,
           season_year,
           RANK() OVER (PARTITION BY season_year ORDER BY avg_pts DESC NULLS LAST) AS pts_rank,
           RANK() OVER (PARTITION BY season_year ORDER BY avg_reb DESC NULLS LAST) AS reb_rank,
           RANK() OVER (PARTITION BY season_year ORDER BY avg_ast DESC NULLS LAST) AS ast_rank,
           RANK() OVER (PARTITION BY season_year ORDER BY avg_stl DESC NULLS LAST) AS stl_rank,
           RANK() OVER (PARTITION BY season_year ORDER BY avg_blk DESC NULLS LAST) AS blk_rank
         FROM player_season_stats
         WHERE season_type = 'Regular' AND gp > 0
       )
       SELECT season_year, 'Scoring Champ' AS label FROM ranked
        WHERE player_id = ? AND pts_rank = 1
       UNION ALL
       SELECT season_year, 'Rebounding Champ' FROM ranked
        WHERE player_id = ? AND reb_rank = 1 AND season_year >= '1950-51'
       UNION ALL
       SELECT season_year, 'Assists Champ' FROM ranked
        WHERE player_id = ? AND ast_rank = 1 AND season_year >= '1949-50'
       UNION ALL
       SELECT season_year, 'Steals Champ' FROM ranked
        WHERE player_id = ? AND stl_rank = 1 AND season_year >= '1973-74'
       UNION ALL
       SELECT season_year, 'Blocks Champ' FROM ranked
        WHERE player_id = ? AND blk_rank = 1 AND season_year >= '1973-74'
       ORDER BY 1`,
      [playerId, playerId, playerId, playerId, playerId],
    ),
    // Jersey numbers worn per team-stint, for the BBR-style jersey graphic.
    // dim_player.jersey_number and bridge_player_team_season.jersey_number
    // are both stale — they show "23" for every LeBron James season
    // including his Miami Heat years, when he actually wore 6 there. The
    // real number is recovered from inactive_players (a per-game scratch/
    // DNP list that happens to capture the jersey worn at the time),
    // joined to `game` for the date. Verified against well-known real
    // history for LeBron and Curry (30 GSW throughout, one stint).
    //
    // bridge_player_team_season is used as a **per-player** fallback when
    // the player has zero rows in inactive_players at all (typically pre-
    // 1996-97 players, since inactive_players coverage starts 1996-97). It
    // is NOT used to fill individual season gaps for players who already
    // have inactive_players data — the stale bridge rows would inject
    // wrong numbers (e.g. MIA #23 for LeBron's 2011-12 / 2012-13 gap years
    // when he actually wore #6 the whole time, splitting his MIA#6 stint).
    //
    // The BBR-scraped roster file is the authoritative fallback when
    // inactive_players has no rows for the player. It is ranked ahead of
    // bridge_player_team_season per (team, season), because bridge can carry
    // stale current jersey numbers into historical seasons. Verified for
    // Pete Maravich (player 77459): bridge says ATL/BOS #7, while BBR roster
    // pages correctly say ATL/BOS #44.
    //
    // A plain GROUP BY (team_id, jersey_num) is wrong: LeBron wore 23 for
    // two non-contiguous Cleveland stints (2003-10, then 2014-18 after the
    // Miami years), which a naive group collapses into one bogus
    // "2003-2018" span. This needs gaps-and-islands grouping: bucket to one
    // (team, number) per season first (majority vote, for the rare in-season
    // trade/number-change), then split into a new group wherever the
    // (team, number) at position N in the chronological *filtered* sequence
    // differs from position N-1 — the classic
    // `ROW_NUMBER() OVER (ORDER BY season) - ROW_NUMBER() OVER (PARTITION BY team, number ORDER BY season)`
    // trick. Verified against LeBron: produces exactly 5 stints (CLE#23
    // 2003-10, MIA#6 2010-14, CLE#23 2014-18, LAL#23 2018-21, LAL#6
    // 2021-23), matching real history.
    //
    // Coverage starts 1996-97 (`inactive_players`' earliest game) and only
    // includes players/stints with at least one tracked inactive-game
    // appearance, so this is sparse/empty for some players — not a complete
    // jersey history. The INNER JOIN to dim_team_history both supplies the
    // team name/abbreviation and filters out non-franchise team_ids that
    // pollute inactive_players (All-Star teams, international exhibition
    // opponents like Real Madrid/CSKA), since those have no
    // dim_team_history row at all. dim_team_history itself only has rows
    // from 1996-97 onward, so for bridge-only pre-1996-07 stints the
    // valid_from range check fails for every row; the `is_current` clause
    // falls back to the current-era name for that team_id, and the QUALIFY
    // picks the earliest valid_from when a franchise has split rows
    // (NJN→BKN, SEA→OKC, etc.) so the pre-era stint gets the original name.
    // For jersey stints, the SELECT also consults dim_team.year_founded so
    // pre-1996 historical names such as New Orleans Jazz (warehouse
    // abbreviation NEO) can be displayed even though dim_team_history starts
    // at 1996-97.
    // SQL and params are built by buildJerseyQuery (above) so the
    // BBR roster CTE is spliced in only when its JSONL file
    // is on disk. The first two placeholders bind to per_game and
    // bridge_dedup; a third is added when the BBR CTE is present.
    (() => {
      const { sql, params } = buildJerseyQuery(playerId);
      return queryObjects(sql, params);
    })(),
  ]);

  const honorBadges: Badge[] = honorRows.map((r) => ({
    season: seasonRangeFromEndYear(r.season),
    label: HONOR_LABELS[String(r.award_type)] ?? String(r.award_type),
  }));
  const leaderBadges: Badge[] = leaderRows.map((r) => ({
    season: String(r.season_year),
    label: String(r.label),
  }));
  const badges = [...honorBadges, ...leaderBadges].sort((a, b) => a.season.localeCompare(b.season));

  const bio = bioRows[0] ? { ...bioRows[0], ...commonInfoRows[0] } : null;
  const hofYear = hofRows[0]?.year;
  const efg = career[0]?.career_efg_pct;

  return {
    bio,
    career: career[0] ?? null,
    seasons,
    awards,
    draft: draft[0] ?? null,
    hallOfFameYear: hofYear !== undefined && hofYear !== null ? Number(hofYear) : null,
    isGreatest75: bio?.greatest_75_flag === "Y",
    allStarCount: Number(allStarRows[0]?.n ?? 0),
    careerEfgPct: efg !== undefined && efg !== null ? Number(efg) : null,
    badges,
    jerseyHistory: splitJerseyStintsByColorEra(
      jerseyRows.map((r) => ({
        team_id: Number(r.team_id),
        jersey_num: String(r.jersey_num),
        season_year: String(r.season_year),
        stint_group: Number(r.stint_group),
        abbreviation: String(r.abbreviation),
        team_name: String(r.team_name),
      })),
    ),
  };
}

// ---------------------------------------------------------------------------
// Teams
//
// dim_team_history (is_current=true) is the canonical "30 real franchises"
// list with clean names; dim_team has duplicate per-era rows for relocated
// franchises (e.g. Minneapolis vs Los Angeles Lakers) and always-NULL
// conference/division, so conference/division come from fact_standings
// instead.
// ---------------------------------------------------------------------------

export async function searchTeams(q: string): Promise<Row[]> {
  const trimmed = q.trim();
  // As with searchPlayers, the empty-query case now only powers the Teams
  // tab's small curated default list, not a full 30-team browse.
  const limit = trimmed ? 40 : 12;
  return queryObjects(
    `SELECT team_id, nickname AS team_name, city, abbreviation
     FROM dim_team_history
     WHERE is_current AND (nickname ILIKE ? OR city ILIKE ? OR abbreviation ILIKE ?)
     ORDER BY nickname
     LIMIT ?`,
    [`%${trimmed}%`, `%${trimmed}%`, `%${trimmed}%`, limit],
  );
}

// ---------------------------------------------------------------------------
// Home page: teams grouped by conference (current standings)
// ---------------------------------------------------------------------------

export async function getTeamsByConference(): Promise<Row[]> {
  return queryObjects(
    `WITH latest_standing AS (
       SELECT *
       FROM fact_standings
       QUALIFY ROW_NUMBER() OVER (
         PARTITION BY team_id
         ORDER BY
           season_year DESC,
           CASE season_type WHEN 'Regular' THEN 0 WHEN 'Playoffs' THEN 1 ELSE 2 END
       ) = 1
     )
     SELECT th.team_id, th.nickname AS team_name, th.abbreviation, ls.conference
     FROM dim_team_history th
     JOIN latest_standing ls ON ls.team_id = th.team_id
     WHERE th.is_current
     ORDER BY ls.conference, th.nickname`,
  );
}

export interface TeamProfile {
  bio: Row | null;
  currentStanding: Row | null;
  seasons: Row[];
  franchiseHistory: Row[];
  recentGames: Row[];
  franchiseTotals: Row | null;
  franchiseAlumni: Row[];
}

export async function getTeamProfile(teamId: number): Promise<TeamProfile> {
  const [
    identity,
    extra,
    details,
    currentStanding,
    seasons,
    franchiseHistory,
    recentGames,
    franchiseTotals,
    franchiseAlumni,
  ] = await Promise.all([
    queryObjects(`SELECT * FROM dim_team_history WHERE team_id = ? AND is_current LIMIT 1`, [
      teamId,
    ]),
    queryObjects(
      `SELECT arena, year_founded FROM dim_team WHERE team_id = ? ORDER BY year_founded DESC LIMIT 1`,
      [teamId],
    ),
    // team_details (fact_team_background) carries bio fields dim_team never
    // populated: arena capacity, owner, GM, current head coach, D-League
    // affiliate, and social links. team_id is stored as VARCHAR there.
    queryObjects(
      `SELECT arenacapacity, owner, generalmanager, headcoach, dleagueaffiliation,
                facebook, instagram, twitter
         FROM team_details WHERE TRY_CAST(team_id AS BIGINT) = ? LIMIT 1`,
      [teamId],
    ),
    queryObjects(
      `SELECT *
       FROM fact_standings
       WHERE team_id = ?
       ORDER BY
         season_year DESC,
         CASE season_type WHEN 'Regular' THEN 0 WHEN 'Playoffs' THEN 1 ELSE 2 END
       LIMIT 1`,
      [teamId],
    ),
    queryObjects(
      `WITH regular_seasons AS (
         SELECT
           pg.nba_team_id AS team_id,
           pg.team AS team_name,
           pg.abbreviation AS team_abbreviation,
           CAST(pg.season - 1 AS VARCHAR) || '-' || lpad(CAST(pg.season % 100 AS VARCHAR), 2, '0') AS season_year,
           'Regular' AS season_type,
           COALESCE(fs.wins + fs.losses, pg.g) AS gp,
           pg.pts_per_game AS avg_pts,
           pg.trb_per_game AS avg_reb,
           pg.ast_per_game AS avg_ast,
           pg.fg_percent AS fg_pct,
           ts.pace AS avg_pace,
           ts.o_rtg AS avg_ortg,
           ts.d_rtg AS avg_drtg,
           ts.n_rtg AS avg_net_rtg
         FROM stg_bref_team_stats_per_game pg
         LEFT JOIN stg_bref_team_summaries ts
           ON ts.nba_team_id = pg.nba_team_id
           AND ts.season = pg.season
           AND ts.lg = pg.lg
         LEFT JOIN fact_standings fs
           ON fs.team_id = pg.nba_team_id
           AND fs.season_year = CAST(pg.season - 1 AS VARCHAR) || '-' || lpad(CAST(pg.season % 100 AS VARCHAR), 2, '0')
           AND fs.season_type = 'Regular'
         WHERE pg.lg = 'NBA'
           AND pg.nba_team_id = ?
       ),
       non_regular_seasons AS (
         SELECT
           s.team_id,
           th.nickname AS team_name,
           th.abbreviation AS team_abbreviation,
           s.season_year,
           s.season_type,
           s.gp,
           s.avg_pts,
           s.avg_reb,
           s.avg_ast,
           s.fg_pct,
           p.avg_pace,
           p.avg_ortg,
           p.avg_drtg,
           p.avg_net_rtg
         FROM agg_team_season s
         LEFT JOIN dim_team_history th
           ON th.team_id = s.team_id
           AND left(s.season_year, 4) >= th.valid_from
           AND (th.valid_to IS NULL OR left(s.season_year, 4) < th.valid_to)
         LEFT JOIN agg_team_pace_and_efficiency p
           ON p.team_id = s.team_id
           AND p.season_year = s.season_year
           AND p.season_type = s.season_type
         WHERE s.team_id = ?
           AND s.season_type <> 'Regular'
         QUALIFY ROW_NUMBER() OVER (
           PARTITION BY s.team_id, s.season_year, s.season_type
           ORDER BY
             CASE WHEN th.team_id IS NOT NULL THEN 0 ELSE 1 END,
             th.valid_from DESC NULLS LAST
         ) = 1
       ),
       all_seasons AS (
         SELECT * FROM regular_seasons
         UNION ALL
         SELECT * FROM non_regular_seasons
       )
       SELECT * FROM all_seasons
       ORDER BY
         season_year DESC,
         CASE season_type WHEN 'Regular' THEN 0 WHEN 'Cup' THEN 1 WHEN 'Playoffs' THEN 2 ELSE 3 END`,
      [teamId, teamId],
    ),
    queryObjects(`SELECT * FROM dim_team_history WHERE team_id = ? ORDER BY valid_from`, [teamId]),
    queryObjects(
      `WITH team_games AS (
           SELECT *
           FROM fact_team_game_log
           WHERE team_id = ?
           QUALIFY ROW_NUMBER() OVER (PARTITION BY game_id, team_id ORDER BY game_date DESC) = 1
         ),
         opponent_games AS (
           SELECT *
           FROM fact_team_game_log
           WHERE team_id <> ?
           QUALIFY ROW_NUMBER() OVER (PARTITION BY game_id, team_id ORDER BY game_date DESC) = 1
         )
         SELECT
           tg.game_id,
           tg.game_date,
           og.team_abbreviation AS opponent,
           CASE WHEN tg.matchup LIKE '% vs. %' THEN 'Home' ELSE 'Away' END AS "location",
           tg.pts AS team_pts,
           og.pts AS opp_pts,
           tg.wl AS result
         FROM team_games tg
         LEFT JOIN opponent_games og ON og.game_id = tg.game_id
         ORDER BY TRY_CAST(tg.game_date AS DATE) DESC
         LIMIT 20`,
      [teamId, teamId],
    ),
    // agg_team_franchise has useful all-time W/L rows, but its title fields
    // are all zero/null and current franchises use an end_year sentinel of
    // 2100. Keep the useful totals and blank the known-bad title/age signals.
    queryObjects(
      `SELECT
         team_id,
         team_city,
         team_name,
         start_year,
         CASE WHEN end_year = 2100 THEN NULL ELSE end_year END AS end_year,
         years,
         games,
         wins,
         losses,
         win_pct,
         po_appearances,
         CAST(NULL AS BIGINT) AS div_titles,
         CAST(NULL AS BIGINT) AS conf_titles,
         CAST(NULL AS BIGINT) AS league_titles,
         years AS franchise_age_years,
         computed_win_pct
       FROM agg_team_franchise
       WHERE team_id = ?
       LIMIT 1`,
      [teamId],
    ),
    // Top-15 career-alumni list from fact_franchise_players, regular
    // season totals only (the same player has Regular + Playoffs +
    // sometimes Cup rows in the source).
    queryObjects(
      `SELECT
           fp.person_id AS player_id,
           fp.player AS source_player_name,
           p.full_name,
           fp.gp,
           fp.pts,
           fp.ast,
           fp.reb,
           fp.stl,
           fp.blk,
           fp.fg_pct,
           fp.fg3_pct,
           fp.ft_pct
         FROM fact_franchise_players fp
         JOIN dim_player p ON p.player_id = fp.person_id AND p.is_current
         WHERE fp.team_id = ?
           AND fp.season_type = 'Regular'
         ORDER BY fp.gp DESC NULLS LAST, p.full_name ASC
         LIMIT 15`,
      [teamId],
    ),
  ]);
  const bio = identity[0] ? { ...identity[0], ...extra[0], ...details[0] } : null;
  return {
    bio,
    currentStanding: currentStanding[0] ?? null,
    seasons,
    franchiseHistory,
    recentGames,
    franchiseTotals: franchiseTotals[0] ?? null,
    franchiseAlumni,
  };
}

// ---------------------------------------------------------------------------
// Current team roster
// ---------------------------------------------------------------------------

export async function getTeamRoster(teamId: number): Promise<Row[]> {
  // dim_player.is_current/is_active track the latest SCD row for a player,
  // not whether he is on a current NBA roster. bridge_player_team_season is
  // season-membership, so players who changed teams during the latest season
  // legitimately appear under multiple teams there. Use the NBA current-player
  // index as the assignment source, then only use same-team latest bridge rows
  // to supplement jersey/position fields.
  return queryObjects(
    `WITH current_assignments AS (
       SELECT person_id
       FROM stg_common_all_players
       WHERE team_id = ? AND roster_status = 1
       QUALIFY ROW_NUMBER() OVER (PARTITION BY person_id ORDER BY TRY_CAST(to_year AS INTEGER) DESC) = 1
     ),
     latest_bridge AS (
       SELECT b.player_id, b.position, b.jersey_number
       FROM bridge_player_team_season b
       WHERE b.team_id = ?
         AND b.season_year = (
           SELECT MAX(season_year)
           FROM bridge_player_team_season
           WHERE team_id = ?
         )
       QUALIFY ROW_NUMBER() OVER (
         PARTITION BY b.player_id
         ORDER BY b.position NULLS LAST, b.jersey_number NULLS LAST
       ) = 1
     )
     SELECT
       p.player_id,
       p.full_name,
       COALESCE(lb.position, p.position) AS position,
       lb.jersey_number,
       p.height,
       p.weight
     FROM current_assignments ca
     JOIN dim_player p ON p.player_id = ca.person_id AND p.is_current
     LEFT JOIN latest_bridge lb ON lb.player_id = p.player_id
     ORDER BY p.full_name`,
    [teamId, teamId, teamId],
  );
}

// ---------------------------------------------------------------------------
// Playoff series-by-series
//
// Derived entirely from fact_game (imported complete game dimension with
// scores — unlike the legacy `game` table, it has no missing playoff
// seasons; the 1994/1996/2000/2002/2006/2024/2025 runs are all present).
// fact_playoff_series is NOT used: its wins/losses/abbreviation columns are
// unreliable (each real game duplicated once per historical abbreviation era,
// counters never reset per series). A team plays each opponent at most once
// per postseason, so grouping a team's playoff games by opponent IS the
// series, and ordering series chronologically within a season reproduces the
// round order (First Round, Conf. Semis, Conf. Finals, Finals) without
// bracket reconstruction. Play-in games are excluded (fact_game classifies
// them under season_type 'Regular', game_type 'Play-in Tournament').
// ---------------------------------------------------------------------------

export async function getTeamPlayoffSeries(teamId: number): Promise<Row[]> {
  return queryObjects(
    `WITH team_games AS (
       SELECT
         g.season_year AS season_id,
         g.game_date,
         CASE WHEN g.winner_team_id = ? THEN 'W' ELSE 'L' END AS team_wl,
         CASE WHEN g.home_team_id = ? THEN g.away_team_id ELSE g.home_team_id END AS opponent_team_id
       FROM fact_game g
       WHERE g.season_type = 'Playoffs'
         AND ? IN (g.home_team_id, g.away_team_id)
         AND g.winner_team_id IS NOT NULL
     ),
     series_agg AS (
       SELECT
         season_id, opponent_team_id,
         MIN(game_date) AS series_start,
         COUNT(*) FILTER (WHERE team_wl = 'W') AS wins,
         COUNT(*) FILTER (WHERE team_wl = 'L') AS losses
       FROM team_games
       GROUP BY season_id, opponent_team_id
     )
     SELECT
       sa.season_id,
       sa.wins,
       sa.losses,
       ROW_NUMBER() OVER (PARTITION BY sa.season_id ORDER BY sa.series_start) AS round_number,
       COALESCE(th_era.abbreviation, th_current.abbreviation) AS opponent_abbreviation,
       COALESCE(th_era.nickname, th_current.nickname) AS opponent_name
     FROM series_agg sa
     LEFT JOIN dim_team_history th_era
       ON th_era.team_id = sa.opponent_team_id
       AND sa.season_id >= th_era.valid_from
       AND (th_era.valid_to IS NULL OR sa.season_id < th_era.valid_to)
     LEFT JOIN dim_team_history th_current
       ON th_current.team_id = sa.opponent_team_id AND th_current.is_current
     ORDER BY sa.season_id DESC, round_number`,
    [teamId, teamId, teamId],
  );
}

// ---------------------------------------------------------------------------
// Historical coach-by-season
// ---------------------------------------------------------------------------

export async function getTeamCoachHistory(teamId: number): Promise<Row[]> {
  if (!BBR_COACHES_AVAILABLE) return [];
  const safePath = BBR_COACHES_PATH.replaceAll("\\", "/").replaceAll("'", "''");
  return queryObjects(
    `SELECT
       season_year,
       COALESCE(first_name || ' ' || last_name, coach_label) AS coach_name,
       wins,
       losses
     FROM read_json_auto('${safePath}')
     WHERE team_id = ?
     ORDER BY season_end_year DESC, wins DESC`,
    [teamId],
  );
}

// ---------------------------------------------------------------------------
// Lineup efficiency (team-level on/off complement)
// ---------------------------------------------------------------------------

export async function getTeamLineupEfficiency(teamId: number, limit = 15): Promise<Row[]> {
  return queryObjects(
    `SELECT group_id, season_year, total_gp, total_min, pts_per48, avg_net_rating
     FROM agg_lineup_efficiency
     WHERE team_id = ?
     ORDER BY total_min DESC
     LIMIT ?`,
    [teamId, limit],
  );
}

// ---------------------------------------------------------------------------
// League ranks (offensive + defensive ordinal ranks per stat per season)
// ---------------------------------------------------------------------------

export async function getTeamRanks(teamId: number): Promise<Row[]> {
  return queryObjects(
    `SELECT season_id, season_type, pts_rank, pts_pg, reb_rank, reb_pg,
            ast_rank, ast_pg, opp_pts_rank, opp_pts_pg
     FROM fact_team_season_ranks
     WHERE team_id = ?
     ORDER BY season_id DESC, season_type`,
    [teamId],
  );
}

// ---------------------------------------------------------------------------
// Opponent four-factors
//
// agg_team_defense carries the defensive side of the four factors
// (opponent eFG%, opponent TOV%, opponent OREB%, opponent FT rate) plus DRtg
// and NetRtg; tracking-era coverage only (no pre-1996-97 rows). season_type
// can be 'Regular' / 'Playoffs' / 'Cup' so the UI can show playoff splits.
// ---------------------------------------------------------------------------

export async function getTeamOpponentStats(teamId: number): Promise<Row[]> {
  return queryObjects(
    `SELECT season_year, season_type, gp,
            avg_def_rating, avg_net_rating,
            avg_opp_efg_pct, avg_opp_tov_pct, avg_opp_oreb_pct, avg_opp_fta_rate,
            avg_contested_shots, avg_deflections, avg_loose_balls_recovered,
            avg_charges_drawn, avg_screen_assists
     FROM agg_team_defense
     WHERE team_id = ?
     ORDER BY season_year DESC, season_type`,
    [teamId],
  );
}

// ---------------------------------------------------------------------------
// Standings
// ---------------------------------------------------------------------------

export async function listStandingsSeasons(): Promise<string[]> {
  const rows = await queryObjects<{ season_year: string }>(
    `SELECT DISTINCT season_year
     FROM (
       SELECT season_year FROM fact_standings
       UNION ALL
       SELECT printf('%d-%02d', season - 1, season % 100) AS season_year
       FROM stg_bref_team_summaries
       WHERE lg = 'NBA'
         AND source_table = 'team_summaries'
         AND nba_team_id IS NOT NULL
         AND team <> 'League Average'
     )
     ORDER BY season_year DESC`,
  );
  return rows.map((r) => r.season_year);
}

export async function getStandings(season: string, seasonType: string): Promise<Row[]> {
  const seasonEndYear = Number(season.slice(0, 4)) + 1;

  // Era-matched the same way as player seasons (see getPlayerProfile), so
  // 1996-97 Seattle standings show "SuperSonics" rather than "Thunder".
  // Older regular seasons fall back to the imported BBR summaries only when
  // fact_standings has no row for the requested season/type.
  return queryObjects(
    `WITH fact_rows AS (
       SELECT
         team_id,
         conference,
         division,
         conf_rank,
         div_rank,
         wins,
         losses,
         win_pct,
         home_record,
         road_record,
         last_ten,
         current_streak,
         games_back,
         clinch_indicator,
         pts_pg,
         opp_pts_pg,
         diff_pts_pg,
         season_year,
         season_type,
         NULL::VARCHAR AS source_team_name,
         NULL::VARCHAR AS source_abbreviation
       FROM fact_standings
       WHERE season_year = ? AND season_type = ?
     ),
     bref_rows AS (
       SELECT
         TRY_CAST(nba_team_id AS BIGINT) AS team_id,
         NULL::VARCHAR AS conference,
         NULL::VARCHAR AS division,
         NULL::BIGINT AS conf_rank,
         NULL::BIGINT AS div_rank,
         TRY_CAST(w AS BIGINT) AS wins,
         TRY_CAST(l AS BIGINT) AS losses,
         CASE
           WHEN TRY_CAST(w AS DOUBLE) + TRY_CAST(l AS DOUBLE) > 0
             THEN TRY_CAST(w AS DOUBLE) / (TRY_CAST(w AS DOUBLE) + TRY_CAST(l AS DOUBLE))
           ELSE NULL
         END AS win_pct,
         NULL::VARCHAR AS home_record,
         NULL::VARCHAR AS road_record,
         NULL::VARCHAR AS last_ten,
         NULL::VARCHAR AS current_streak,
         NULL::DOUBLE AS games_back,
         NULL::VARCHAR AS clinch_indicator,
         NULL::DOUBLE AS pts_pg,
         NULL::DOUBLE AS opp_pts_pg,
         TRY_CAST(mov AS DOUBLE) AS diff_pts_pg,
         ? AS season_year,
         ? AS season_type,
         team AS source_team_name,
         abbreviation AS source_abbreviation
       FROM stg_bref_team_summaries
       WHERE ? = 'Regular'
         AND season = ?
         AND lg = 'NBA'
         AND source_table = 'team_summaries'
         AND nba_team_id IS NOT NULL
         AND team <> 'League Average'
         AND NOT EXISTS (SELECT 1 FROM fact_rows)
     ),
     standings_rows AS (
       SELECT * FROM fact_rows
       UNION ALL
       SELECT * FROM bref_rows
     ),
     latest_team_history AS (
       SELECT *
       FROM dim_team_history
       QUALIFY ROW_NUMBER() OVER (PARTITION BY team_id ORDER BY valid_from DESC) = 1
     )
     SELECT
       s.team_id,
       s.conference,
       s.division,
       s.conf_rank,
       s.div_rank,
       s.wins,
       s.losses,
       s.win_pct,
       s.home_record,
       s.road_record,
       s.last_ten,
       s.current_streak,
       s.games_back,
       s.clinch_indicator,
       s.pts_pg,
       s.opp_pts_pg,
       s.diff_pts_pg,
       s.season_year,
       s.season_type,
       COALESCE(th.nickname, lth.nickname, s.source_team_name) AS team_name,
       COALESCE(th.abbreviation, lth.abbreviation, s.source_abbreviation) AS abbreviation
     FROM standings_rows s
     LEFT JOIN dim_team_history th
       ON th.team_id = s.team_id
       AND s.season_year >= th.valid_from
       AND (th.valid_to IS NULL OR s.season_year < th.valid_to)
     LEFT JOIN latest_team_history lth ON lth.team_id = s.team_id
     ORDER BY s.conference NULLS LAST, s.conf_rank NULLS LAST, team_name`,
    [season, seasonType, season, seasonType, seasonType, seasonEndYear],
  );
}

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
// Awards
// ---------------------------------------------------------------------------

export async function listAwardSeasons(): Promise<string[]> {
  const rows = await queryObjects<{ season: string }>(
    `WITH ${PLAYER_AWARD_ROWS_CTE}
     SELECT DISTINCT season FROM award_rows ORDER BY season DESC`,
  );
  return rows.map((r) => r.season);
}

export async function listAwardTypes(): Promise<string[]> {
  const rows = await queryObjects<{ award_type: string }>(
    `WITH ${PLAYER_AWARD_ROWS_CTE}
     SELECT DISTINCT award_type
     FROM award_rows
     ORDER BY award_type`,
  );
  return rows.map((r) => r.award_type);
}

export async function getAwards(season: string, awardType: string | null): Promise<Row[]> {
  const conditions = ["a.season = ?"];
  const params: DuckDBValue[] = [season];
  if (awardType) {
    conditions.push("a.award_type = ?");
    params.push(awardType);
  }
  return queryObjects(
    `WITH ${PLAYER_AWARD_ROWS_CTE}
     SELECT a.*, COALESCE(p.full_name, a.source_player_name) AS full_name
     FROM award_rows a
     LEFT JOIN dim_player p ON p.player_id = a.player_id AND p.is_current
     WHERE ${conditions.join(" AND ")}
     ORDER BY a.award_type, full_name`,
    params,
  );
}

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

export async function getSeasonLeaders(
  season: string,
  statKey: string,
  limit = 25,
): Promise<Row[]> {
  // Whitelist stat keys — agg_league_leaders stores each as a separate
  // rank/avg column, so we can't parameterise the column name. Falling
  // back to 'pts' on unknown input keeps the endpoint resilient.
  const statColumn: Record<string, { avg: string; rank: string }> = {
    pts: { avg: "avg_pts", rank: "pts_rank" },
    reb: { avg: "avg_reb", rank: "reb_rank" },
    ast: { avg: "avg_ast", rank: "ast_rank" },
    stl: { avg: "avg_stl", rank: "stl_rank" },
    blk: { avg: "avg_blk", rank: "blk_rank" },
  };
  const cols = statColumn[statKey] ?? statColumn.pts;
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

// ---------------------------------------------------------------------------
// Franchise Leaders (per-team career leaders)
//
// fact_franchise_leaders has one row per team_id with five stat leaders
// (pts/ast/reb/blk/stl). Each leader is stored as `<stat>_person_id`
// (BIGINT, dim_player.player_id) plus a `<stat>_player` VARCHAR name
// snapshot, plus the leader's `<stat>` value. Join dim_player to
// canonicalize full_name (in case the snapshot drifted).
//
// fact_franchise_players has one row per (team_id, person_id, season_type)
// with that player's career-with-team totals (gp, pts, ast, reb, fg_pct,
// ...). Player key is `person_id` (same value-space as
// dim_player.player_id, verified by spot-check); dedupe by season_type
// since the same player typically has a Regular and a Playoffs row.
// Sortable by any of the totals; default sort is gp DESC.
// ---------------------------------------------------------------------------

export async function getFranchiseLeaders(teamId: number): Promise<Row | null> {
  const rows = await queryObjects(
    `WITH regular_players AS (
       SELECT
         fp.team_id,
         fp.person_id,
         fp.player,
         p.full_name,
         fp.pts,
         fp.ast,
         fp.reb,
         fp.blk,
         fp.stl
       FROM fact_franchise_players fp
       LEFT JOIN dim_player p ON p.player_id = fp.person_id AND p.is_current
       WHERE fp.team_id = ?
         AND fp.season_type = 'Regular'
     ),
     pts_leader AS (
       SELECT * FROM regular_players
       ORDER BY pts DESC NULLS LAST, player
       LIMIT 1
     ),
     ast_leader AS (
       SELECT * FROM regular_players
       ORDER BY ast DESC NULLS LAST, player
       LIMIT 1
     ),
     reb_leader AS (
       SELECT * FROM regular_players
       ORDER BY reb DESC NULLS LAST, player
       LIMIT 1
     ),
     blk_leader AS (
       SELECT * FROM regular_players
       ORDER BY blk DESC NULLS LAST, player
       LIMIT 1
     ),
     stl_leader AS (
       SELECT * FROM regular_players
       ORDER BY stl DESC NULLS LAST, player
       LIMIT 1
     )
     SELECT
       ? AS team_id,
       pts.pts,
       pts.person_id AS pts_person_id,
       pts.player AS pts_player,
       COALESCE(pts.full_name, pts.player) AS pts_leader_name,
       ast.ast,
       ast.person_id AS ast_person_id,
       ast.player AS ast_player,
       COALESCE(ast.full_name, ast.player) AS ast_leader_name,
       reb.reb,
       reb.person_id AS reb_person_id,
       reb.player AS reb_player,
       COALESCE(reb.full_name, reb.player) AS reb_leader_name,
       blk.blk,
       blk.person_id AS blk_person_id,
       blk.player AS blk_player,
       COALESCE(blk.full_name, blk.player) AS blk_leader_name,
       stl.stl,
       stl.person_id AS stl_person_id,
       stl.player AS stl_player,
       COALESCE(stl.full_name, stl.player) AS stl_leader_name
     FROM pts_leader pts
     CROSS JOIN ast_leader ast
     CROSS JOIN reb_leader reb
     CROSS JOIN blk_leader blk
     CROSS JOIN stl_leader stl`,
    [teamId, teamId],
  );
  return rows[0] ?? null;
}

// Whitelist of stat keys the client can sort by. Maps onto the numeric
// columns of fact_franchise_players (all DOUBLE in the schema).
const FRANCHISE_PLAYER_SORT_COLUMNS: ReadonlySet<string> = new Set([
  "gp",
  "pts",
  "ast",
  "reb",
  "stl",
  "blk",
  "tov",
  "fg_pct",
  "fg3_pct",
  "ft_pct",
  "oreb",
  "dreb",
]);

export async function getFranchiseTopPlayers(
  teamId: number,
  statKey = "gp",
  limit = 25,
): Promise<Row[]> {
  const sortKey = FRANCHISE_PLAYER_SORT_COLUMNS.has(statKey) ? statKey : "gp";
  return queryObjects(
    `SELECT
       fp.person_id AS player_id,
       fp.player AS source_player_name,
       p.full_name,
       fp.gp,
       fp.pts,
       fp.ast,
       fp.reb,
       fp.stl,
       fp.blk,
       fp.fg_pct,
       fp.fg3_pct,
       fp.ft_pct
     FROM fact_franchise_players fp
     JOIN dim_player p ON p.player_id = fp.person_id AND p.is_current
     WHERE fp.team_id = ?
       AND fp.season_type = 'Regular'
     ORDER BY fp.${sortKey} DESC NULLS LAST, p.full_name ASC
     LIMIT ?`,
    [teamId, limit],
  );
}

// ---------------------------------------------------------------------------
// Player Season Ranks
//
// fact_player_season_ranks has rank_* columns per (player_id, season_id,
// league_id, rank_type). league_id is the literal '00' (NBA) across all
// rows; rank_type is one of Regular/Playoffs/Cup. The schema includes
// team_abbreviation inline (no join required), but we re-derive the
// current abbreviation from dim_team_history so the era-correct
// abbreviation is returned (matches the convention used elsewhere on
// the player profile).
// ---------------------------------------------------------------------------

export async function getPlayerSeasonRanks(playerId: number, limit = 50): Promise<Row[]> {
  return queryObjects(
    `SELECT
       r.player_id,
       r.season_id,
       r.rank_type,
       r.team_id,
       COALESCE(th.abbreviation, r.team_abbreviation) AS team_abbreviation,
       r.gp,
       r.player_age,
       r.rank_pts,
       r.rank_reb,
       r.rank_ast,
       r.rank_stl,
       r.rank_blk,
       r.rank_fgm,
       r.rank_fga,
       r.rank_fg_pct,
       r.rank_fg3m,
       r.rank_fg3a,
       r.rank_fg3_pct,
       r.rank_ftm,
       r.rank_fta,
       r.rank_ft_pct,
       r.rank_oreb,
       r.rank_dreb,
       r.rank_tov,
       r.rank_eff,
       r.rank_min
     FROM fact_player_season_ranks r
     LEFT JOIN dim_team_history th
       ON th.team_id = r.team_id
       AND r.season_id >= th.valid_from
       AND (th.valid_to IS NULL OR r.season_id < th.valid_to)
     WHERE r.player_id = ?
       AND r.league_id = '00'
     ORDER BY r.season_id DESC, CASE r.rank_type WHEN 'Regular' THEN 1 WHEN 'Playoffs' THEN 2 WHEN 'Cup' THEN 3 ELSE 4 END
     LIMIT ?`,
    [playerId, limit],
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

// ---------------------------------------------------------------------------
// Player splits / estimated metrics / shot chart
//
// analytics_player_general_splits only carries the Location split type
// (Home/Away) — W/L / month / pre-post-ASG splits were never built upstream.
// The shot chart bins analytics_shooting_efficiency's raw loc_x/loc_y
// (tenths of feet, x -250..250 from the hoop centerline, y -52..418 toward
// halfcourt) into 25-unit (2.5 ft) cells server-side so the client renders a
// small fixed grid instead of pulling 6.5M rows.
// ---------------------------------------------------------------------------

export async function getPlayerLocationSplits(playerId: number): Promise<Row[]> {
  return queryObjects(
    `SELECT season_year, group_value, gp, w, l, w_pct, min, pts, reb, ast,
            fg_pct, fg3_pct, ft_pct, plus_minus
     FROM analytics_player_general_splits
     WHERE player_id = ? AND split_type = 'Location' AND season_type = 'Regular'
     ORDER BY season_year, group_value`,
    [playerId],
  );
}

export async function getPlayerEstimatedMetrics(playerId: number): Promise<Row[]> {
  return queryObjects(
    `SELECT season_year, gp, w, l,
            e_off_rating, e_def_rating, e_net_rating, e_pace,
            e_usg_pct, e_reb_pct, e_tov_pct
     FROM fact_player_estimated_metrics
     WHERE player_id = ?
     ORDER BY season_year`,
    [playerId],
  );
}

export async function listPlayerShotSeasons(playerId: number): Promise<string[]> {
  const rows = await queryObjects<{ season_year: string }>(
    `SELECT DISTINCT season_year FROM analytics_shooting_efficiency
     WHERE player_id = ? ORDER BY season_year DESC`,
    [playerId],
  );
  return rows.map((r) => r.season_year);
}

export async function getPlayerShotChart(playerId: number, season: string | null): Promise<Row[]> {
  const conditions = ["player_id = ?", "loc_y BETWEEN -52 AND 418", "loc_x BETWEEN -250 AND 250"];
  const params: DuckDBValue[] = [playerId];
  if (season) {
    conditions.push("season_year = ?");
    params.push(season);
  }
  return queryObjects(
    `SELECT CAST(floor(loc_x / 25) AS INTEGER) AS bin_x,
            CAST(floor(loc_y / 25) AS INTEGER) AS bin_y,
            count(*) AS attempts,
            CAST(sum(shot_made_flag) AS BIGINT) AS makes,
            round(avg(league_avg_fg_pct), 3) AS league_fg_pct
     FROM analytics_shooting_efficiency
     WHERE ${conditions.join(" AND ")}
     GROUP BY 1, 2`,
    params,
  );
}

// ---------------------------------------------------------------------------
// Team head-to-head + season context
// ---------------------------------------------------------------------------

export async function getTeamHeadToHead(teamId: number): Promise<Row[]> {
  return queryObjects(
    `SELECT h.opponent_team_id,
            coalesce(th.abbreviation, max(h.opponent_abbr)) AS opponent_abbreviation,
            coalesce(th.nickname, max(h.opponent_abbr)) AS opponent_name,
            CAST(sum(h.games_played) AS BIGINT) AS gp,
            CAST(sum(h.wins) AS BIGINT) AS wins,
            CAST(sum(h.losses) AS BIGINT) AS losses,
            round(sum(h.avg_pts_scored * h.games_played) / nullif(sum(h.games_played), 0), 1) AS avg_pts_scored,
            round(sum(h.avg_pts_allowed * h.games_played) / nullif(sum(h.games_played), 0), 1) AS avg_pts_allowed,
            round(sum(h.avg_margin * h.games_played) / nullif(sum(h.games_played), 0), 1) AS avg_margin
     FROM analytics_head_to_head h
     LEFT JOIN dim_team_history th ON th.team_id = h.opponent_team_id AND th.is_current
     WHERE h.team_id = ?
     GROUP BY h.opponent_team_id, th.abbreviation, th.nickname
     ORDER BY gp DESC, wins DESC`,
    [teamId],
  );
}

// BBR team-season context (SRS, pace, ratings, four factors both ways).
// stg_bref_team_summaries is keyed by BBR abbreviation+season and carries the
// crosswalk-resolved nba_team_id added at import time.
export async function getTeamSeasonContext(teamId: number): Promise<Row[]> {
  return queryObjects(
    `SELECT season, w, l, pw, pl, srs, sos, pace, o_rtg, d_rtg, n_rtg,
            e_fg_percent, tov_percent, orb_percent, ft_fga,
            opp_e_fg_percent, opp_tov_percent, drb_percent, opp_ft_fga,
            attend_g
     FROM stg_bref_team_summaries
     WHERE nba_team_id = ? AND NOT playoffs
     ORDER BY season DESC`,
    [teamId],
  );
}

// ---------------------------------------------------------------------------
// Game detail
//
// All game-keyed tables share the same 10-char zero-padded game_id
// (verified: fact_game, line_score, officials, fact_game_leaders,
// fact_starting_lineup_player, fact_pbp_events). The PBP tail returns the
// final scoring plays in reverse chronological order (client re-reverses).
// ---------------------------------------------------------------------------

export interface GameDetail {
  header: Row | null;
  metadata: Row | null;
  lineScore: Row | null;
  periodScores: Row[];
  teamBoxes: Row[];
  playerBoxes: Row[];
  leaders: Row[];
  officials: Row[];
  starters: Row[];
  lastPlays: Row[];
  context: Row[];
  coverage: Row;
}

function lineScoreMatchesHeader(lineScore: Row, header: Row): boolean {
  return (
    String(lineScore.team_id_home) === String(header.home_team_id) &&
    String(lineScore.team_id_away) === String(header.away_team_id)
  );
}

function normalizeLineScore(lineScore: Row, header: Row): Row {
  if (lineScoreMatchesHeader(lineScore, header)) return lineScore;
  const isReversed =
    String(lineScore.team_id_home) === String(header.away_team_id) &&
    String(lineScore.team_id_away) === String(header.home_team_id);
  if (!isReversed) return lineScore;
  const normalized: Row = { ...lineScore };
  for (const key of Object.keys(lineScore)) {
    if (key.endsWith("_home")) {
      normalized[key] = lineScore[`${key.slice(0, -5)}_away`];
    } else if (key.endsWith("_away")) {
      normalized[key] = lineScore[`${key.slice(0, -5)}_home`];
    }
  }
  return normalized;
}

type TeamSide = "home" | "away";

const PERIOD_KEYS = [
  { key: "pts_qtr1", period: 1, label: "Q1" },
  { key: "pts_qtr2", period: 2, label: "Q2" },
  { key: "pts_qtr3", period: 3, label: "Q3" },
  { key: "pts_qtr4", period: 4, label: "Q4" },
  ...Array.from({ length: 10 }, (_, idx) => ({
    key: `pts_ot${idx + 1}`,
    period: idx + 5,
    label: `OT${idx + 1}`,
  })),
] as const;

function teamSide(teamId: unknown, header: Row): TeamSide | null {
  if (String(teamId) === String(header.home_team_id)) return "home";
  if (String(teamId) === String(header.away_team_id)) return "away";
  return null;
}

function teamNameForSide(side: TeamSide, header: Row): unknown {
  return side === "home" ? header.home_name : header.away_name;
}

function teamAbbreviationForSide(side: TeamSide, header: Row): unknown {
  return side === "home" ? header.home_abbreviation : header.away_abbreviation;
}

function scoreForSide(side: TeamSide, header: Row): unknown {
  return side === "home" ? header.home_score : header.away_score;
}

function baseLineScore(header: Row, source: string): Row {
  return {
    line_score_source: source,
    team_id_home: header.home_team_id,
    team_id_away: header.away_team_id,
    team_abbreviation_home: header.home_abbreviation,
    team_abbreviation_away: header.away_abbreviation,
    team_city_name_home: header.home_name,
    team_nickname_home: "",
    team_city_name_away: header.away_name,
    team_nickname_away: "",
    pts_home: header.home_score,
    pts_away: header.away_score,
  };
}

function lineScoreFromQuarterScores(rows: Row[], header: Row): Row | null {
  if (rows.length === 0) return null;
  const lineScore = baseLineScore(header, "fact_game_quarter_scores");
  let found = false;
  for (const row of rows) {
    const side = teamSide(row.team_id, header);
    const period = Number(row.period);
    if (!side || !Number.isInteger(period) || period < 1) continue;
    const key = period <= 4 ? `pts_qtr${period}` : `pts_ot${period - 4}`;
    lineScore[`${key}_${side}`] = row.pts;
    found = true;
  }
  return found ? lineScore : null;
}

function lineScoreFromWideRows(rows: Row[], header: Row, source: string): Row | null {
  if (rows.length === 0) return null;
  const lineScore = baseLineScore(header, source);
  let foundPeriod = false;
  for (const row of rows) {
    const side = teamSide(row.team_id, header);
    if (!side) continue;
    lineScore[`team_id_${side}`] = row.team_id;
    lineScore[`team_abbreviation_${side}`] =
      row.team_abbreviation ?? teamAbbreviationForSide(side, header);
    lineScore[`team_city_name_${side}`] = row.team_city_name ?? teamNameForSide(side, header);
    lineScore[`team_nickname_${side}`] = row.team_nickname ?? "";
    for (const period of PERIOD_KEYS) {
      if (row[period.key] == null) continue;
      lineScore[`${period.key}_${side}`] = row[period.key];
      foundPeriod = true;
    }
    lineScore[`pts_${side}`] = row.pts ?? scoreForSide(side, header);
  }
  return foundPeriod ? lineScore : null;
}

function lineScoreFromLegacyRow(row: Row | undefined, header: Row): Row | null {
  if (!row) return null;
  const normalized = normalizeLineScore(row, header);
  return { ...normalized, line_score_source: normalized.line_score_source ?? "line_score" };
}

function labelFromLineScore(lineScore: Row, side: TeamSide): string {
  return [lineScore[`team_city_name_${side}`], lineScore[`team_nickname_${side}`]]
    .filter(Boolean)
    .map(String)
    .join(" ");
}

function headerWithLineScoreLabels(header: Row, lineScore: Row): Row {
  return {
    ...header,
    home_abbreviation: lineScore.team_abbreviation_home ?? header.home_abbreviation,
    away_abbreviation: lineScore.team_abbreviation_away ?? header.away_abbreviation,
    home_name: labelFromLineScore(lineScore, "home") || header.home_name,
    away_name: labelFromLineScore(lineScore, "away") || header.away_name,
  };
}

function periodScoresFromLineScore(lineScore: Row, header: Row): Row[] {
  const rows: Row[] = [];
  const source = lineScore.line_score_source ?? "line_score";
  for (const side of ["away", "home"] as const) {
    const teamId = lineScore[`team_id_${side}`];
    const teamName = [lineScore[`team_city_name_${side}`], lineScore[`team_nickname_${side}`]]
      .filter(Boolean)
      .map(String)
      .join(" ");
    for (const period of PERIOD_KEYS) {
      const pts = lineScore[`${period.key}_${side}`];
      if (pts == null) continue;
      rows.push({
        line_score_source: source,
        team_id: teamId,
        team_side: side,
        team_name: teamName || teamNameForSide(side, header),
        period: period.period,
        period_label: period.label,
        pts,
        is_final_only: false,
      });
    }
  }
  if (rows.length > 0) return rows;
  return (["away", "home"] as const).map((side) => ({
    line_score_source: source,
    team_id: lineScore[`team_id_${side}`],
    team_side: side,
    team_name: [lineScore[`team_city_name_${side}`], lineScore[`team_nickname_${side}`]]
      .filter(Boolean)
      .map(String)
      .join(" "),
    period: null,
    period_label: "Final",
    pts: lineScore[`pts_${side}`],
    is_final_only: true,
  }));
}

function buildMetadata(header: Row | null): Row | null {
  if (!header) return null;
  return {
    game_id: header.game_id,
    game_date: header.game_date,
    game_datetime_est: header.game_datetime_est,
    season_year: header.season_year,
    season_type: header.season_type,
    game_type: header.game_type,
    game_subtype: header.game_subtype,
    game_label: header.game_label,
    game_sub_label: header.game_sub_label,
    series_game_number: header.series_game_number,
    game_status: header.game_status,
    game_status_text: header.game_status_text,
    game_clock: header.game_clock,
    game_time_utc: header.game_time_utc,
    game_et: header.game_et,
    game_duration: header.game_duration,
    arena_id: header.arena_id,
    arena_name: header.arena_name,
    arena_city: header.arena_city,
    arena_state: header.arena_state,
    attendance: header.attendance,
    sellout: header.sellout,
    is_overtime: header.is_overtime,
    odds_home: header.odds_home,
    odds_away: header.odds_away,
  };
}

function buildCoverage(
  header: Row | null,
  lineScore: Row | null,
  periodScores: Row[],
  teamBoxes: Row[],
  playerBoxes: Row[],
  officials: Row[],
  starters: Row[],
  lastPlays: Row[],
  context: Row[],
): Row {
  const seasonYear = header?.season_year;
  const seasonStart =
    typeof seasonYear === "string" || typeof seasonYear === "number"
      ? Number(String(seasonYear).slice(0, 4))
      : NaN;
  const hasPeriodScores = periodScores.some((row) => row.is_final_only !== true);
  const hasModernPlayerBox = playerBoxes.some((row) => row.coverage_level === "modern");
  const isHistoricalPartial =
    Number.isFinite(seasonStart) && seasonStart < 1996 && (!hasPeriodScores || !hasModernPlayerBox);
  const hasFullModernBox = hasPeriodScores && teamBoxes.length >= 2 && hasModernPlayerBox;
  return {
    coverage_label: hasFullModernBox
      ? "Full modern box score"
      : isHistoricalPartial
        ? "Partial historical box score"
        : "Partial box score",
    is_historical_partial: isHistoricalPartial,
    has_period_scores: hasPeriodScores,
    line_score_source: lineScore?.line_score_source ?? null,
    has_team_box: teamBoxes.length >= 2,
    team_box_source: teamBoxes[0]?.box_score_source ?? null,
    has_player_box: playerBoxes.length > 0,
    player_box_source: playerBoxes[0]?.box_score_source ?? null,
    has_modern_player_box: hasModernPlayerBox,
    has_advanced_player_box: playerBoxes.some(
      (row) => row.off_rating != null || row.ts_pct != null,
    ),
    has_starters: starters.length > 0,
    has_officials: officials.length > 0,
    has_pbp: lastPlays.length > 0,
    has_context: context.length > 0,
    has_attendance: header?.attendance != null,
    has_arena: header?.arena_name != null,
  };
}

export async function getGameDetail(gameId: string): Promise<GameDetail> {
  const [
    header,
    quarterScores,
    scoreboardLineScore,
    v3LineScore,
    legacyLineScore,
    factTeamBoxes,
    extendedTeamBoxes,
    factPlayerBoxes,
    extendedPlayerBoxes,
    leaders,
    officials,
    starters,
    lastPlays,
    extendedContext,
    factContext,
  ] = await Promise.all([
    queryObjects(
      `WITH game_row AS (
         SELECT g.*, TRY_CAST(substr(CAST(g.season_year AS VARCHAR), 1, 4) AS INTEGER) AS season_start
         FROM fact_game g
         WHERE g.game_id = ?
       ),
       home_history AS (
         SELECT *
         FROM (
           SELECT th.*,
                  ROW_NUMBER() OVER (
                    ORDER BY
                      CASE
                        WHEN g.season_start >= TRY_CAST(substr(th.valid_from, 1, 4) AS INTEGER)
                         AND (th.valid_to IS NULL OR g.season_start <= TRY_CAST(substr(th.valid_to, 1, 4) AS INTEGER))
                        THEN 0
                        WHEN th.is_current THEN 1
                        ELSE 2
                      END,
                      TRY_CAST(substr(th.valid_from, 1, 4) AS INTEGER) DESC NULLS LAST
                  ) AS rn
           FROM game_row g
           JOIN dim_team_history th ON th.team_id = g.home_team_id
         )
         WHERE rn = 1
       ),
       away_history AS (
         SELECT *
         FROM (
           SELECT th.*,
                  ROW_NUMBER() OVER (
                    ORDER BY
                      CASE
                        WHEN g.season_start >= TRY_CAST(substr(th.valid_from, 1, 4) AS INTEGER)
                         AND (th.valid_to IS NULL OR g.season_start <= TRY_CAST(substr(th.valid_to, 1, 4) AS INTEGER))
                        THEN 0
                        WHEN th.is_current THEN 1
                        ELSE 2
                      END,
                      TRY_CAST(substr(th.valid_from, 1, 4) AS INTEGER) DESC NULLS LAST
                  ) AS rn
           FROM game_row g
           JOIN dim_team_history th ON th.team_id = g.away_team_id
         )
         WHERE rn = 1
       ),
       context_header AS (
         SELECT game_id,
                max(attendance) AS attendance,
                max(NULLIF(game_time, '')) AS game_time,
                max(NULLIF(game_status_text, '')) AS game_status_text
         FROM fact_game_context
         WHERE game_id = ?
         GROUP BY game_id
       )
       SELECT g.game_id, g.game_date, g.game_datetime_est, g.season_year, g.season_type,
              g.game_type, g.game_subtype, g.game_label, g.game_sub_label, g.series_game_number,
              g.home_team_id, g.away_team_id, g.home_score, g.away_score,
              g.winner_team_id, g.arena_id,
              COALESCE(g.arena_name, a.arena_name) AS arena_name,
              COALESCE(g.arena_city, a.city) AS arena_city,
              COALESCE(g.arena_state, a.state) AS arena_state,
              COALESCE(g.attendance, gi.attendance, gs.attendance, ch.attendance) AS attendance,
              g.is_overtime, g.odds_home, g.odds_away,
              th_home.abbreviation AS home_abbreviation, th_home.city AS home_city,
              th_home.nickname AS home_name,
              th_away.abbreviation AS away_abbreviation, th_away.city AS away_city,
              th_away.nickname AS away_name,
              gs.game_status, COALESCE(gs.game_status_text, ch.game_status_text) AS game_status_text,
              gs.game_clock, gs.game_time_utc, gs.game_et,
              COALESCE(gs.duration, gi.game_duration, ch.game_time) AS game_duration,
              gs.sellout
       FROM game_row g
       LEFT JOIN home_history th_home ON true
       LEFT JOIN away_history th_away ON true
       LEFT JOIN dim_arena a ON a.arena_id = g.arena_id
       LEFT JOIN fact_box_score_summary_v3_game_info gi ON gi.game_id = g.game_id
       LEFT JOIN fact_box_score_summary_v3_game_summary gs ON gs.game_id = g.game_id
       LEFT JOIN context_header ch ON ch.game_id = g.game_id
       LIMIT 1`,
      [gameId, gameId],
    ),
    queryObjects(
      `SELECT game_id, team_id, period, pts
       FROM fact_game_quarter_scores
       WHERE game_id = ?
       ORDER BY period, team_id`,
      [gameId],
    ),
    queryObjects(
      `SELECT game_id, team_id, team_abbreviation, team_city_name, team_name AS team_nickname,
              pts_qtr1, pts_qtr2, pts_qtr3, pts_qtr4,
              pts_ot1, pts_ot2, pts_ot3, pts_ot4, pts_ot5,
              pts_ot6, pts_ot7, pts_ot8, pts_ot9, pts_ot10,
              pts
       FROM fact_scoreboard_line_score
       WHERE game_id = ?`,
      [gameId],
    ),
    queryObjects(
      `SELECT game_id, team_id, team_tricode AS team_abbreviation,
              team_city AS team_city_name, team_name AS team_nickname,
              period1_score AS pts_qtr1, period2_score AS pts_qtr2,
              period3_score AS pts_qtr3, period4_score AS pts_qtr4,
              score AS pts
       FROM fact_box_score_summary_v3_line_score
       WHERE game_id = ?`,
      [gameId],
    ),
    queryObjects(`SELECT * FROM line_score WHERE game_id = ? LIMIT 1`, [gameId]),
    queryObjects(
      `SELECT 'fact_box_score_team' AS box_score_source,
              t.game_id, t.team_id,
              CASE WHEN t.team_id = g.home_team_id THEN 'home' ELSE 'away' END AS team_side,
              t.team_name, t.team_abbreviation, t.team_city,
              t.min, t.fgm, t.fga, t.fg_pct, t.fg3m, t.fg3a, t.fg3_pct,
              t.ftm, t.fta, t.ft_pct, t.oreb, t.dreb, t.reb,
              t.ast, t.stl, t.blk, t.tov, t.pf, t.pts, t.plus_minus
       FROM fact_box_score_team t
       JOIN fact_game g ON g.game_id = t.game_id
       WHERE t.game_id = ?
       ORDER BY CASE WHEN t.team_id = g.away_team_id THEN 0 ELSE 1 END`,
      [gameId],
    ),
    queryObjects(
      `SELECT 'teamstatisticsextended' AS box_score_source,
              lpad(gameId, 10, '0') AS game_id,
              TRY_CAST(teamId AS BIGINT) AS team_id,
              CASE WHEN home = '1' THEN 'home' ELSE 'away' END AS team_side,
              trim(teamCity || ' ' || teamName) AS team_name,
              NULL AS team_abbreviation,
              teamCity AS team_city,
              TRY_CAST(numMinutes AS DOUBLE) AS min,
              TRY_CAST(fieldGoalsMade AS DOUBLE) AS fgm,
              TRY_CAST(fieldGoalsAttempted AS DOUBLE) AS fga,
              TRY_CAST(fieldGoalsPercentage AS DOUBLE) AS fg_pct,
              TRY_CAST(threePointersMade AS DOUBLE) AS fg3m,
              TRY_CAST(threePointersAttempted AS DOUBLE) AS fg3a,
              TRY_CAST(threePointersPercentage AS DOUBLE) AS fg3_pct,
              TRY_CAST(freeThrowsMade AS DOUBLE) AS ftm,
              TRY_CAST(freeThrowsAttempted AS DOUBLE) AS fta,
              TRY_CAST(freeThrowsPercentage AS DOUBLE) AS ft_pct,
              TRY_CAST(reboundsOffensive AS DOUBLE) AS oreb,
              TRY_CAST(reboundsDefensive AS DOUBLE) AS dreb,
              TRY_CAST(reboundsTotal AS DOUBLE) AS reb,
              TRY_CAST(assists AS DOUBLE) AS ast,
              TRY_CAST(steals AS DOUBLE) AS stl,
              TRY_CAST(blocks AS DOUBLE) AS blk,
              TRY_CAST(turnovers AS DOUBLE) AS tov,
              TRY_CAST(foulsPersonal AS DOUBLE) AS pf,
              TRY_CAST(teamScore AS DOUBLE) AS pts,
              TRY_CAST(plusMinusPoints AS DOUBLE) AS plus_minus
       FROM teamstatisticsextended
       WHERE lpad(gameId, 10, '0') = ?
       ORDER BY CASE WHEN home = '0' THEN 0 ELSE 1 END`,
      [gameId],
    ),
    queryObjects(
      `SELECT 'fact_player_game_boxscore' AS box_score_source,
              b.game_id, b.player_id, b.team_id, b.opponent_team_id,
              CASE WHEN b.team_id = g.home_team_id THEN 'home' ELSE 'away' END AS team_side,
              CASE WHEN b.team_id = g.home_team_id THEN th_home.abbreviation ELSE th_away.abbreviation END AS team_abbreviation,
              CASE WHEN b.team_id = g.home_team_id THEN th_home.nickname ELSE th_away.nickname END AS team_name,
              p.full_name, b.is_home, b.is_win, NULLIF(b.starting_position, '') AS starting_position,
              NULLIF(b.comment, '') AS comment, b.min, b.points, b.assists, b.blocks, b.steals,
              b.turnovers, b.fga, b.fgm, b.fg_pct, b.fg3a, b.fg3m, b.fg3_pct,
              b.fta, b.ftm, b.ft_pct, b.oreb, b.dreb, b.reb, b.fouls_personal,
              b.plus_minus, b.off_rating, b.def_rating, b.net_rating, b.ast_pct,
              b.ast_to_turnover_ratio, b.ast_ratio, b.oreb_pct, b.dreb_pct, b.reb_pct,
              b.tov_pct, b.efg_pct, b.ts_pct, b.usg_pct, b.pace, b.pie,
              CASE
                WHEN TRY_CAST(substr(CAST(g.season_year AS VARCHAR), 1, 4) AS INTEGER) < 1996
                THEN 'scoring_only'
                ELSE 'modern'
              END AS coverage_level
       FROM fact_player_game_boxscore b
       JOIN fact_game g ON g.game_id = b.game_id
       LEFT JOIN dim_player p ON p.player_id = b.player_id AND p.is_current
       LEFT JOIN dim_team_history th_home ON th_home.team_id = g.home_team_id AND th_home.is_current
       LEFT JOIN dim_team_history th_away ON th_away.team_id = g.away_team_id AND th_away.is_current
       WHERE b.game_id = ?
       ORDER BY CASE WHEN b.team_id = g.away_team_id THEN 0 ELSE 1 END,
                CASE WHEN NULLIF(b.starting_position, '') IS NULL THEN 1 ELSE 0 END,
                b.min DESC NULLS LAST, b.points DESC NULLS LAST`,
      [gameId],
    ),
    queryObjects(
      `SELECT 'playerstatisticsextended' AS box_score_source,
              lpad(gameId, 10, '0') AS game_id,
              TRY_CAST(personId AS BIGINT) AS player_id,
              TRY_CAST(playerteamId AS BIGINT) AS team_id,
              TRY_CAST(opponentteamId AS BIGINT) AS opponent_team_id,
              CASE WHEN home = '1' THEN 'home' ELSE 'away' END AS team_side,
              NULL AS team_abbreviation,
              trim(playerteamCity || ' ' || playerteamName) AS team_name,
              trim(firstName || ' ' || lastName) AS full_name,
              home = '1' AS is_home,
              win = '1' AS is_win,
              NULLIF(startingPosition, '') AS starting_position,
              NULLIF(comment, '') AS comment,
              TRY_CAST(numMinutes AS DOUBLE) AS min,
              TRY_CAST(points AS INTEGER) AS points,
              TRY_CAST(assists AS INTEGER) AS assists,
              TRY_CAST(blocks AS INTEGER) AS blocks,
              TRY_CAST(steals AS INTEGER) AS steals,
              TRY_CAST(turnovers AS INTEGER) AS turnovers,
              TRY_CAST(fieldGoalsAttempted AS INTEGER) AS fga,
              TRY_CAST(fieldGoalsMade AS INTEGER) AS fgm,
              TRY_CAST(fieldGoalsPercentage AS DOUBLE) AS fg_pct,
              TRY_CAST(threePointersAttempted AS INTEGER) AS fg3a,
              TRY_CAST(threePointersMade AS INTEGER) AS fg3m,
              TRY_CAST(threePointersPercentage AS DOUBLE) AS fg3_pct,
              TRY_CAST(freeThrowsAttempted AS INTEGER) AS fta,
              TRY_CAST(freeThrowsMade AS INTEGER) AS ftm,
              TRY_CAST(freeThrowsPercentage AS DOUBLE) AS ft_pct,
              TRY_CAST(reboundsOffensive AS INTEGER) AS oreb,
              TRY_CAST(reboundsDefensive AS INTEGER) AS dreb,
              TRY_CAST(reboundsTotal AS INTEGER) AS reb,
              TRY_CAST(foulsPersonal AS INTEGER) AS fouls_personal,
              TRY_CAST(plusMinusPoints AS INTEGER) AS plus_minus,
              TRY_CAST(offensiveRating AS DOUBLE) AS off_rating,
              TRY_CAST(defensiveRating AS DOUBLE) AS def_rating,
              TRY_CAST(netRating AS DOUBLE) AS net_rating,
              TRY_CAST(assistPercentage AS DOUBLE) AS ast_pct,
              TRY_CAST(assistToTurnoverRatio AS DOUBLE) AS ast_to_turnover_ratio,
              TRY_CAST(assistRatio AS DOUBLE) AS ast_ratio,
              TRY_CAST(offensiveReboundPercentage AS DOUBLE) AS oreb_pct,
              TRY_CAST(defensiveReboundPercentage AS DOUBLE) AS dreb_pct,
              TRY_CAST(reboundPercentage AS DOUBLE) AS reb_pct,
              TRY_CAST(teamTurnoverPercentage AS DOUBLE) AS tov_pct,
              TRY_CAST(effectiveFieldGoalPercentage AS DOUBLE) AS efg_pct,
              TRY_CAST(trueShootingPercentage AS DOUBLE) AS ts_pct,
              TRY_CAST(usagePercentage AS DOUBLE) AS usg_pct,
              TRY_CAST(pace AS DOUBLE) AS pace,
              TRY_CAST(playerImpactEstimate AS DOUBLE) AS pie,
              'modern' AS coverage_level
       FROM playerstatisticsextended
       WHERE lpad(gameId, 10, '0') = ?
       ORDER BY CASE WHEN home = '0' THEN 0 ELSE 1 END,
                CASE WHEN NULLIF(startingPosition, '') IS NULL THEN 1 ELSE 0 END,
                TRY_CAST(numMinutes AS DOUBLE) DESC NULLS LAST,
                TRY_CAST(points AS INTEGER) DESC NULLS LAST`,
      [gameId],
    ),
    queryObjects(
      `SELECT l.leader_type, l.person_id, l.name, l.team_tricode,
              l.points, l.rebounds, l.assists
       FROM fact_game_leaders l
       WHERE l.game_id = ?
       ORDER BY l.leader_type, l.points DESC`,
      [gameId],
    ),
    queryObjects(
      `WITH combined AS (
         SELECT 1 AS source_rank, 'fact_game_official' AS official_source,
                CAST(o.official_id AS VARCHAR) AS official_id,
                o.official_name AS name, d.first_name, d.last_name, d.jersey_num
         FROM fact_game_official o
         LEFT JOIN dim_official d ON d.official_id = o.official_id
         WHERE o.game_id = ?
         UNION ALL
         SELECT 2 AS source_rank, 'officials' AS official_source,
                official_id, trim(first_name || ' ' || last_name) AS name,
                first_name, last_name, jersey_num
         FROM officials
         WHERE game_id = ?
         UNION ALL
         SELECT 3 AS source_rank, 'fact_box_score_summary_v3_officials' AS official_source,
                CAST(person_id AS VARCHAR) AS official_id, name,
                first_name, family_name AS last_name, jersey_num
         FROM fact_box_score_summary_v3_officials
         WHERE game_id = ?
       ),
       ranked AS (
         SELECT *,
                ROW_NUMBER() OVER (
                  PARTITION BY COALESCE(NULLIF(official_id, ''), lower(name))
                  ORDER BY source_rank
                ) AS rn
         FROM combined
       )
       SELECT official_id, name, first_name, last_name, jersey_num, official_source
       FROM ranked
       WHERE rn = 1
       ORDER BY last_name, name`,
      [gameId, gameId, gameId],
    ),
    queryObjects(
      `SELECT s.team_id, s.person_id, s.starting_position,
              p.full_name,
              CASE WHEN s.team_id = g.home_team_id THEN th_home.abbreviation ELSE th_away.abbreviation END AS team_abbreviation
       FROM fact_starting_lineup_player s
       JOIN fact_game g ON g.game_id = s.game_id
       LEFT JOIN dim_player p ON p.player_id = s.person_id AND p.is_current
       LEFT JOIN dim_team_history th_home ON th_home.team_id = g.home_team_id AND th_home.is_current
       LEFT JOIN dim_team_history th_away ON th_away.team_id = g.away_team_id AND th_away.is_current
       WHERE s.game_id = ?
       ORDER BY CASE WHEN s.team_id = g.away_team_id THEN 0 ELSE 1 END, s.starting_position`,
      [gameId],
    ),
    queryObjects(
      `SELECT period, clock, description, score_home, score_away, points_total
       FROM fact_pbp_events
       WHERE game_id = ? AND score_home IS NOT NULL
         AND points_total IS NOT NULL AND points_total > 0
       ORDER BY seconds_elapsed DESC
       LIMIT 12`,
      [gameId],
    ),
    queryObjects(
      `SELECT 'teamstatisticsextended' AS context_source,
              TRY_CAST(teamId AS BIGINT) AS team_id,
              CASE WHEN home = '1' THEN 'home' ELSE 'away' END AS team_side,
              trim(teamCity || ' ' || teamName) AS team_name,
              TRY_CAST(benchPoints AS BIGINT) AS bench_points,
              TRY_CAST(biggestLead AS BIGINT) AS largest_lead,
              TRY_CAST(biggestScoringRun AS BIGINT) AS biggest_scoring_run,
              TRY_CAST(leadChanges AS BIGINT) AS lead_changes,
              TRY_CAST(pointsFastBreak AS BIGINT) AS pts_fb,
              TRY_CAST(pointsFromTurnovers AS BIGINT) AS pts_off_to,
              TRY_CAST(pointsInThePaint AS BIGINT) AS pts_paint,
              TRY_CAST(pointsSecondChance AS BIGINT) AS pts_2nd_chance,
              TRY_CAST(timesTied AS BIGINT) AS times_tied
       FROM teamstatisticsextended
       WHERE lpad(gameId, 10, '0') = ?
       ORDER BY CASE WHEN home = '0' THEN 0 ELSE 1 END`,
      [gameId],
    ),
    queryObjects(
      `SELECT 'fact_game_context' AS context_source,
              team_id,
              NULL AS team_side,
              trim(team_city || ' ' || team_name) AS team_name,
              NULL AS bench_points,
              largest_lead,
              NULL AS biggest_scoring_run,
              lead_changes,
              pts_fb,
              pts_off_to,
              pts_paint,
              pts_2nd_chance,
              times_tied
       FROM fact_game_context
       WHERE game_id = ? AND team_id IS NOT NULL
       ORDER BY team_id`,
      [gameId],
    ),
  ]);
  const headerRow = header[0] ?? null;
  const lineScoreRow = headerRow
    ? (lineScoreFromQuarterScores(quarterScores, headerRow) ??
      lineScoreFromWideRows(scoreboardLineScore, headerRow, "fact_scoreboard_line_score") ??
      lineScoreFromWideRows(v3LineScore, headerRow, "fact_box_score_summary_v3_line_score") ??
      lineScoreFromLegacyRow(legacyLineScore[0], headerRow) ??
      baseLineScore(headerRow, "fact_game_total"))
    : null;
  const displayHeaderRow =
    headerRow && lineScoreRow ? headerWithLineScoreLabels(headerRow, lineScoreRow) : headerRow;
  const periodScores =
    displayHeaderRow && lineScoreRow
      ? periodScoresFromLineScore(lineScoreRow, displayHeaderRow)
      : [];
  const teamBoxes = factTeamBoxes.length > 0 ? factTeamBoxes : extendedTeamBoxes;
  const playerBoxes = factPlayerBoxes.length > 0 ? factPlayerBoxes : extendedPlayerBoxes;
  const context = extendedContext.length > 0 ? extendedContext : factContext;
  return {
    header: displayHeaderRow,
    metadata: buildMetadata(displayHeaderRow),
    lineScore: lineScoreRow,
    periodScores,
    teamBoxes,
    playerBoxes,
    leaders,
    officials,
    starters,
    lastPlays,
    context,
    coverage: buildCoverage(
      displayHeaderRow,
      lineScoreRow,
      periodScores,
      teamBoxes,
      playerBoxes,
      officials,
      starters,
      lastPlays,
      context,
    ),
  };
}

// ---------------------------------------------------------------------------
// Award voting detail (BBR voting shares via the crosswalk)
// ---------------------------------------------------------------------------

export async function getAwardVoting(season: string, award: string): Promise<Row[]> {
  return queryObjects(
    `WITH ${PLAYER_BBR_XWALK_CTE},
     bref_name_span_xwalk AS (
       SELECT normalized_player_name, nba_player_id, "from" AS from_year, "to" AS to_year
       FROM stg_bref_player_career_info
       WHERE nba_player_id IS NOT NULL
       QUALIFY ROW_NUMBER() OVER (
         PARTITION BY normalized_player_name, "from", "to"
         ORDER BY nba_player_id
       ) = 1
     )
     SELECT COALESCE(s.nba_player_id, x.nba_player_id, nx.nba_player_id) AS player_id,
            coalesce(p.full_name, s.player) AS full_name,
            s.age, s.first AS first_place_votes,
            s.pts_won, s.pts_max, s.share, s.winner
     FROM stg_bref_player_award_shares s
     LEFT JOIN player_bbr_xwalk x ON x.bbr_player_id = s.bref_player_id
     LEFT JOIN bref_name_span_xwalk nx
       ON s.nba_player_id IS NULL
       AND s.bref_player_id IS NULL
       AND nx.normalized_player_name = s.normalized_player_name
       AND s.season BETWEEN nx.from_year AND nx.to_year
     LEFT JOIN dim_player p
       ON p.player_id = COALESCE(s.nba_player_id, x.nba_player_id, nx.nba_player_id)
       AND p.is_current
     WHERE s.season = TRY_CAST(? AS INTEGER) AND s.award = ?
     ORDER BY s.pts_won DESC NULLS LAST, s.share DESC NULLS LAST`,
    [season, award],
  );
}
