import { queryObjects } from "../db.ts";
import type { Row } from "./shared.ts";

// ---------------------------------------------------------------------------
// Officials (league-wide referee leaderboard)
//
// fact_official_assignment currently only covers the 2025-26 season (a live/
// current-season feed, confirmed via live MIN/MAX check during the schema
// migration) — this is a real, permanent coverage limit, not a bug. The
// leaderboard is a flat "most games worked this season" list rather than a
// season picker, since there is only one season of data.
// ---------------------------------------------------------------------------

export async function getOfficialsLeaders(limit = 50): Promise<Row[]> {
  return queryObjects(
    `SELECT
       o.official_id,
       COALESCE(d.first_name || ' ' || d.last_name, o.official_name) AS full_name,
       COUNT(DISTINCT o.game_id) AS games
     FROM fact_official_assignment o
     LEFT JOIN dim_official d ON d.official_id = o.official_id
     GROUP BY o.official_id, COALESCE(d.first_name || ' ' || d.last_name, o.official_name)
     ORDER BY games DESC, full_name
     LIMIT ?`,
    [limit],
  );
}

// ---------------------------------------------------------------------------
// Coaching (league-wide win/tenure leaderboard)
//
// fact_coach_season has one row per (team, season, coach) — a coach who
// changed teams mid-career has multiple rows across different team_ids.
// coach_bbr_slug is the stable per-coach identifier for aggregating across
// team-seasons (coach_name alone can collide between different people).
// ---------------------------------------------------------------------------

export async function getCoachingLeaders(limit = 50): Promise<Row[]> {
  return queryObjects(
    `SELECT
       coach_bbr_slug,
       any_value(coach_name) AS coach_name,
       COUNT(DISTINCT season_year) AS seasons,
       COUNT(DISTINCT team_id) AS teams,
       SUM(wins) AS wins,
       SUM(losses) AS losses,
       SUM(wins) / NULLIF(SUM(wins) + SUM(losses), 0) AS win_pct
     FROM fact_coach_season
     WHERE coach_bbr_slug IS NOT NULL
     GROUP BY coach_bbr_slug
     ORDER BY wins DESC
     LIMIT ?`,
    [limit],
  );
}
