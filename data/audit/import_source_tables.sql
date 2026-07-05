-- Import the adoptable tables from the source database into the local
-- warehouse (data/nba.duckdb), per docs/source-db-adoption.md.
--
-- HISTORICAL / ARCHIVAL RECORD -- NOT part of the routine rebuild pipeline.
-- This script's primary connection is the pre-migration raw source db (it
-- writes v1-era table names like fact_game/fact_player_game_boxscore/
-- dim_bref_player -- the exact names data/audit/build_nba.py's
-- CANONICAL_SOURCE_TABLES expects from a --source-db, not the final
-- warehouse's dim_*/fact_* names). It documents how that raw source was
-- built before it was archived outside this repo; do not run it against
-- the current data/nba.duckdb -- doing so would overwrite the final
-- warehouse with legacy pre-rebuild tables.
--
-- Run from the repo root with the app dev server STOPPED (needs write lock):
--   duckdb data/nba.duckdb -c ".read data/audit/import_source_tables.sql"
--
-- Idempotent: CREATE OR REPLACE throughout. Naming: source names are kept
-- where they don't collide with existing warehouse tables; the stg_bref
-- schema is imported with a stg_bref_ prefix and enriched with warehouse ids
-- via the audit crosswalks (data/audit/out/), which are themselves imported
-- as bridge_player_bbr / bridge_team_bbr.
--
-- Deliberately NOT imported (lossy near-duplicates or already present):
--   - source award facts (fact_player_award_vote/honor(_vote),
--     fact_all_star_selection(s), unified fact_player_awards): their player
--     resolution drops diacritic names (Doncic has 0 rows). Rebuild awards
--     from stg_bref_* + bridge_player_bbr instead.
--   - dim_game/dim_arena/dim_date/dim_coach, officials, line scores,
--     other_stats, inactive players, draft combine, team game logs, odds
--     snapshots: already in the warehouse at equal or better coverage.

ATTACH 'C:/Users/nicolas/Documents/GitHub/basketball-data/duckdb/nba.duckdb' AS src (READ_ONLY);

-- ------------------------------------------------------------ dictionaries

CREATE OR REPLACE TABLE bridge_player_bbr AS
SELECT nba_player_id, bbr_player_id, full_name, method, span_score
FROM read_csv_auto('data/audit/out/player_crosswalk.csv');

CREATE OR REPLACE TABLE bridge_team_bbr AS
SELECT DISTINCT season, team_id, team_abbreviation, bbr_abbreviation, bbr_team_name, lg
FROM read_csv_auto('data/audit/out/team_crosswalk.csv');

CREATE OR REPLACE TABLE bridge_player_source_id AS
SELECT * FROM src.main.bridge_player_source_id;

-- Enrichment helpers: one warehouse id per BBR id/team-season (duplicate
-- warehouse identities — Vaught, O'Bannon, Werdann, Rambis — map both ids to
-- one BBR player in bridge_player_bbr; joins below pick the dominant id by
-- games played so imported rows don't fan out).
CREATE OR REPLACE TEMP TABLE pxw AS
SELECT bbr_player_id, nba_player_id
FROM (
  SELECT b.bbr_player_id, b.nba_player_id,
         row_number() OVER (
           PARTITION BY b.bbr_player_id
           ORDER BY coalesce(g.gp, 0) DESC, b.nba_player_id
         ) AS rn
  FROM bridge_player_bbr b
  LEFT JOIN (SELECT player_id, count(*) AS gp FROM fact_player_game_boxscore GROUP BY 1) g
    ON g.player_id = b.nba_player_id
) WHERE rn = 1;

CREATE OR REPLACE TEMP TABLE txw AS
SELECT season, bbr_abbreviation, min(team_id) AS team_id
FROM bridge_team_bbr GROUP BY 1, 2;

-- ------------------------------------------------- curated/unified imports

CREATE OR REPLACE TABLE fact_game AS
SELECT * FROM src.main.fact_game;

CREATE OR REPLACE TABLE fact_player_season_stat_resolved AS
SELECT * FROM src.main.fact_player_season_stat_resolved;

CREATE OR REPLACE TABLE fact_player_game_boxscore AS
SELECT * FROM src.unified_star.fact_player_game_boxscore;

CREATE OR REPLACE TABLE fact_pbp_events AS
SELECT * FROM src.unified_star.fact_pbp_events;

CREATE OR REPLACE TABLE fact_game_quarter_scores AS
SELECT * FROM src.unified_star.fact_game_quarter_scores;

CREATE OR REPLACE TABLE fact_starting_lineup_player AS
SELECT * FROM src.main.fact_starting_lineup_player;

CREATE OR REPLACE TABLE fact_game_market_odds AS
SELECT * FROM src.main.fact_game_market_odds;

CREATE OR REPLACE TABLE bridge_game_market_odds AS
SELECT * FROM src.main.bridge_game_market_odds;

CREATE OR REPLACE TABLE fact_game_betting_lines AS
SELECT * FROM src.unified_star.fact_game_betting_lines;

CREATE OR REPLACE TABLE fact_game_official AS
SELECT * FROM src.main.fact_game_official;

CREATE OR REPLACE TABLE fact_player_season_stats AS
SELECT * FROM src.unified_star.fact_player_season_stats;

CREATE OR REPLACE TABLE fact_team_season_summary AS
SELECT * FROM src.unified_star.fact_team_season_summary;

CREATE OR REPLACE TABLE fact_season_leader AS
SELECT * FROM src.main.fact_season_leader;

CREATE OR REPLACE TABLE dim_bref_player AS
SELECT * FROM src.main.dim_bref_player;

CREATE OR REPLACE TABLE dim_team_season AS
SELECT * FROM src.main.dim_team_season;

CREATE OR REPLACE TABLE fact_bref_player_season_totals AS
SELECT * FROM src.main.fact_bref_player_season_totals;
CREATE OR REPLACE TABLE fact_bref_player_season_advanced AS
SELECT * FROM src.main.fact_bref_player_season_advanced;
CREATE OR REPLACE TABLE fact_bref_player_season_per_game AS
SELECT * FROM src.main.fact_bref_player_season_per_game;
CREATE OR REPLACE TABLE fact_bref_player_season_per36 AS
SELECT * FROM src.main.fact_bref_player_season_per36;
CREATE OR REPLACE TABLE fact_bref_player_season_per100 AS
SELECT * FROM src.main.fact_bref_player_season_per100;
CREATE OR REPLACE TABLE fact_bref_player_season_shooting AS
SELECT * FROM src.main.fact_bref_player_season_shooting;
CREATE OR REPLACE TABLE fact_bref_player_season_play_by_play AS
SELECT * FROM src.main.fact_bref_player_season_play_by_play;

-- ------------------------------------------- stg_bref (lossless BBR layer)
-- Player-keyed tables gain nba_player_id via the audit crosswalk; team-season
-- tables gain nba_team_id via the team crosswalk. Original columns unchanged.

CREATE OR REPLACE TABLE stg_bref_player_totals AS
SELECT s.*, p.nba_player_id FROM src.stg_bref.player_totals s LEFT JOIN pxw p ON p.bbr_player_id = s.player_id;
CREATE OR REPLACE TABLE stg_bref_advanced AS
SELECT s.*, p.nba_player_id FROM src.stg_bref.advanced s LEFT JOIN pxw p ON p.bbr_player_id = s.player_id;
CREATE OR REPLACE TABLE stg_bref_player_per_game AS
SELECT s.*, p.nba_player_id FROM src.stg_bref.player_per_game s LEFT JOIN pxw p ON p.bbr_player_id = s.player_id;
CREATE OR REPLACE TABLE stg_bref_per_36_minutes AS
SELECT s.*, p.nba_player_id FROM src.stg_bref.per_36_minutes s LEFT JOIN pxw p ON p.bbr_player_id = s.player_id;
CREATE OR REPLACE TABLE stg_bref_per_100_poss AS
SELECT s.*, p.nba_player_id FROM src.stg_bref.per_100_poss s LEFT JOIN pxw p ON p.bbr_player_id = s.player_id;
CREATE OR REPLACE TABLE stg_bref_player_shooting AS
SELECT s.*, p.nba_player_id FROM src.stg_bref.player_shooting s LEFT JOIN pxw p ON p.bbr_player_id = s.player_id;
CREATE OR REPLACE TABLE stg_bref_player_play_by_play AS
SELECT s.*, p.nba_player_id FROM src.stg_bref.player_play_by_play s LEFT JOIN pxw p ON p.bbr_player_id = s.player_id;
CREATE OR REPLACE TABLE stg_bref_player_season_info AS
SELECT s.*, p.nba_player_id FROM src.stg_bref.player_season_info s LEFT JOIN pxw p ON p.bbr_player_id = s.player_id;
CREATE OR REPLACE TABLE stg_bref_player_career_info AS
SELECT s.*, p.nba_player_id FROM src.stg_bref.player_career_info s LEFT JOIN pxw p ON p.bbr_player_id = s.player_id;
CREATE OR REPLACE TABLE stg_bref_player_award_shares AS
SELECT s.*, p.nba_player_id FROM src.stg_bref.player_award_shares s LEFT JOIN pxw p ON p.bbr_player_id = s.player_id;
CREATE OR REPLACE TABLE stg_bref_all_star_selections AS
SELECT s.*, p.nba_player_id FROM src.stg_bref.all_star_selections s LEFT JOIN pxw p ON p.bbr_player_id = s.player_id;
CREATE OR REPLACE TABLE stg_bref_end_of_season_teams AS
SELECT s.*, p.nba_player_id FROM src.stg_bref.end_of_season_teams s LEFT JOIN pxw p ON p.bbr_player_id = s.player_id;
CREATE OR REPLACE TABLE stg_bref_end_of_season_teams_voting AS
SELECT s.*, p.nba_player_id FROM src.stg_bref.end_of_season_teams_voting s LEFT JOIN pxw p ON p.bbr_player_id = s.player_id;
CREATE OR REPLACE TABLE stg_bref_draft_pick_history AS
SELECT s.*, p.nba_player_id FROM src.stg_bref.draft_pick_history s LEFT JOIN pxw p ON p.bbr_player_id = s.player_id;

CREATE OR REPLACE TABLE stg_bref_team_totals AS
SELECT s.*, t.team_id AS nba_team_id FROM src.stg_bref.team_totals s LEFT JOIN txw t ON t.season = s.season AND t.bbr_abbreviation = s.abbreviation;
CREATE OR REPLACE TABLE stg_bref_team_summaries AS
SELECT s.*, t.team_id AS nba_team_id FROM src.stg_bref.team_summaries s LEFT JOIN txw t ON t.season = s.season AND t.bbr_abbreviation = s.abbreviation;
CREATE OR REPLACE TABLE stg_bref_team_stats_per_game AS
SELECT s.*, t.team_id AS nba_team_id FROM src.stg_bref.team_stats_per_game s LEFT JOIN txw t ON t.season = s.season AND t.bbr_abbreviation = s.abbreviation;
CREATE OR REPLACE TABLE stg_bref_team_stats_per_100_poss AS
SELECT s.*, t.team_id AS nba_team_id FROM src.stg_bref.team_stats_per_100_poss s LEFT JOIN txw t ON t.season = s.season AND t.bbr_abbreviation = s.abbreviation;
CREATE OR REPLACE TABLE stg_bref_opponent_totals AS
SELECT s.*, t.team_id AS nba_team_id FROM src.stg_bref.opponent_totals s LEFT JOIN txw t ON t.season = s.season AND t.bbr_abbreviation = s.abbreviation;
CREATE OR REPLACE TABLE stg_bref_opponent_stats_per_game AS
SELECT s.*, t.team_id AS nba_team_id FROM src.stg_bref.opponent_stats_per_game s LEFT JOIN txw t ON t.season = s.season AND t.bbr_abbreviation = s.abbreviation;
CREATE OR REPLACE TABLE stg_bref_opponent_stats_per_100_poss AS
SELECT s.*, t.team_id AS nba_team_id FROM src.stg_bref.opponent_stats_per_100_poss s LEFT JOIN txw t ON t.season = s.season AND t.bbr_abbreviation = s.abbreviation;
CREATE OR REPLACE TABLE stg_bref_team_abbrev AS
SELECT s.*, t.team_id AS nba_team_id FROM src.stg_bref.team_abbrev s LEFT JOIN txw t ON t.season = s.season AND t.bbr_abbreviation = s.abbreviation;

-- --------------------------------------------------------- raw_csv extras

CREATE OR REPLACE TABLE playerstatisticsextended AS
SELECT * FROM src.raw_csv.playerstatisticsextended;

CREATE OR REPLACE TABLE teamstatisticsextended AS
SELECT * FROM src.raw_csv.teamstatisticsextended;

CREATE OR REPLACE TABLE nba_detailed_odds AS
SELECT * FROM src.raw_csv.nba_detailed_odds;
CREATE OR REPLACE TABLE nba_main_lines AS
SELECT * FROM src.raw_csv.nba_main_lines;
CREATE OR REPLACE TABLE nba_preseason_detailed_odds AS
SELECT * FROM src.raw_csv.nba_preseason_detailed_odds;
CREATE OR REPLACE TABLE nba_preseason_main_lines AS
SELECT * FROM src.raw_csv.nba_preseason_main_lines;

CREATE OR REPLACE TABLE leagueschedule24_25 AS
SELECT * FROM src.raw_csv.leagueschedule24_25;
CREATE OR REPLACE TABLE leagueschedule25_26 AS
SELECT * FROM src.raw_csv.leagueschedule25_26;

-- ---------------------------------------------------------------- verify

SELECT 'fact_pbp_events' AS t, count(*) AS n FROM fact_pbp_events
UNION ALL SELECT 'fact_player_game_boxscore', count(*) FROM fact_player_game_boxscore
UNION ALL SELECT 'fact_game', count(*) FROM fact_game
UNION ALL SELECT 'fact_player_season_stat_resolved', count(*) FROM fact_player_season_stat_resolved
UNION ALL SELECT 'stg_bref_player_totals', count(*) FROM stg_bref_player_totals
UNION ALL SELECT 'stg_bref_player_totals matched to nba id', count(*) FROM stg_bref_player_totals WHERE nba_player_id IS NOT NULL
UNION ALL SELECT 'stg_bref_player_award_shares (Doncic rows)', count(*) FROM stg_bref_player_award_shares WHERE nba_player_id = 1629029
UNION ALL SELECT 'fact_game 2023-24 finals games', count(*) FROM fact_game WHERE season_year = '2023-24' AND season_type = 'Playoffs'
UNION ALL SELECT 'bridge_player_bbr', count(*) FROM bridge_player_bbr
UNION ALL SELECT 'bridge_team_bbr', count(*) FROM bridge_team_bbr
ORDER BY t;
