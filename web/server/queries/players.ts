import type { DuckDBValue } from "@duckdb/node-api";
import { HONOR_LABELS } from "../../src/awards.ts";
import { queryObjects } from "../db.ts";
import { colorForEra } from "../teamColorEras.ts";
import {
  AWARD_ROWS_CTE,
  DRAFT_SOURCE_CTE,
  PLAYER_BBR_XWALK_CTE,
  PLAYER_EXTRA_BIO_CTE,
  PLAYER_SEASON_STATS_CTE,
  type Row,
} from "./shared.ts";

// ---------------------------------------------------------------------------
// Players
//
// dim_player is now one row per player (no more SCD is_current filtering
// needed). Player-facing season/career stats read from mart_player_season /
// mart_player_career; awards read from fact_award; jersey history reads from
// fact_player_jersey_season (pre-baked source-priority tiers).
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
       FROM fact_player_game_box
       GROUP BY player_id
     ),
     current_players AS (
       SELECT
         p.player_id,
         p.full_name,
         p.position,
         p.is_active,
         cur.abbreviation AS team_abbreviation,
         COALESCE(ps.game_count, 0) AS game_count,
         COUNT(*) OVER (PARTITION BY p.full_name) AS same_name_count
       FROM dim_player p
       LEFT JOIN dim_team_era cur ON cur.team_id = p.current_team_id AND cur.is_current
       LEFT JOIN player_signal ps ON ps.player_id = p.player_id
       WHERE (length(?) = 0 OR p.full_name ILIKE ?)
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
// ---------------------------------------------------------------------------

export async function getFeaturedPlayer(): Promise<Row | null> {
  const rows = await queryObjects(
    `WITH ${PLAYER_SEASON_STATS_CTE},
     featured AS (
       SELECT player_id FROM dim_player ORDER BY random() LIMIT 1
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
       p.player_id, p.full_name, p.position, cur.abbreviation AS team_abbreviation,
       c.career_gp,
       c.career_ppg
     FROM featured f
     JOIN dim_player p ON p.player_id = f.player_id
     LEFT JOIN dim_team_era cur ON cur.team_id = p.current_team_id AND cur.is_current
     LEFT JOIN career c ON c.player_id = p.player_id`,
  );
  return rows[0] ?? null;
}

// ---------------------------------------------------------------------------
// Players tab: paginated/filterable roster browse
// ---------------------------------------------------------------------------

const PLAYER_BROWSE_SORT_CLAUSES: Record<string, string> = {
  name: "p.full_name ASC",
  team: "cur.abbreviation ASC NULLS LAST, p.full_name ASC",
  active: "p.is_active DESC, p.full_name ASC",
};

export interface BrowsePlayerRow extends Row {
  player_id: number;
  full_name: string;
  position: string | null;
  is_active: boolean;
  team_id: number | null;
  team_abbreviation: string | null;
  team_name: string | null;
}

export interface BrowsePlayerFacetTeam {
  team_id: number;
  abbreviation: string;
  name: string;
}

export interface BrowsePlayerFacets {
  totalPlayers: number;
  activePlayers: number;
  teams: BrowsePlayerFacetTeam[];
  positions: string[];
}

export interface BrowsePlayersResult {
  rows: BrowsePlayerRow[];
  total: number;
  facets: BrowsePlayerFacets;
}

export async function browsePlayers(opts: {
  q?: string | null;
  position?: string | null;
  teamId?: number | null;
  active?: boolean | null;
  letter?: string | null;
  sort?: string;
  limit: number;
  offset: number;
}): Promise<BrowsePlayersResult> {
  const q = (opts.q ?? "").trim();
  const position = (opts.position ?? "").trim();
  const teamId = opts.teamId ?? null;
  const active = opts.active ?? null;
  const letter = (opts.letter ?? "").trim().toUpperCase();
  const sortClause =
    PLAYER_BROWSE_SORT_CLAUSES[opts.sort ?? "name"] ?? PLAYER_BROWSE_SORT_CLAUSES.name;
  const { limit, offset } = opts;

  const where = `WHERE (length(?) = 0 OR p.full_name ILIKE ?)
      AND (? = '' OR p.position = ?)
      AND (? IS NULL OR p.current_team_id = ?)
      AND (? IS NULL OR p.is_active = ?)
      AND (? = '' OR p.full_name ILIKE ? || '%')`;
  const filterParams: DuckDBValue[] = [
    q,
    `%${q}%`,
    position,
    position,
    teamId,
    teamId,
    active,
    active,
    letter,
    letter,
  ];
  const baseFrom = `FROM dim_player p
    LEFT JOIN dim_team_era cur ON cur.team_id = p.current_team_id AND cur.is_current`;

  const [countRows, pageRows, totalPlayersRows, activePlayersRows, teams, positionRows] =
    await Promise.all([
      queryObjects<{ n: number | bigint }>(
        `SELECT COUNT(*) AS n ${baseFrom} ${where}`,
        filterParams,
      ),
      queryObjects<BrowsePlayerRow>(
        `SELECT
           p.player_id,
           p.full_name,
           p.position,
           p.is_active,
           p.current_team_id AS team_id,
           cur.abbreviation AS team_abbreviation,
           cur.nickname AS team_name
         ${baseFrom}
         ${where}
         ORDER BY ${sortClause}
         LIMIT ? OFFSET ?`,
        [...filterParams, limit, offset],
      ),
      queryObjects<{ n: number | bigint }>(`SELECT COUNT(*) AS n FROM dim_player`),
      queryObjects<{ n: number | bigint }>(`SELECT COUNT(*) AS n FROM dim_player WHERE is_active`),
      queryObjects<BrowsePlayerFacetTeam>(
        `SELECT team_id, abbreviation, nickname AS name
         FROM dim_team_era
         WHERE is_current
         ORDER BY abbreviation`,
      ),
      queryObjects<{ position: string }>(
        `SELECT DISTINCT position
         FROM dim_player
         WHERE position IS NOT NULL
         ORDER BY position`,
      ),
    ]);

  return {
    rows: pageRows,
    total: Number(countRows[0]?.n ?? 0),
    facets: {
      totalPlayers: Number(totalPlayersRows[0]?.n ?? 0),
      activePlayers: Number(activePlayersRows[0]?.n ?? 0),
      teams,
      positions: positionRows.map((r) => r.position),
    },
  };
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
         COALESCE(era.abbreviation, s.source_team_abbreviation) AS team_abbreviation,
         s.total_pts * 36 / NULLIF(s.total_min, 0) AS pts_per36,
         s.total_reb * 36 / NULLIF(s.total_min, 0) AS reb_per36,
         s.total_ast * 36 / NULLIF(s.total_min, 0) AS ast_per36,
         s.total_stl * 36 / NULLIF(s.total_min, 0) AS stl_per36,
         s.total_blk * 36 / NULLIF(s.total_min, 0) AS blk_per36,
         s.total_tov * 36 / NULLIF(s.total_min, 0) AS tov_per36,
         false AS is_cup_final_only
       FROM player_season_stats s
       LEFT JOIN dim_team_era era
         ON era.team_id = s.team_id
         AND CAST(LEFT(s.season_year, 4) AS INTEGER) BETWEEN era.valid_from_year AND era.valid_to_year
       WHERE s.player_id = ?
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
         COALESCE(era.abbreviation, s.source_team_abbreviation) AS team_abbreviation,
         s.total_pts * 48 / NULLIF(s.total_min, 0) AS pts_per48,
         s.total_reb * 48 / NULLIF(s.total_min, 0) AS reb_per48,
         s.total_ast * 48 / NULLIF(s.total_min, 0) AS ast_per48,
         s.total_stl * 48 / NULLIF(s.total_min, 0) AS stl_per48,
         s.total_blk * 48 / NULLIF(s.total_min, 0) AS blk_per48,
         s.total_tov * 48 / NULLIF(s.total_min, 0) AS tov_per48,
         false AS is_cup_final_only
       FROM player_season_stats s
       LEFT JOIN dim_team_era era
         ON era.team_id = s.team_id
         AND CAST(LEFT(s.season_year, 4) AS INTEGER) BETWEEN era.valid_from_year AND era.valid_to_year
       WHERE s.player_id = ?
       ORDER BY s.season_year, s.season_type`,
      [playerId],
    ),
  ]);
  return { per36, per48 };
}

// ---------------------------------------------------------------------------
// Advanced stats per season
//
// fact_player_season_box carries BBR-derived value metrics (PER, OWS/DWS,
// OBPM/DBPM, VORP) directly — the replacement for the old
// fact_player_season_stat_resolved-based CTE, which had these baked in.
// NBA tracking-only context (pace/PIE/AST ratio) is overlaid live from
// fact_player_game_advanced, which already carries its own season_type
// column (no more join through a separate game-log table needed).
// ---------------------------------------------------------------------------

export async function getPlayerAdvancedStats(playerId: number): Promise<Row[]> {
  return queryObjects(
    `WITH nba_tracking AS (
       SELECT
         player_id,
         team_id,
         season_year,
         season_type,
         AVG(off_rating) AS avg_off_rating,
         AVG(def_rating) AS avg_def_rating,
         AVG(net_rating) AS avg_net_rating,
         AVG(ts_pct) AS avg_ts_pct,
         AVG(usg_pct) AS avg_usg_pct,
         AVG(efg_pct) AS avg_efg_pct,
         AVG(ast_pct) AS avg_ast_pct,
         AVG(ast_ratio) AS avg_ast_ratio,
         AVG(oreb_pct) AS avg_oreb_pct,
         AVG(dreb_pct) AS avg_dreb_pct,
         AVG(reb_pct) AS avg_reb_pct,
         AVG(pace) AS avg_pace,
         AVG(pie) AS avg_pie
       FROM fact_player_game_advanced
       WHERE player_id = ?
       GROUP BY player_id, team_id, season_year, season_type
     )
     SELECT
       b.player_id,
       b.season_year,
       b.season_type,
       b.gp,
       COALESCE(n.avg_off_rating, b.ortg) AS avg_off_rating,
       COALESCE(n.avg_def_rating, b.drtg) AS avg_def_rating,
       COALESCE(n.avg_net_rating, b.ortg - b.drtg) AS avg_net_rating,
       COALESCE(n.avg_ts_pct, b.ts_pct) AS avg_ts_pct,
       COALESCE(n.avg_usg_pct, b.usgp / 100.0) AS avg_usg_pct,
       n.avg_efg_pct,
       COALESCE(n.avg_ast_pct, b.astp / 100.0) AS avg_ast_pct,
       n.avg_ast_ratio,
       COALESCE(n.avg_oreb_pct, b.orbp / 100.0) AS avg_oreb_pct,
       COALESCE(n.avg_dreb_pct, b.drbp / 100.0) AS avg_dreb_pct,
       COALESCE(n.avg_reb_pct, b.trbp / 100.0) AS avg_reb_pct,
       b.tovp / 100.0 AS avg_tov_pct,
       n.avg_pace,
       n.avg_pie,
       b.per,
       b.ows,
       b.dws,
       b.ows + b.dws AS ws,
       b.obpm,
       b.dbpm,
       b.obpm + b.dbpm AS bpm,
       b.vorp,
       COALESCE(era.abbreviation, b.team_abbreviation) AS team_abbreviation,
       b.team_id,
       false AS is_cup_final_only
     FROM fact_player_season_box b
     LEFT JOIN nba_tracking n
       ON n.player_id = b.player_id
       AND n.team_id = b.team_id
       AND n.season_year = b.season_year
       AND n.season_type = b.season_type
     LEFT JOIN dim_team_era era
       ON era.team_id = b.team_id
       AND CAST(LEFT(b.season_year, 4) AS INTEGER) BETWEEN era.valid_from_year AND era.valid_to_year
     WHERE b.player_id = ?
     ORDER BY b.season_year, b.season_type`,
    [playerId, playerId],
  );
}

// ---------------------------------------------------------------------------
// Per-100-possession player view
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
       FROM src_stg_bref_per_100_poss p
       LEFT JOIN player_bbr_xwalk x
         ON x.bbr_player_id = p.bref_player_id
       LEFT JOIN map_team_bbr bt
         ON bt.season = p.season
         AND bt.bbr_abbreviation = p.team
         AND bt.lg = p.lg
       WHERE p.lg = 'NBA'
         AND p.team NOT IN ('TOT', '2TM', '3TM', '4TM', '5TM')
     )
     SELECT
       p.season_year,
       p.season_type,
       COALESCE(era.abbreviation, bt.team_abbreviation, p.source_team_abbreviation) AS team_abbreviation,
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
     LEFT JOIN map_team_bbr bt
       ON bt.team_id = p.team_id
       AND bt.season = CAST(SUBSTRING(p.season_year, 1, 4) AS INTEGER) + 1
     LEFT JOIN dim_team_era era
       ON era.team_id = p.team_id
       AND CAST(LEFT(p.season_year, 4) AS INTEGER) BETWEEN era.valid_from_year AND era.valid_to_year
     WHERE p.player_id = ?
     ORDER BY p.season_year, p.season_type`,
    [playerId],
  );
}

// ---------------------------------------------------------------------------
// Career/game highs
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
      `(SELECT '${s.label}' AS stat, g.${s.key} AS value, g.game_date::VARCHAR AS game_date,
               COALESCE(era.abbreviation, cur.abbreviation) AS team_abbreviation
        FROM fact_player_game_box g
        LEFT JOIN dim_team_era era
          ON era.team_id = g.team_id
          AND CAST(LEFT(g.season_year, 4) AS INTEGER) BETWEEN era.valid_from_year AND era.valid_to_year
        LEFT JOIN dim_team_era cur ON cur.team_id = g.team_id AND cur.is_current
        WHERE g.player_id = ? AND g.${s.key} IS NOT NULL
        ORDER BY g.${s.key} DESC, g.game_date ASC
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
// fact_player_game_box already carries is_home/is_win/opponent_team_id
// directly, so no more self-join against a separate team-log table is
// needed for the opponent side or the W/L result.
// ---------------------------------------------------------------------------

export async function getPlayerRecentGames(playerId: number): Promise<Row[]> {
  const rows = await queryObjects(
    `SELECT
       pg.game_id, pg.game_date::VARCHAR AS game_date, pg.season_type, pg.season_year,
       COALESCE(era.abbreviation, cur.abbreviation) AS team_abbreviation, pg.team_id,
       COALESCE(opp_era.abbreviation, opp_cur.abbreviation) AS opponent, pg.opponent_team_id,
       CASE WHEN pg.is_home THEN 'Home' ELSE 'Away' END AS location,
       CASE WHEN pg.is_win THEN 'W' ELSE 'L' END AS result,
       pg.min, pg.pts, pg.reb, pg.ast, pg.stl, pg.blk,
       pg.fgm, pg.fga, pg.fg3m, pg.fg3a, pg.ftm, pg.fta, pg.plus_minus
     FROM fact_player_game_box pg
     LEFT JOIN dim_team_era era
       ON era.team_id = pg.team_id
       AND CAST(LEFT(pg.season_year, 4) AS INTEGER) BETWEEN era.valid_from_year AND era.valid_to_year
     LEFT JOIN dim_team_era cur ON cur.team_id = pg.team_id AND cur.is_current
     LEFT JOIN dim_team_era opp_era
       ON opp_era.team_id = pg.opponent_team_id
       AND CAST(LEFT(pg.season_year, 4) AS INTEGER) BETWEEN opp_era.valid_from_year AND opp_era.valid_to_year
     LEFT JOIN dim_team_era opp_cur ON opp_cur.team_id = pg.opponent_team_id AND opp_cur.is_current
     WHERE pg.player_id = ?
     ORDER BY pg.game_date DESC
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
// fact_shot already carries shot_zone_basic/shot_zone_area/shot_zone_range
// precomputed (unlike the old fact_shot_chart, which only had raw
// loc_x/loc_y) — the hand-rolled zone classification is no longer needed.
// ---------------------------------------------------------------------------

export async function getPlayerShotSplits(playerId: number): Promise<Row[]> {
  return queryObjects(
    `WITH player_seasons AS (
       SELECT DISTINCT season_year, season_type FROM fact_shot WHERE player_id = ?
     ),
     league_avg AS (
       SELECT f.season_year, f.season_type, f.shot_zone_basic,
              SUM(f.shot_made_flag) AS league_makes, COUNT(*) AS league_attempts
       FROM fact_shot f
       JOIN player_seasons ps ON ps.season_year = f.season_year AND ps.season_type = f.season_type
       GROUP BY f.season_year, f.season_type, f.shot_zone_basic
     ),
     player_zones AS (
       SELECT
         season_year, season_type, shot_zone_basic, shot_zone_area, shot_zone_range,
         COUNT(*) AS attempts,
         SUM(shot_made_flag) AS makes,
         SUM(shot_made_flag) / NULLIF(COUNT(*), 0) AS fg_pct,
         AVG(shot_distance) AS avg_distance
       FROM fact_shot
       WHERE player_id = ?
       GROUP BY season_year, season_type, shot_zone_basic, shot_zone_area, shot_zone_range
     )
     SELECT
       pz.season_year, pz.season_type, pz.shot_zone_basic, pz.shot_zone_area, pz.shot_zone_range,
       pz.attempts, pz.makes, pz.fg_pct, pz.avg_distance,
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
// src_agg_on_off_splits carries both player- and team-level rows
// (entity_type); only the player rows are surfaced here.
// ---------------------------------------------------------------------------

export async function getPlayerOnOffSplits(playerId: number): Promise<Row[]> {
  return queryObjects(
    `SELECT season_year, season_type, on_off, gp, min, pts, reb, ast, off_rating, def_rating, net_rating
     FROM src_agg_on_off_splits
     WHERE entity_type = 'player' AND entity_id = ?
     ORDER BY season_year, season_type, on_off`,
    [playerId],
  );
}

// ---------------------------------------------------------------------------
// Form tracker (rolling averages)
//
// mart_player_rolling has precomputed 5/10/20-game rolling pts/reb/ast for
// every player game back to 1946. Per-game actuals come from
// analytics_player_game_complete, which has the known agg-layer fan-out
// (duplicate player-game rows), so it is deduped with any_value() before
// joining.
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
       r.game_date::VARCHAR AS game_date,
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
     FROM mart_player_rolling r
     LEFT JOIN game_line g ON g.game_id = r.game_id
     WHERE r.player_id = ?
     ORDER BY r.game_date DESC
     LIMIT ?`,
    [playerId, playerId, limit],
  );
}

// ---------------------------------------------------------------------------
// Draft combine measurements
// ---------------------------------------------------------------------------

export async function getPlayerDraftCombine(playerId: number): Promise<Row | null> {
  const rows = await queryObjects(
    `SELECT
       c.season,
       c.height_wo_shoes, c.height_w_shoes, c.weight, c.wingspan, c.standing_reach,
       c.body_fat_pct, c.hand_length, c.hand_width,
       d.standing_vertical_leap, d.max_vertical_leap, d.lane_agility_time,
       d.modified_lane_agility_time, d.three_quarter_sprint, d.bench_press
     FROM src_stg_draft_combine c
     LEFT JOIN src_stg_draft_combine_drills d ON d.player_id = c.player_id AND d.season = c.season
     WHERE c.player_id = ?
     ORDER BY c.season DESC
     LIMIT 1`,
    [playerId],
  );
  return rows[0] ?? null;
}

// ---------------------------------------------------------------------------
// Similar players
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
     JOIN dim_player p ON p.player_id = c.player_id
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

// Jersey numbers worn per team-stint. fact_player_jersey_season pre-bakes
// the source-priority tiers this used to require a hand-tuned three-way
// UNION for (game_inactive_list > bbr_roster > inferred, confirmed via
// `source` column values). A plain GROUP BY (team_id, jersey_num) would be
// wrong for players who wore the same number across non-contiguous stints
// (e.g. LeBron #23 for Cleveland 2003-10 and again 2014-18) — this needs
// gaps-and-islands grouping: bucket to one (team, number) per season first
// (best-priority source wins), then split into a new group wherever the
// (team, number) at position N in the chronological sequence differs from
// position N-1.
async function getJerseyHistoryRows(playerId: number): Promise<JerseySeasonRow[]> {
  const rows = await queryObjects(
    `WITH jersey_rows AS (
       SELECT
         team_id,
         TRIM(jersey_number) AS jersey_num,
         season_year,
         CASE source
           WHEN 'game_inactive_list' THEN 1
           WHEN 'bbr_roster' THEN 2
           ELSE 3
         END AS source_priority
       FROM fact_player_jersey_season
       WHERE player_id = ? AND jersey_number IS NOT NULL AND TRIM(jersey_number) != ''
     ),
     best_per_season AS (
       SELECT team_id, jersey_num, season_year
       FROM jersey_rows
       QUALIFY ROW_NUMBER() OVER (PARTITION BY team_id, season_year ORDER BY source_priority) = 1
     ),
     combined_with_first AS (
       SELECT team_id, jersey_num, season_year,
              MIN(season_year) OVER (PARTITION BY team_id, jersey_num) AS first_season
       FROM best_per_season
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
       COALESCE(era.abbreviation, cur.abbreviation) AS abbreviation,
       COALESCE(era.nickname, cur.nickname) AS team_name
     FROM grouped g
     JOIN stint_bounds sb
       ON sb.team_id = g.team_id AND sb.jersey_num = g.jersey_num AND sb.stint_group = g.stint_group
     LEFT JOIN dim_team_era era
       ON era.team_id = g.team_id
       AND CAST(LEFT(g.season_year, 4) AS INTEGER) BETWEEN era.valid_from_year AND era.valid_to_year
     LEFT JOIN dim_team_era cur ON cur.team_id = g.team_id AND cur.is_current
     ORDER BY sb.stint_first_season, g.season_year, g.team_id, g.jersey_num`,
    [playerId],
  );
  return rows.map((r) => ({
    team_id: Number(r.team_id),
    jersey_num: String(r.jersey_num),
    season_year: String(r.season_year),
    stint_group: Number(r.stint_group),
    abbreviation: String(r.abbreviation),
    team_name: String(r.team_name),
  }));
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
      `WITH ${PLAYER_EXTRA_BIO_CTE}
       SELECT
         p.player_id,
         p.full_name,
         p.first_name,
         p.last_name,
         p.is_active,
         p.current_team_id,
         COALESCE(eb.common_position, p.bbr_primary_position, p.position) AS position,
         p.current_jersey_number,
         COALESCE(
           eb.common_height,
           CASE WHEN p.bbr_height_inches IS NOT NULL
             THEN CAST(FLOOR(p.bbr_height_inches / 12.0) AS BIGINT)::VARCHAR || '-' ||
                  CAST(p.bbr_height_inches % 12 AS BIGINT)::VARCHAR
           END,
           p.height
         ) AS height,
         CAST(COALESCE(eb.common_weight, p.bbr_weight_lbs::VARCHAR, p.weight::VARCHAR) AS VARCHAR) AS weight,
         COALESCE(eb.common_birth_date, CAST(p.birth_date AS VARCHAR)) AS birth_date,
         COALESCE(eb.common_country, p.country) AS country,
         p.college_id,
         p.draft_year,
         p.draft_round,
         p.draft_number,
         p.from_year,
         p.to_year,
         p.bbr_player_id,
         p.bbr_colleges,
         p.is_hall_of_fame,
         eb.school,
         eb.is_greatest_75,
         eb.season_exp,
         cur.abbreviation AS team_abbreviation,
         cur.nickname AS team_name
       FROM dim_player p
       LEFT JOIN player_extra_bio eb ON eb.player_id = p.player_id
       LEFT JOIN dim_team_era cur ON cur.team_id = p.current_team_id AND cur.is_current
       WHERE p.player_id = ?
       LIMIT 1`,
      [playerId],
    ),
    // Career totals recomputed from mart_player_season (regular season only).
    queryObjects(
      `WITH ${PLAYER_SEASON_STATS_CTE}
       SELECT
         CAST(SUM(gp) AS INTEGER) AS career_gp,
         SUM(total_min) AS career_min,
         CAST(SUM(total_pts) AS INTEGER) AS career_pts,
         CAST(SUM(total_reb) AS INTEGER) AS career_reb,
         CAST(SUM(total_ast) AS INTEGER) AS career_ast,
         CAST(SUM(total_stl) AS INTEGER) AS career_stl,
         CAST(SUM(total_blk) AS INTEGER) AS career_blk,
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
      // Display abbreviation is re-derived from team_id + season era so a
      // trade-year season shows the correct era-accurate franchise name.
      `WITH ${PLAYER_SEASON_STATS_CTE}
       SELECT s.* EXCLUDE (source_team_abbreviation),
              COALESCE(era.abbreviation, s.source_team_abbreviation) AS team_abbreviation,
              false AS is_cup_final_only
       FROM player_season_stats s
       LEFT JOIN dim_team_era era
         ON era.team_id = s.team_id
         AND CAST(LEFT(s.season_year, 4) AS INTEGER) BETWEEN era.valid_from_year AND era.valid_to_year
       WHERE s.player_id = ?
       ORDER BY s.season_year, s.season_type`,
      [playerId],
    ),
    // Player-facing awards come from fact_award directly.
    queryObjects(
      `WITH ${AWARD_ROWS_CTE}
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
    // src_stg_team_retired is mislabeled — its actual content is Hall of
    // Fame induction records, not retired jersey numbers.
    queryObjects(`SELECT DISTINCT year FROM src_stg_team_retired WHERE playerid = ? LIMIT 1`, [
      playerId,
    ]),
    queryObjects(
      `WITH ${AWARD_ROWS_CTE}
       SELECT COUNT(*) AS n
       FROM award_rows
       WHERE player_id = ? AND award_type = 'All-Star'`,
      [playerId],
    ),
    queryObjects(
      `WITH ${AWARD_ROWS_CTE}
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
    getJerseyHistoryRows(playerId),
  ]);

  const badges: Badge[] = [
    ...honorRows.map((r) => ({
      season: seasonRangeFromEndYear(r.season),
      label: HONOR_LABELS[String(r.award_type)] ?? String(r.award_type),
    })),
    ...leaderRows.map((r) => ({
      season: String(r.season_year),
      label: String(r.label),
    })),
  ].sort((a, b) => a.season.localeCompare(b.season));

  const bio = bioRows[0] ?? null;
  const hofYear = hofRows[0]?.year;
  const efg = career[0]?.career_efg_pct;

  return {
    bio,
    career: career[0] ?? null,
    seasons,
    awards,
    draft: draft[0] ?? null,
    hallOfFameYear: hofYear !== undefined && hofYear !== null ? Number(hofYear) : null,
    isGreatest75: bio?.is_greatest_75 === true,
    allStarCount: Number(allStarRows[0]?.n ?? 0),
    careerEfgPct: efg !== undefined && efg !== null ? Number(efg) : null,
    badges,
    jerseyHistory: splitJerseyStintsByColorEra(jerseyRows),
  };
}

// ---------------------------------------------------------------------------
// Player Season Ranks
// ---------------------------------------------------------------------------

export async function getPlayerSeasonRanks(playerId: number, limit = 50): Promise<Row[]> {
  return queryObjects(
    `SELECT
       r.player_id,
       r.season_id,
       r.rank_type,
       r.team_id,
       COALESCE(era.abbreviation, r.team_abbreviation) AS team_abbreviation,
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
     FROM src_fact_player_season_ranks r
     LEFT JOIN dim_team_era era
       ON era.team_id = r.team_id
       AND CAST(LEFT(r.season_id, 4) AS INTEGER) BETWEEN era.valid_from_year AND era.valid_to_year
     WHERE r.player_id = ?
       AND r.league_id = '00'
     ORDER BY r.season_id DESC, CASE r.rank_type WHEN 'Regular' THEN 1 WHEN 'Playoffs' THEN 2 WHEN 'Cup' THEN 3 ELSE 4 END
     LIMIT ?`,
    [playerId, limit],
  );
}

// ---------------------------------------------------------------------------
// Player splits / estimated metrics / shot chart
// ---------------------------------------------------------------------------

export async function getPlayerLocationSplits(playerId: number): Promise<Row[]> {
  return queryObjects(
    `SELECT season_year, group_value, gp, w, l, w_pct, min, pts, reb, ast,
            fg_pct, fg3_pct, ft_pct, plus_minus
     FROM src_analytics_player_general_splits
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
     FROM src_fact_player_estimated_metrics
     WHERE player_id = ?
     ORDER BY season_year`,
    [playerId],
  );
}

export async function listPlayerShotSeasons(playerId: number): Promise<string[]> {
  const rows = await queryObjects<{ season_year: string }>(
    `SELECT DISTINCT season_year FROM fact_shot
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
  const leagueConditions = ["loc_y BETWEEN -52 AND 418", "loc_x BETWEEN -250 AND 250"];
  const leagueParams: DuckDBValue[] = [];
  if (season) {
    leagueConditions.push("season_year = ?");
    leagueParams.push(season);
  }
  return queryObjects(
    `WITH league_bins AS (
       SELECT
         CAST(floor(loc_x / 25) AS INTEGER) AS bin_x,
         CAST(floor(loc_y / 25) AS INTEGER) AS bin_y,
         AVG(shot_made_flag) AS league_fg_pct
       FROM fact_shot
       WHERE ${leagueConditions.join(" AND ")}
       GROUP BY 1, 2
     )
     SELECT
       CAST(floor(f.loc_x / 25) AS INTEGER) AS bin_x,
       CAST(floor(f.loc_y / 25) AS INTEGER) AS bin_y,
       count(*) AS attempts,
       CAST(sum(f.shot_made_flag) AS BIGINT) AS makes,
       round(any_value(lb.league_fg_pct), 3) AS league_fg_pct
     FROM fact_shot f
     LEFT JOIN league_bins lb
       ON lb.bin_x = CAST(floor(f.loc_x / 25) AS INTEGER)
       AND lb.bin_y = CAST(floor(f.loc_y / 25) AS INTEGER)
     WHERE ${conditions.join(" AND ")}
     GROUP BY 1, 2`,
    [...leagueParams, ...params],
  );
}
