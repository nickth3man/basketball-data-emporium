import { queryObjects } from "../db.ts";
import type { Row } from "./shared.ts";

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
