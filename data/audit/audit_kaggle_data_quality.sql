-- Repeatable data-quality checks for the Kaggle-backed warehouse audit.
--
-- Run from the repo root:
--   duckdb -readonly data/nba.duckdb -c ".read data/audit/audit_kaggle_data_quality.sql"
--
-- This script is intentionally read-only for warehouse tables. It reports:
--   - 2025-26 playoff repair coverage after backfill_2026_playoffs_from_kaggle.sql.
--   - Legacy game table gaps now backfillable from stg_kaggle_nba_games.
--   - Luka Doncic award rows recovered by the BBR/crosswalk rebuild.
--   - fact_standings regular-season W/L parity with BBR summaries.
--   - Active agg_player_season abbreviation drift and duplicate-key symptoms.

CREATE OR REPLACE TEMP MACRO season_year_from_date(d) AS
  CASE
    WHEN month(CAST(d AS DATE)) >= 10 THEN
      CAST(year(CAST(d AS DATE)) AS VARCHAR) || '-' ||
      lpad(CAST((year(CAST(d AS DATE)) + 1) % 100 AS VARCHAR), 2, '0')
    ELSE
      CAST(year(CAST(d AS DATE)) - 1 AS VARCHAR) || '-' ||
      lpad(CAST(year(CAST(d AS DATE)) % 100 AS VARCHAR), 2, '0')
  END;

CREATE OR REPLACE TEMP MACRO end_year_to_season(y) AS
  CAST(y - 1 AS VARCHAR) || '-' || lpad(CAST(y % 100 AS VARCHAR), 2, '0');

-- -------------------------------------------------------- 2026 playoff repair

WITH stg AS (
  SELECT game_id
  FROM stg_kaggle_nba_games
  WHERE game_id LIKE '00425%' AND gameType = 'Playoffs'
)
SELECT '2026_playoff_staged_games' AS metric, count(*) AS n FROM stg
UNION ALL SELECT '2026_playoff_dim_game_present', count(*) FROM dim_game WHERE game_id IN (SELECT game_id FROM stg)
UNION ALL SELECT '2026_playoff_fact_game_present', count(*) FROM fact_game WHERE game_id IN (SELECT game_id FROM stg)
UNION ALL SELECT '2026_playoff_dim_bad_placeholders', count(*) FROM dim_game WHERE game_id IN (SELECT game_id FROM stg) AND (season_type <> 'Playoffs' OR home_team_id = 0 OR visitor_team_id = 0)
UNION ALL SELECT '2026_playoff_unresolved_not_in_dim_game', count(*) FROM bridge_game_source_id WHERE source_system='kaggle_nba' AND is_unresolved AND unresolved_reason='not_in_dim_game' AND lpad(source_game_id,10,'0') LIKE '004%'
UNION ALL SELECT '2026_playoff_team_log_games', count(DISTINCT game_id) FROM fact_team_game_log WHERE game_id IN (SELECT game_id FROM stg)
UNION ALL SELECT '2026_playoff_player_log_games', count(DISTINCT game_id) FROM fact_player_game_log WHERE game_id IN (SELECT game_id FROM stg)
UNION ALL SELECT '2026_playoff_player_boxscore_games', count(DISTINCT game_id) FROM fact_player_game_boxscore WHERE game_id IN (SELECT game_id FROM stg)
UNION ALL SELECT '2026_playoff_pbp_games', count(DISTINCT game_id) FROM fact_pbp_events WHERE game_id IN (SELECT game_id FROM stg)
ORDER BY metric;

-- ----------------------------------------------------- legacy game table gaps

WITH stg AS (
  SELECT game_id,
         season_year_from_date(CAST(gameDate AS DATE)) AS season_year,
         gameType AS game_type
  FROM stg_kaggle_nba_games
),
legacy AS (
  SELECT DISTINCT game_id FROM game
)
SELECT season_year, game_type, count(*) AS stg_games, count(legacy.game_id) AS legacy_game_rows,
       count(*) - count(legacy.game_id) AS missing_from_legacy_game
FROM stg
LEFT JOIN legacy USING (game_id)
GROUP BY season_year, game_type
HAVING count(*) - count(legacy.game_id) > 0
ORDER BY season_year, game_type;

-- ---------------------------------------------------------- Doncic awards fix

SELECT 'fact_player_awards Doncic rows' AS metric, count(*) AS n
FROM fact_player_awards
WHERE player_id = 1629029
UNION ALL
SELECT 'fact_player_awards_legacy_names Doncic rows', count(*)
FROM fact_player_awards_legacy_names
WHERE player_id = 1629029
UNION ALL
SELECT 'stg_bref_player_award_shares Doncic rows', count(*)
FROM stg_bref_player_award_shares
WHERE nba_player_id = 1629029
UNION ALL
SELECT 'stg_bref_end_of_season_teams Doncic rows', count(*)
FROM stg_bref_end_of_season_teams
WHERE nba_player_id = 1629029
ORDER BY metric;

-- ------------------------------------------------------- standings W/L parity

WITH bbr AS (
  SELECT end_year_to_season(season) AS season_year,
         nba_team_id AS team_id,
         CAST(w AS BIGINT) AS bbr_wins,
         CAST(l AS BIGINT) AS bbr_losses
  FROM stg_bref_team_summaries
  WHERE nba_team_id IS NOT NULL
),
fs AS (
  SELECT season_year, team_id, wins, losses
  FROM fact_standings
  WHERE season_type = 'Regular'
)
SELECT 'standings_rows_compared' AS metric, count(*) AS n
FROM fs JOIN bbr USING (season_year, team_id)
UNION ALL
SELECT 'standings_wl_mismatches', count(*)
FROM fs JOIN bbr USING (season_year, team_id)
WHERE wins <> bbr_wins OR losses <> bbr_losses
UNION ALL
SELECT 'standings_playin_era_mismatches', count(*)
FROM fs JOIN bbr USING (season_year, team_id)
WHERE season_year >= '2019-20' AND (wins <> bbr_wins OR losses <> bbr_losses)
ORDER BY metric;

-- ---------------------------------------------- agg_player_season diagnostics

WITH era AS (
  SELECT team_id, abbreviation,
         TRY_CAST(substr(valid_from, 1, 4) AS INTEGER) AS start_year,
         COALESCE(TRY_CAST(substr(valid_to, 1, 4) AS INTEGER), 9999) AS end_year
  FROM dim_team_history
),
cur AS (
  SELECT a.*, TRY_CAST(substr(season_year, 1, 4) AS INTEGER) AS season_start
  FROM agg_player_season a
  WHERE season_type = 'Regular'
),
joined AS (
  SELECT cur.*, era.abbreviation AS expected_abbreviation
  FROM cur
  JOIN era
    ON era.team_id = cur.team_id
   AND cur.season_start BETWEEN era.start_year AND era.end_year
)
SELECT 'current_agg_rows_checked_against_team_eras' AS metric, count(*) AS n FROM joined
UNION ALL SELECT 'current_agg_abbreviation_mismatches', count(*) FROM joined WHERE team_abbreviation <> expected_abbreviation
UNION ALL SELECT 'current_agg_same_key_multiple_abbrevs', count(*) FROM (
  SELECT player_id, team_id, season_year, season_type
  FROM agg_player_season
  GROUP BY 1, 2, 3, 4
  HAVING count(DISTINCT team_abbreviation) > 1
)
UNION ALL SELECT 'current_agg_duplicate_exact_keys', count(*) FROM (
  SELECT player_id, team_id, team_abbreviation, season_year, season_type
  FROM agg_player_season
  GROUP BY 1, 2, 3, 4, 5
  HAVING count(*) > 1
)
ORDER BY metric;

WITH era AS (
  SELECT team_id, abbreviation,
         TRY_CAST(substr(valid_from, 1, 4) AS INTEGER) AS start_year,
         COALESCE(TRY_CAST(substr(valid_to, 1, 4) AS INTEGER), 9999) AS end_year
  FROM dim_team_history
),
cur AS (
  SELECT a.*, TRY_CAST(substr(season_year, 1, 4) AS INTEGER) AS season_start
  FROM agg_player_season a
  WHERE season_type = 'Regular'
),
joined AS (
  SELECT cur.team_abbreviation, era.abbreviation AS expected_abbreviation
  FROM cur
  JOIN era
    ON era.team_id = cur.team_id
   AND cur.season_start BETWEEN era.start_year AND era.end_year
  WHERE cur.team_abbreviation <> era.abbreviation
)
SELECT team_abbreviation, expected_abbreviation, count(*) AS row_count
FROM joined
GROUP BY 1, 2
ORDER BY row_count DESC, team_abbreviation
LIMIT 25;
