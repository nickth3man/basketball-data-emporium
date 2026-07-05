import { queryObjects } from "../db.ts";
import type { Row } from "./shared.ts";

// ---------------------------------------------------------------------------
// Vegas vs Reality (moneyline betting explorer)
//
// fact_game_odds stores odds in a long (market, selection, odds) shape
// covering many markets; the 'decimal_home'/'decimal_away' markets are the
// direct successor of the old fact_game_betting_lines wide columns (same
// canonical_source, one row per game per side, real decimal moneylines) and
// cover 2003-04 through 2025-26 — a real coverage extension vs. the old
// ~2022-23 cap, since results now come from dim_game instead of the legacy
// `game` table. Implied win probability strips the bookmaker's overround by
// normalizing the two sides' inverse odds to sum to 1.
// ---------------------------------------------------------------------------

const BETTING_JOIN_CTE = `odds_pivot AS (
    SELECT
      game_id,
      MAX(CASE WHEN market = 'decimal_home' THEN odds END) AS decimal_home,
      MAX(CASE WHEN market = 'decimal_away' THEN odds END) AS decimal_away
    FROM fact_game_odds
    WHERE market IN ('decimal_home', 'decimal_away')
    GROUP BY game_id
  ),
  betting_games AS (
    SELECT
      p.game_id,
      g.game_date::VARCHAR AS game_date,
      g.season_year,
      g.season_type,
      g.home_team_id AS team_id_home,
      COALESCE(ht.abbreviation, htc.abbreviation) AS home,
      g.away_team_id AS team_id_away,
      COALESCE(aw.abbreviation, awc.abbreviation) AS away,
      g.home_score AS pts_home,
      g.away_score AS pts_away,
      CASE WHEN g.home_score > g.away_score THEN 'W' ELSE 'L' END AS wl_home,
      p.decimal_home,
      p.decimal_away,
      (1 / p.decimal_home) / ((1 / p.decimal_home) + (1 / p.decimal_away)) AS implied_home
    FROM odds_pivot p
    JOIN dim_game g ON g.game_id = p.game_id
    LEFT JOIN dim_team_era ht
      ON ht.team_id = g.home_team_id
      AND CAST(SUBSTR(g.season_year, 1, 4) AS INTEGER) BETWEEN ht.valid_from_year AND ht.valid_to_year
    LEFT JOIN dim_team_era htc ON htc.team_id = g.home_team_id AND htc.is_current
    LEFT JOIN dim_team_era aw
      ON aw.team_id = g.away_team_id
      AND CAST(SUBSTR(g.season_year, 1, 4) AS INTEGER) BETWEEN aw.valid_from_year AND aw.valid_to_year
    LEFT JOIN dim_team_era awc ON awc.team_id = g.away_team_id AND awc.is_current
    WHERE p.decimal_home > 1 AND p.decimal_away > 1
      AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL
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
       WHERE season_type = 'Regular'
       UNION ALL
       SELECT season_year, team_id_away, away,
              CASE WHEN wl_home = 'W' THEN 0 ELSE 1 END,
              1 - implied_home
       FROM betting_games
       WHERE season_type = 'Regular'
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
     WHERE season_type = 'Regular'
     GROUP BY season_year
     ORDER BY season_year`,
  );
}
