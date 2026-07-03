import { queryObjects } from "../db.ts";
import type { Row } from "./shared.ts";

// ---------------------------------------------------------------------------
// Matchup Explorer (who guarded whom)
//
// stg_season_matchups is NBA.com tracking data: one row per
// (offensive player, defender) pair with partial-possession accounting.
// It is a SINGLE-SEASON snapshot with no season column — verified by
// summing a star's offensive matchup minutes against his current-season
// minutes (LeBron: 1,757 matchup min vs a ~2,000-minute 2025-26 season;
// gp maxes out at 5). Treat every number here as "this season so far".
// player_pts is what the offensive player scored while this defender was
// the nearest matchup; partial_poss is the tracking system's fractional
// possession credit, so pts/100 poss is the cleanest efficiency measure.
// ---------------------------------------------------------------------------

const MATCHUP_STAT_COLS = `
  gp::INTEGER AS gp,
  ROUND(matchup_min, 1) AS matchup_min,
  ROUND(partial_poss, 1) AS partial_poss,
  player_pts::INTEGER AS pts,
  CASE WHEN partial_poss > 0 THEN ROUND(player_pts / partial_poss * 100, 1) END AS pts_per100,
  matchup_fgm::INTEGER AS fgm,
  matchup_fga::INTEGER AS fga,
  CASE WHEN matchup_fga > 0 THEN ROUND(matchup_fgm / matchup_fga, 3) END AS fg_pct,
  matchup_fg3m::INTEGER AS fg3m,
  matchup_fg3a::INTEGER AS fg3a,
  matchup_ftm::INTEGER AS ftm,
  matchup_fta::INTEGER AS fta,
  matchup_ast::INTEGER AS ast,
  matchup_tov::INTEGER AS tov,
  matchup_blk::INTEGER AS blk`;

/** Matchup rows for one player. side="offense" lists the defenders who
 *  guarded them; side="defense" lists the scorers they were matched up
 *  against. The opposite player is exposed as player_id/opponent_name so
 *  the shared playerCell can link to their profile. */
export async function getPlayerMatchups(
  playerId: number,
  side: "offense" | "defense",
  limit = 25,
): Promise<Row[]> {
  const opponent =
    side === "offense"
      ? "def_player_id AS player_id, def_player_name AS opponent_name"
      : "off_player_id AS player_id, off_player_name AS opponent_name";
  const filter = side === "offense" ? "off_player_id" : "def_player_id";
  return queryObjects(
    `SELECT ${opponent}, ${MATCHUP_STAT_COLS}
     FROM stg_season_matchups
     WHERE ${filter} = ?
     ORDER BY matchup_min DESC
     LIMIT ?`,
    [playerId, limit],
  );
}

/** League-wide defender leaderboard aggregated over every matchup a player
 *  defended. sort="toughest" ranks by fewest points allowed per 100
 *  partial possessions (with a volume floor so garbage-time specialists
 *  don't top the list); sort="workload" ranks by total matchup minutes. */
export async function getMatchupDefenderLeaders(
  sort: "toughest" | "workload",
  limit = 30,
): Promise<Row[]> {
  const order =
    sort === "toughest" ? "pts_per100 ASC NULLS LAST" : "total_matchup_min DESC NULLS LAST";
  return queryObjects(
    `SELECT
       def_player_id AS player_id,
       any_value(def_player_name) AS defender_name,
       COUNT(*)::INTEGER AS opponents,
       ROUND(SUM(matchup_min), 0)::INTEGER AS total_matchup_min,
       ROUND(SUM(partial_poss), 0)::INTEGER AS total_poss,
       SUM(player_pts)::INTEGER AS pts_allowed,
       CASE WHEN SUM(partial_poss) > 0
         THEN ROUND(SUM(player_pts) / SUM(partial_poss) * 100, 1) END AS pts_per100,
       CASE WHEN SUM(matchup_fga) > 0
         THEN ROUND(SUM(matchup_fgm) / SUM(matchup_fga), 3) END AS fg_pct_allowed,
       SUM(matchup_blk)::INTEGER AS blk,
       SUM(matchup_tov)::INTEGER AS tov_forced
     FROM stg_season_matchups
     GROUP BY def_player_id
     HAVING SUM(partial_poss) >= 750
     ORDER BY ${order}
     LIMIT ?`,
    [limit],
  );
}
