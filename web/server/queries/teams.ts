import { queryObjects } from "../db.ts";
import { BBR_COACHES_AVAILABLE, BBR_COACHES_PATH, escapeDuckDbPath, type Row } from "./shared.ts";

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
  const safePath = escapeDuckDbPath(BBR_COACHES_PATH);
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
