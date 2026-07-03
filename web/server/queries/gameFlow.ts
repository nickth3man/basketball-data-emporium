import { queryObjects } from "../db.ts";
import { SEASON_FROM_GAME_ID_SQL } from "./shared.ts";
import type { Row } from "./shared.ts";

// ---------------------------------------------------------------------------
// Game flow + clutch, derived live from fact_play_by_play_v3 (18.7M events,
// 1996-97 onward). Era quirk: modern rows carry the running score on every
// event, but legacy rows only populate score_home/score_away on scoring
// plays (0/0 otherwise) — so everything here is built from scoring events
// only, which works identically in both eras. order_number is not
// chronological in legacy games; (seconds_elapsed, action_number) is the
// reliable sort. seconds_elapsed is cumulative across periods.
//
// The prebuilt clutch tables (agg_clutch_stats etc.) are empty shells, so
// clutch scoring is computed here: points credited via the score delta
// between consecutive scoring events (era-agnostic, no reliance on
// action-type taxonomies), filtered to the NBA.com clutch definition —
// last 5 minutes of the 4th period or overtime, margin within 5 before
// the event. Sanity-checked against NBA.com's 2024-25 clutch leaders
// (Edwards / Brunson / Young order reproduced).
//
// fact_rotation was evaluated for a stint overlay and rejected: every
// game is missing its opening stints (earliest in_time ≈ 300s) and
// pts_diff is always NULL. Don't build on it without an upstream re-scrape.
// ---------------------------------------------------------------------------

/** Chronological scoring events for one game: the score after each make,
 *  which is everything a margin timeline needs. */
export async function getGameFlow(gameId: string): Promise<Row[]> {
  return queryObjects(
    `SELECT
       period::INTEGER AS period,
       seconds_elapsed,
       score_home,
       score_away,
       team_tri_code,
       player_name,
       description
     FROM fact_play_by_play_v3
     WHERE game_id = ? AND (score_home > 0 OR score_away > 0)
     ORDER BY seconds_elapsed, action_number`,
    [gameId],
  );
}

export async function listClutchSeasons(): Promise<string[]> {
  const rows = await queryObjects<{ season_year: string }>(
    `SELECT DISTINCT ${SEASON_FROM_GAME_ID_SQL} AS season_year
     FROM fact_play_by_play_v3
     WHERE substr(game_id, 1, 3) = '002'
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
    `WITH scoring AS (
       SELECT
         game_id, period, seconds_elapsed, person_id, player_name,
         score_home + score_away
           - LAG(score_home + score_away, 1, 0)
             OVER (PARTITION BY game_id ORDER BY seconds_elapsed, action_number) AS pts,
         LAG(score_home, 1, 0)
           OVER (PARTITION BY game_id ORDER BY seconds_elapsed, action_number) AS prev_h,
         LAG(score_away, 1, 0)
           OVER (PARTITION BY game_id ORDER BY seconds_elapsed, action_number) AS prev_a
       FROM fact_play_by_play_v3
       WHERE substr(game_id, 1, 3) = '002'
         AND ${SEASON_FROM_GAME_ID_SQL} = ?
         AND (score_home > 0 OR score_away > 0)
     ),
     clutch AS (
       SELECT person_id, any_value(player_name) AS pbp_name,
              SUM(pts)::INTEGER AS clutch_pts,
              COUNT(DISTINCT game_id)::INTEGER AS games
       FROM scoring
       WHERE pts BETWEEN 1 AND 3
         AND period >= 4
         AND (CASE WHEN period <= 4 THEN 720.0 * period
                   ELSE 2880 + 300.0 * (period - 4) END) - seconds_elapsed <= 300
         AND abs(prev_h - prev_a) <= 5
         AND person_id > 0
       GROUP BY person_id
     ),
     names AS (
       SELECT player_id, any_value(full_name) AS full_name
       FROM dim_player
       WHERE is_current
       GROUP BY player_id
     )
     SELECT
       c.person_id AS player_id,
       COALESCE(n.full_name, c.pbp_name) AS full_name,
       c.clutch_pts,
       c.games,
       ROUND(c.clutch_pts * 1.0 / NULLIF(c.games, 0), 1) AS pts_per_game
     FROM clutch c
     LEFT JOIN names n ON n.player_id = c.person_id
     ORDER BY c.clutch_pts DESC
     LIMIT 100`,
    [season],
  );
}
