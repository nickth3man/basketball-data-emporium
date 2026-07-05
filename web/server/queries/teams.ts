import { queryObjects } from "../db.ts";
import type { Row } from "./shared.ts";

// ---------------------------------------------------------------------------
// Teams
//
// dim_team_era (is_current=true) is the canonical "30 real franchises" list
// with clean names; dim_team has duplicate per-era rows for relocated
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
     FROM dim_team_era
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
     SELECT era.team_id, era.nickname AS team_name, era.abbreviation, ls.conference
     FROM dim_team_era era
     JOIN latest_standing ls ON ls.team_id = era.team_id
     WHERE era.is_current
     ORDER BY ls.conference, era.nickname`,
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
    queryObjects(`SELECT * FROM dim_team_era WHERE team_id = ? AND is_current LIMIT 1`, [teamId]),
    queryObjects(
      `SELECT arena, year_founded FROM dim_team WHERE team_id = ? ORDER BY year_founded DESC LIMIT 1`,
      [teamId],
    ),
    // src_team_details (legacy fact_team_background) carries bio fields
    // dim_team never populated: arena capacity, owner, GM, current head
    // coach, D-League affiliate, and social links. team_id is VARCHAR there.
    queryObjects(
      `SELECT arenacapacity, owner, generalmanager, headcoach, dleagueaffiliation,
                facebook, instagram, twitter
         FROM src_team_details WHERE TRY_CAST(team_id AS BIGINT) = ? LIMIT 1`,
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
         FROM src_stg_bref_team_stats_per_game pg
         LEFT JOIN src_stg_bref_team_summaries ts
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
           era.nickname AS team_name,
           era.abbreviation AS team_abbreviation,
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
         FROM src_agg_team_season s
         LEFT JOIN dim_team_era era
           ON era.team_id = s.team_id
           AND CAST(left(s.season_year, 4) AS INTEGER) BETWEEN era.valid_from_year AND era.valid_to_year
         LEFT JOIN src_agg_team_pace_and_efficiency p
           ON p.team_id = s.team_id
           AND p.season_year = s.season_year
           AND p.season_type = s.season_type
         WHERE s.team_id = ?
           AND s.season_type <> 'Regular'
         QUALIFY ROW_NUMBER() OVER (
           PARTITION BY s.team_id, s.season_year, s.season_type
           ORDER BY CASE WHEN era.team_id IS NOT NULL THEN 0 ELSE 1 END
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
    queryObjects(`SELECT * FROM dim_team_era WHERE team_id = ? ORDER BY valid_from_year`, [teamId]),
    queryObjects(
      `WITH team_games AS (
           SELECT tb.*, g.game_date
           FROM fact_team_game_box tb
           JOIN dim_game g ON g.game_id = tb.game_id
           WHERE tb.team_id = ?
         ),
         opponent_games AS (
           SELECT tb.*, g.game_date
           FROM fact_team_game_box tb
           JOIN dim_game g ON g.game_id = tb.game_id
           WHERE tb.team_id <> ?
         )
         SELECT
           tg.game_id,
           tg.game_date::VARCHAR AS game_date,
           og.team_abbreviation AS opponent,
           CASE WHEN tg.is_home THEN 'Home' ELSE 'Away' END AS "location",
           tg.pts AS team_pts,
           og.pts AS opp_pts,
           CASE WHEN tg.is_win THEN 'W' ELSE 'L' END AS result
         FROM team_games tg
         LEFT JOIN opponent_games og ON og.game_id = tg.game_id
         ORDER BY tg.game_date DESC
         LIMIT 20`,
      [teamId, teamId],
    ),
    // src_agg_team_franchise has useful all-time W/L rows, but its title
    // fields are all zero/null and current franchises use an end_year
    // sentinel of 2100. Keep the useful totals and blank the known-bad
    // title/age signals.
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
       FROM src_agg_team_franchise
       WHERE team_id = ?
       LIMIT 1`,
      [teamId],
    ),
    // Top-15 career-alumni list from src_fact_franchise_players, regular
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
         FROM src_fact_franchise_players fp
         JOIN dim_player p ON p.player_id = fp.person_id
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
  // dim_player no longer has an is_current/is_active SCD marker for roster
  // membership. src_bridge_player_team_season is season-membership, so
  // players who changed teams during the latest season legitimately appear
  // under multiple teams there. Use the NBA current-player index as the
  // assignment source, then only use same-team latest bridge rows to
  // supplement jersey/position fields.
  return queryObjects(
    `WITH current_assignments AS (
       SELECT person_id
       FROM src_stg_common_all_players
       WHERE team_id = ? AND roster_status = 1
       QUALIFY ROW_NUMBER() OVER (PARTITION BY person_id ORDER BY TRY_CAST(to_year AS INTEGER) DESC) = 1
     ),
     latest_bridge AS (
       SELECT b.player_id, b.position, b.jersey_number
       FROM src_bridge_player_team_season b
       WHERE b.team_id = ?
         AND b.season_year = (
           SELECT MAX(season_year)
           FROM src_bridge_player_team_season
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
     JOIN dim_player p ON p.player_id = ca.person_id
     LEFT JOIN latest_bridge lb ON lb.player_id = p.player_id
     ORDER BY p.full_name`,
    [teamId, teamId, teamId],
  );
}

// ---------------------------------------------------------------------------
// Playoff series-by-series
//
// Derived entirely from dim_game (complete game dimension with scores). A
// team plays each opponent at most once per postseason, so grouping a
// team's playoff games by opponent IS the series, and ordering series
// chronologically within a season reproduces the round order (First Round,
// Conf. Semis, Conf. Finals, Finals) without bracket reconstruction. Play-in
// games are excluded (dim_game classifies them under season_type 'Regular').
// ---------------------------------------------------------------------------

export async function getTeamPlayoffSeries(teamId: number): Promise<Row[]> {
  return queryObjects(
    `WITH team_games AS (
       SELECT
         g.season_year AS season_id,
         g.game_date,
         CASE WHEN g.winner_team_id = ? THEN 'W' ELSE 'L' END AS team_wl,
         CASE WHEN g.home_team_id = ? THEN g.away_team_id ELSE g.home_team_id END AS opponent_team_id
       FROM dim_game g
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
       COALESCE(era.abbreviation, cur_era.abbreviation) AS opponent_abbreviation,
       COALESCE(era.nickname, cur_era.nickname) AS opponent_name
     FROM series_agg sa
     LEFT JOIN dim_team_era era
       ON era.team_id = sa.opponent_team_id
       AND CAST(left(sa.season_id, 4) AS INTEGER) BETWEEN era.valid_from_year AND era.valid_to_year
     LEFT JOIN dim_team_era cur_era
       ON cur_era.team_id = sa.opponent_team_id AND cur_era.is_current
     ORDER BY sa.season_id DESC, round_number`,
    [teamId, teamId, teamId],
  );
}

// ---------------------------------------------------------------------------
// Historical coach-by-season
//
// fact_coach_season is now a proper warehouse table (season, wins, losses,
// win_pct, coach name) replacing the BBR JSONL-at-query-time lookup.
// ---------------------------------------------------------------------------

export async function getTeamCoachHistory(teamId: number): Promise<Row[]> {
  return queryObjects(
    `SELECT
       season_year,
       coach_name,
       wins,
       losses,
       win_pct
     FROM fact_coach_season
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
    `SELECT group_id, season_year, total_gp, total_min, pts_per48, avg_net_rating, total_plus_minus
     FROM src_agg_lineup_efficiency
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
     FROM src_fact_team_season_ranks
     WHERE team_id = ?
     ORDER BY season_id DESC, season_type`,
    [teamId],
  );
}

// ---------------------------------------------------------------------------
// Opponent four-factors
//
// src_agg_team_defense carries the defensive side of the four factors
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
     FROM src_agg_team_defense
     WHERE team_id = ?
     ORDER BY season_year DESC, season_type`,
    [teamId],
  );
}

// ---------------------------------------------------------------------------
// Franchise Leaders (per-team career leaders)
//
// mart_franchise_leaders has one row per team_id with five stat leaders
// (pts/ast/reb/blk/stl), each stored as `<stat>` value plus `<stat>_player_id`
// — a direct, prebuilt replacement for the old five-CTE-plus-CROSS-JOIN
// query. Join dim_player to resolve each leader's display name.
// ---------------------------------------------------------------------------

export async function getFranchiseLeaders(teamId: number): Promise<Row | null> {
  const rows = await queryObjects(
    `SELECT
       l.team_id,
       l.pts,
       l.pts_player_id,
       p_pts.full_name AS pts_leader_name,
       l.ast,
       l.ast_player_id,
       p_ast.full_name AS ast_leader_name,
       l.reb,
       l.reb_player_id,
       p_reb.full_name AS reb_leader_name,
       l.blk,
       l.blk_player_id,
       p_blk.full_name AS blk_leader_name,
       l.stl,
       l.stl_player_id,
       p_stl.full_name AS stl_leader_name
     FROM mart_franchise_leaders l
     LEFT JOIN dim_player p_pts ON p_pts.player_id = l.pts_player_id
     LEFT JOIN dim_player p_ast ON p_ast.player_id = l.ast_player_id
     LEFT JOIN dim_player p_reb ON p_reb.player_id = l.reb_player_id
     LEFT JOIN dim_player p_blk ON p_blk.player_id = l.blk_player_id
     LEFT JOIN dim_player p_stl ON p_stl.player_id = l.stl_player_id
     WHERE l.team_id = ?`,
    [teamId],
  );
  return rows[0] ?? null;
}

// Whitelist of stat keys the client can sort by. Maps onto the numeric
// columns of src_fact_franchise_players (all DOUBLE in the schema).
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
     FROM src_fact_franchise_players fp
     JOIN dim_player p ON p.player_id = fp.person_id
     WHERE fp.team_id = ?
       AND fp.season_type = 'Regular'
     ORDER BY fp.${sortKey} DESC NULLS LAST, p.full_name ASC
     LIMIT ?`,
    [teamId, limit],
  );
}

// ---------------------------------------------------------------------------
// Team head-to-head + season context
// ---------------------------------------------------------------------------

export async function getTeamHeadToHead(teamId: number): Promise<Row[]> {
  return queryObjects(
    `SELECT h.opponent_team_id,
            coalesce(era.abbreviation, max(h.opponent_team_id)::VARCHAR) AS opponent_abbreviation,
            coalesce(era.nickname, max(h.opponent_team_id)::VARCHAR) AS opponent_name,
            CAST(sum(h.games_played) AS BIGINT) AS gp,
            CAST(sum(h.wins) AS BIGINT) AS wins,
            CAST(sum(h.losses) AS BIGINT) AS losses,
            round(sum(h.avg_pts_scored * h.games_played) / nullif(sum(h.games_played), 0), 1) AS avg_pts_scored,
            round(sum(h.avg_pts_allowed * h.games_played) / nullif(sum(h.games_played), 0), 1) AS avg_pts_allowed,
            round(sum(h.avg_margin * h.games_played) / nullif(sum(h.games_played), 0), 1) AS avg_margin
     FROM mart_head_to_head h
     LEFT JOIN dim_team_era era ON era.team_id = h.opponent_team_id AND era.is_current
     WHERE h.team_id = ?
     GROUP BY h.opponent_team_id, era.abbreviation, era.nickname
     ORDER BY gp DESC, wins DESC`,
    [teamId],
  );
}

// BBR team-season context (SRS, pace, ratings, four factors both ways).
// src_stg_bref_team_summaries is keyed by BBR abbreviation+season and
// carries the crosswalk-resolved nba_team_id added at import time.
export async function getTeamSeasonContext(teamId: number): Promise<Row[]> {
  return queryObjects(
    `SELECT season, w, l, pw, pl, srs, sos, pace, o_rtg, d_rtg, n_rtg,
            e_fg_percent, tov_percent, orb_percent, ft_fga,
            opp_e_fg_percent, opp_tov_percent, drb_percent, opp_ft_fga,
            attend_g
     FROM src_stg_bref_team_summaries
     WHERE nba_team_id = ? AND NOT playoffs
     ORDER BY season DESC`,
    [teamId],
  );
}
