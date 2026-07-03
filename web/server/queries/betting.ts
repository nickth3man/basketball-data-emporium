import { queryObjects } from "../db.ts";
import type { Row } from "./shared.ts";

// ---------------------------------------------------------------------------
// Vegas vs Reality (moneyline betting explorer)
//
// fact_game_betting_lines has one row per game back to 2003-04 with REAL
// moneylines in decimal_home/decimal_away — but its spread_* columns are
// duplicated moneyline values and `total` is always NULL (verified 2026-07),
// so only moneyline analysis is possible. Results come from the trusted
// `game` fact table, which caps usable coverage at the seasons it carries
// (through 2022-23 as of writing — the betting rows for later seasons have
// no result to join against) and has a few dozen duplicate game_ids, hence
// the any_value() dedup. Implied win probability strips the bookmaker's
// overround by normalizing the two sides' inverse odds to sum to 1.
// ---------------------------------------------------------------------------

const BETTING_JOIN_CTE = `game_dedup AS (
    SELECT
      game_id,
      any_value(game_date) AS game_date,
      any_value(team_id_home) AS team_id_home,
      any_value(team_abbreviation_home) AS home,
      any_value(team_id_away) AS team_id_away,
      any_value(team_abbreviation_away) AS away,
      any_value(pts_home) AS pts_home,
      any_value(pts_away) AS pts_away,
      any_value(wl_home) AS wl_home,
      any_value(season_type) AS season_type
    FROM game
    GROUP BY game_id
  ),
  betting_games AS (
    SELECT
      b.game_id,
      CAST(g.game_date AS DATE)::VARCHAR AS game_date,
      substr(b.game_id, 4, 2) AS yy,
      (CASE WHEN CAST(substr(b.game_id, 4, 2) AS INTEGER) >= 46 THEN '19' ELSE '20' END)
        || substr(b.game_id, 4, 2) || '-' ||
        lpad(CAST((CAST(substr(b.game_id, 4, 2) AS INTEGER) + 1) % 100 AS VARCHAR), 2, '0')
        AS season_year,
      g.season_type,
      g.team_id_home, g.home, g.team_id_away, g.away,
      g.pts_home, g.pts_away, g.wl_home,
      b.decimal_home, b.decimal_away,
      (1 / b.decimal_home) / ((1 / b.decimal_home) + (1 / b.decimal_away)) AS implied_home
    FROM fact_game_betting_lines b
    JOIN game_dedup g USING (game_id)
    WHERE b.decimal_home > 1 AND b.decimal_away > 1 AND g.wl_home IN ('W', 'L')
  )`;

export async function listBettingSeasons(): Promise<string[]> {
  const rows = await queryObjects<{ season_year: string }>(
    `WITH ${BETTING_JOIN_CTE}
     SELECT DISTINCT season_year FROM betting_games ORDER BY season_year DESC`,
  );
  return rows.map((r) => r.season_year);
}

export async function getBettingMarketBeaters(season: string | null): Promise<Row[]> {
  return queryObjects(
    `WITH ${BETTING_JOIN_CTE},
     sides AS (
       SELECT season_year, team_id_home AS team_id, home AS team_abbreviation,
              CASE WHEN wl_home = 'W' THEN 1 ELSE 0 END AS win,
              implied_home AS implied_p
       FROM betting_games
       WHERE season_type = 'Regular Season'
       UNION ALL
       SELECT season_year, team_id_away, away,
              CASE WHEN wl_home = 'W' THEN 0 ELSE 1 END,
              1 - implied_home
       FROM betting_games
       WHERE season_type = 'Regular Season'
     )
     SELECT
       team_id,
       team_abbreviation,
       COUNT(*) AS gp,
       SUM(win) AS wins,
       ROUND(SUM(implied_p), 1) AS expected_wins,
       ROUND(SUM(win) - SUM(implied_p), 1) AS wins_vs_market,
       SUM(CASE WHEN implied_p > 0.5 AND win = 1 THEN 1 ELSE 0 END) AS fav_wins,
       SUM(CASE WHEN implied_p > 0.5 AND win = 0 THEN 1 ELSE 0 END) AS fav_losses,
       SUM(CASE WHEN implied_p < 0.5 AND win = 1 THEN 1 ELSE 0 END) AS dog_wins,
       SUM(CASE WHEN implied_p < 0.5 AND win = 0 THEN 1 ELSE 0 END) AS dog_losses
     FROM sides
     WHERE ? IS NULL OR season_year = ?
     GROUP BY team_id, team_abbreviation
     ORDER BY wins_vs_market DESC`,
    [season, season],
  );
}

export async function getBettingUpsets(season: string | null, limit = 25): Promise<Row[]> {
  return queryObjects(
    `WITH ${BETTING_JOIN_CTE}
     SELECT
       game_id,
       game_date,
       season_year,
       season_type,
       CASE WHEN wl_home = 'W' THEN home ELSE away END AS winner,
       CASE WHEN wl_home = 'W' THEN away ELSE home END AS loser,
       CASE WHEN wl_home = 'W' THEN pts_home ELSE pts_away END AS winner_pts,
       CASE WHEN wl_home = 'W' THEN pts_away ELSE pts_home END AS loser_pts,
       CASE WHEN wl_home = 'W' THEN 'Home' ELSE 'Away' END AS winner_side,
       CASE WHEN wl_home = 'W' THEN decimal_home ELSE decimal_away END AS winner_odds,
       ROUND(CASE WHEN wl_home = 'W' THEN implied_home ELSE 1 - implied_home END * 100, 1)
         AS implied_win_pct
     FROM betting_games
     WHERE ? IS NULL OR season_year = ?
     ORDER BY winner_odds DESC
     LIMIT ?`,
    [season, season, limit],
  );
}

export async function getBettingCalibration(): Promise<Row[]> {
  return queryObjects(
    `WITH ${BETTING_JOIN_CTE}
     SELECT
       season_year,
       COUNT(*) AS games,
       ROUND(AVG(CASE WHEN wl_home = 'W' THEN 1.0 ELSE 0.0 END) * 100, 1) AS home_win_pct,
       ROUND(AVG(implied_home) * 100, 1) AS implied_home_pct,
       ROUND(AVG(CASE
         WHEN (implied_home > 0.5) = (wl_home = 'W') THEN 1.0 ELSE 0.0
       END) * 100, 1) AS favorite_win_pct,
       ROUND(AVG(CASE WHEN implied_home > 0.5 THEN implied_home ELSE 1 - implied_home END) * 100, 1)
         AS favorite_implied_pct
     FROM betting_games
     WHERE season_type = 'Regular Season'
     GROUP BY season_year
     ORDER BY season_year`,
  );
}
