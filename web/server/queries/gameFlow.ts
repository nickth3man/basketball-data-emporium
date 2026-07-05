import { queryObjects } from "../db.ts";
import { DIM_GAME_SEASON_GUARD_SQL } from "./shared.ts";
import type { Row } from "./shared.ts";

// ---------------------------------------------------------------------------
// Game flow + clutch, derived live from fact_pbp_event (18.7M events,
// 1996-97 onward). Era quirk: modern rows carry the running score on every
// event, but legacy rows only populate score_home/score_away on scoring
// plays (0/0 otherwise) — so everything here is built from scoring events
// only, which works identically in both eras. action_number is the reliable
// tie-break sort within (seconds_elapsed), which is cumulative across
// periods. Season/season_type now come from a dim_game join instead of
// parsing the game id.
//
// Clutch scoring is computed here: points credited via the score delta
// between consecutive scoring events (era-agnostic, no reliance on
// action-type taxonomies), filtered to the NBA.com clutch definition —
// last 5 minutes of the 4th period or overtime, margin within 5 before
// the event.
// ---------------------------------------------------------------------------

/** Chronological scoring events for one game: the score after each make,
 *  which is everything a margin timeline needs. */
export async function getGameFlow(gameId: string): Promise<Row[]> {
  return queryObjects(
    `WITH game_meta AS (
       SELECT game_id, season_year FROM dim_game WHERE game_id = ?
     )
     SELECT
       e.period::INTEGER AS period,
       e.seconds_elapsed,
       e.score_home,
       e.score_away,
       COALESCE(era.abbreviation, cur.abbreviation) AS team_tri_code,
       p.full_name AS player_name,
       e.description
     FROM fact_pbp_event e
     JOIN game_meta gm ON gm.game_id = e.game_id
     LEFT JOIN dim_team_era era
       ON era.team_id = e.team_id
       AND CAST(SUBSTR(gm.season_year, 1, 4) AS INTEGER) BETWEEN era.valid_from_year AND era.valid_to_year
     LEFT JOIN dim_team_era cur ON cur.team_id = e.team_id AND cur.is_current
     LEFT JOIN dim_player p ON p.player_id = e.player_id
     WHERE e.game_id = ? AND (e.score_home > 0 OR e.score_away > 0)
     ORDER BY e.seconds_elapsed, e.action_number`,
    [gameId, gameId],
  );
}

export async function listClutchSeasons(): Promise<string[]> {
  const rows = await queryObjects<{ season_year: string }>(
    `SELECT DISTINCT g.season_year
     FROM fact_pbp_event e
     JOIN dim_game g ON g.game_id = e.game_id
     WHERE g.season_type = 'Regular' AND (${DIM_GAME_SEASON_GUARD_SQL})
     ORDER BY season_year DESC`,
  );
  return rows.map((r) => r.season_year);
}

// Per-season memo — the scan is ~0.5s per season, cheap enough to compute
// on demand but not worth repeating for every visitor of the same season.
const clutchCache = new Map<string, Promise<Row[]>>();

export function getClutchLeaders(season: string, limit = 30): Promise<Row[]> {
  let cached = clutchCache.get(season);
  if (!cached) {
    cached = queryClutchLeaders(season);
    clutchCache.set(season, cached);
    // Don't memoize failures (transient DB hiccups shouldn't stick).
    cached.catch(() => clutchCache.delete(season));
  }
  return cached.then((rows) => rows.slice(0, limit));
}

async function queryClutchLeaders(season: string): Promise<Row[]> {
  return queryObjects(
    `WITH season_games AS (
       SELECT game_id FROM dim_game WHERE season_type = 'Regular' AND season_year = ?
     ),
     scoring AS (
       SELECT
         e.game_id, e.period, e.seconds_elapsed, e.player_id,
         e.score_home + e.score_away
           - LAG(e.score_home + e.score_away, 1, 0)
             OVER (PARTITION BY e.game_id ORDER BY e.seconds_elapsed, e.action_number) AS pts,
         LAG(e.score_home, 1, 0)
           OVER (PARTITION BY e.game_id ORDER BY e.seconds_elapsed, e.action_number) AS prev_h,
         LAG(e.score_away, 1, 0)
           OVER (PARTITION BY e.game_id ORDER BY e.seconds_elapsed, e.action_number) AS prev_a
       FROM fact_pbp_event e
       JOIN season_games sg ON sg.game_id = e.game_id
       WHERE e.score_home > 0 OR e.score_away > 0
     ),
     clutch AS (
       SELECT player_id,
              SUM(pts)::INTEGER AS clutch_pts,
              COUNT(DISTINCT game_id)::INTEGER AS games
       FROM scoring
       WHERE pts BETWEEN 1 AND 3
         AND period >= 4
         AND (CASE WHEN period <= 4 THEN 720.0 * period
                   ELSE 2880 + 300.0 * (period - 4) END) - seconds_elapsed <= 300
         AND abs(prev_h - prev_a) <= 5
         AND player_id > 0
       GROUP BY player_id
     )
     SELECT
       c.player_id,
       p.full_name,
       c.clutch_pts,
       c.games,
       ROUND(c.clutch_pts * 1.0 / NULLIF(c.games, 0), 1) AS pts_per_game
     FROM clutch c
     LEFT JOIN dim_player p ON p.player_id = c.player_id
     ORDER BY c.clutch_pts DESC
     LIMIT 100`,
    [season],
  );
}
