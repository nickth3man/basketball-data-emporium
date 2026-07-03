import { queryObjects } from "../db.ts";
import type { Row } from "./shared.ts";

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

const TEAM_SIDES: readonly TeamSide[] = ["away", "home"];

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
  for (const side of TEAM_SIDES) {
    const teamId = lineScore[`team_id_${side}`];
    const teamName = labelFromLineScore(lineScore, side);
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
  return TEAM_SIDES.map((side) => ({
    line_score_source: source,
    team_id: lineScore[`team_id_${side}`],
    team_side: side,
    team_name: labelFromLineScore(lineScore, side),
    period: null,
    period_label: "Final",
    pts: lineScore[`pts_${side}`],
    is_final_only: true,
  }));
}

const METADATA_KEYS = [
  "game_id",
  "game_date",
  "game_datetime_est",
  "season_year",
  "season_type",
  "game_type",
  "game_subtype",
  "game_label",
  "game_sub_label",
  "series_game_number",
  "game_status",
  "game_status_text",
  "game_clock",
  "game_time_utc",
  "game_et",
  "game_duration",
  "arena_id",
  "arena_name",
  "arena_city",
  "arena_state",
  "attendance",
  "sellout",
  "is_overtime",
  "odds_home",
  "odds_away",
] as const;

function pickRow(row: Row, keys: readonly string[]): Row {
  const picked: Row = {};
  for (const key of keys) picked[key] = row[key];
  return picked;
}

function buildMetadata(header: Row | null): Row | null {
  if (!header) return null;
  return pickRow(header, METADATA_KEYS);
}

function coverageLabel(hasFullModernBox: boolean, isHistoricalPartial: boolean): string {
  if (hasFullModernBox) return "Full modern box score";
  if (isHistoricalPartial) return "Partial historical box score";
  return "Partial box score";
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
    coverage_label: coverageLabel(hasFullModernBox, isHistoricalPartial),
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
