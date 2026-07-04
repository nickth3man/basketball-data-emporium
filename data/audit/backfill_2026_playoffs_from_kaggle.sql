-- Backfill the missing 2026 playoff games from the locally staged Kaggle NBA
-- source tables.
--
-- Context:
--   The 2026-07-04 kaggle_nba ingest staged complete 2026 playoff games through
--   the Finals in stg_kaggle_nba_*. dim_game was missing 17 completed playoff
--   games, and fact_game plus downstream box-score/PBP facts were missing a
--   larger 33-game tail because some dim_game rows were only schedule
--   placeholders.
--
-- Scope:
--   - Refresh dim_game rows for staged 2025-26 playoff game ids (00425%).
--   - Anti-join insert missing rows into fact_game and the game-detail /
--     player/team-game facts that can be derived from staged Kaggle data.
--   - Resolve player and team ids only through bridge_*_source_id for
--     source_system='kaggle_nba'.
--
-- Run from the repo root with the dev server STOPPED:
--   duckdb data/nba.duckdb -c ".read data/audit/backfill_2026_playoffs_from_kaggle.sql"
--
-- Then refresh game bridge resolution and validate bridge invariants:
--   python data/ingest/ingest.py kaggle_nba --resolve-only
--   python data/ingest/validate_bridges.py
--
-- Idempotent. Re-running replaces only dim_game's 00425 playoff rows and uses
-- anti-join inserts for fact tables.

CREATE OR REPLACE MACRO season_year_from_date(d) AS
  CASE
    WHEN month(CAST(d AS DATE)) >= 10 THEN
      CAST(year(CAST(d AS DATE)) AS VARCHAR) || '-' ||
      lpad(CAST((year(CAST(d AS DATE)) + 1) % 100 AS VARCHAR), 2, '0')
    ELSE
      CAST(year(CAST(d AS DATE)) - 1 AS VARCHAR) || '-' ||
      lpad(CAST(year(CAST(d AS DATE)) % 100 AS VARCHAR), 2, '0')
  END;

CREATE OR REPLACE MACRO clock_seconds_remaining(clock_text) AS
  TRY_CAST(regexp_extract(clock_text, 'PT([0-9]+)M', 1) AS DOUBLE) * 60
  + TRY_CAST(regexp_extract(clock_text, 'M([0-9]+(?:\\.[0-9]+)?)S', 1) AS DOUBLE);

CREATE OR REPLACE MACRO pbp_seconds_elapsed(period_num, clock_text) AS
  CASE
    WHEN TRY_CAST(period_num AS INTEGER) BETWEEN 1 AND 4 THEN
      (TRY_CAST(period_num AS INTEGER) - 1) * 720
      + 720 - clock_seconds_remaining(clock_text)
    WHEN TRY_CAST(period_num AS INTEGER) >= 5 THEN
      4 * 720 + (TRY_CAST(period_num AS INTEGER) - 5) * 300
      + 300 - clock_seconds_remaining(clock_text)
    ELSE NULL
  END;

CREATE OR REPLACE TEMP TABLE k_team_xw AS
SELECT source_team_id, team_id
FROM (
  SELECT source_team_id, team_id,
         row_number() OVER (
           PARTITION BY source_team_id
           ORDER BY match_confidence DESC NULLS LAST, team_id
         ) AS rn
  FROM bridge_team_source_id
  WHERE source_system = 'kaggle_nba'
    AND NOT coalesce(is_unresolved, false)
    AND team_id IS NOT NULL
)
WHERE rn = 1;

CREATE OR REPLACE TEMP TABLE k_player_xw AS
SELECT source_player_id, person_id AS player_id
FROM (
  SELECT source_player_id, person_id,
         row_number() OVER (
           PARTITION BY source_player_id
           ORDER BY match_confidence DESC NULLS LAST, person_id
         ) AS rn
  FROM bridge_player_source_id
  WHERE source_system = 'kaggle_nba'
    AND NOT coalesce(is_unresolved, false)
    AND person_id IS NOT NULL
)
WHERE rn = 1;

CREATE OR REPLACE TEMP TABLE k_team_dim AS
SELECT team_id, abbreviation, city, nickname AS team_name
FROM (
  SELECT team_id, abbreviation, city, nickname, is_current,
         row_number() OVER (
           PARTITION BY team_id
           ORDER BY CASE WHEN is_current THEN 0 ELSE 1 END,
                    TRY_CAST(substr(valid_from, 1, 4) AS INTEGER) DESC NULLS LAST,
                    abbreviation
         ) AS rn
  FROM dim_team_history
)
WHERE rn = 1;

CREATE OR REPLACE TEMP TABLE k_games AS
SELECT coalesce(bg.game_id, g.game_id) AS game_id,
       CAST(g.gameDate AS DATE) AS game_date,
       g.gameDateTimeEst AS game_datetime_est,
       season_year_from_date(CAST(g.gameDate AS DATE)) AS season_year,
       'Playoffs' AS season_type,
       g.gameType AS game_type,
       g.gameSubtype AS game_subtype,
       g.gameLabel AS game_label,
       g.gameSubLabel AS game_sub_label,
       TRY_CAST(regexp_extract(g.seriesGameNumber, '([0-9]+)', 1) AS INTEGER) AS series_game_number,
       home_xw.team_id AS home_team_id,
       away_xw.team_id AS away_team_id,
       CAST(g.homeScore AS INTEGER) AS home_score,
       CAST(g.awayScore AS INTEGER) AS away_score,
       winner_xw.team_id AS winner_team_id,
       g.arenaId AS arena_id,
       NULLIF(g.arenaName, '') AS arena_name,
       NULLIF(g.arenaCity, '') AS arena_city,
       NULLIF(g.arenaState, '') AS arena_state,
       CAST(g.attendance AS INTEGER) AS attendance,
       coalesce(q.has_ot, false) AS is_overtime,
       home_dim.abbreviation AS home_abbreviation,
       away_dim.abbreviation AS away_abbreviation,
       home_dim.city AS home_city,
       away_dim.city AS away_city,
       home_dim.team_name AS home_team_name,
       away_dim.team_name AS away_team_name
FROM stg_kaggle_nba_games g
JOIN bridge_game_source_id bg
  ON bg.source_system = 'kaggle_nba'
 AND bg.source_game_id = CAST(g.gameId AS VARCHAR)
JOIN k_team_xw home_xw ON home_xw.source_team_id = CAST(g.hometeamId AS VARCHAR)
JOIN k_team_xw away_xw ON away_xw.source_team_id = CAST(g.awayteamId AS VARCHAR)
LEFT JOIN k_team_xw winner_xw ON winner_xw.source_team_id = CAST(g.winner AS VARCHAR)
LEFT JOIN k_team_dim home_dim ON home_dim.team_id = home_xw.team_id
LEFT JOIN k_team_dim away_dim ON away_dim.team_id = away_xw.team_id
LEFT JOIN (
  SELECT game_id, bool_or(coalesce(ot1Points, 0) <> 0 OR coalesce(ot2Points, 0) <> 0 OR coalesce(otAllPoints, 0) <> 0) AS has_ot
  FROM stg_kaggle_nba_team_statistics
  GROUP BY game_id
) q ON q.game_id = g.game_id
WHERE g.game_id LIKE '00425%'
  AND g.gameType = 'Playoffs';

CREATE TABLE IF NOT EXISTS dim_game_2026_playoff_pre_kaggle_backfill AS
SELECT *
FROM dim_game
WHERE game_id IN (SELECT game_id FROM k_games);

CREATE TABLE IF NOT EXISTS fact_game_2026_playoff_pre_kaggle_backfill AS
SELECT *
FROM fact_game
WHERE game_id IN (SELECT game_id FROM k_games);

-- dim_game had a mix of complete rows, Regular-season placeholders, zero-team
-- Finals placeholders, and missing rows. Replace this narrow slice from the
-- completed staged games so game bridge resolution has one canonical target.
DELETE FROM dim_game
WHERE game_id IN (SELECT game_id FROM k_games);

INSERT INTO dim_game (
  game_id, game_date, season_year, season_type, home_team_id, visitor_team_id,
  matchup, arena_name, arena_city
)
SELECT game_id,
       CAST(game_date AS VARCHAR) AS game_date,
       season_year,
       season_type,
       home_team_id,
       away_team_id AS visitor_team_id,
       home_abbreviation || ' vs ' || away_abbreviation AS matchup,
       arena_name,
       arena_city
FROM k_games
ORDER BY game_id;

INSERT INTO fact_game (
  game_id, game_date, game_datetime_est, season_year, season_type, game_type,
  game_subtype, game_label, game_sub_label, series_game_number, home_team_id,
  away_team_id, home_score, away_score, winner_team_id, arena_id, arena_name,
  arena_city, arena_state, attendance, is_overtime, odds_home, odds_away
)
SELECT g.game_id, g.game_date, g.game_datetime_est, g.season_year, g.season_type,
       g.game_type, g.game_subtype, g.game_label, g.game_sub_label,
       g.series_game_number, g.home_team_id, g.away_team_id, g.home_score,
       g.away_score, g.winner_team_id, g.arena_id, g.arena_name, g.arena_city,
       g.arena_state, g.attendance, g.is_overtime,
       CAST(NULL AS DOUBLE) AS odds_home,
       CAST(NULL AS DOUBLE) AS odds_away
FROM k_games g
ANTI JOIN fact_game f USING (game_id)
ORDER BY g.game_id;

CREATE OR REPLACE TEMP TABLE k_team_stats AS
SELECT s.game_id,
       kg.game_date,
       kg.season_year,
       kg.season_type,
       tx.team_id,
       ox.team_id AS opponent_team_id,
       td.abbreviation AS team_abbreviation,
       od.abbreviation AS opponent_abbreviation,
       td.city AS team_city,
       td.team_name,
       CAST(s.home AS INTEGER) = 1 AS is_home,
       CAST(s.win AS INTEGER) = 1 AS is_win,
       CAST(s.teamScore AS DOUBLE) AS pts,
       CAST(s.opponentScore AS DOUBLE) AS opponent_pts,
       CAST(s.assists AS DOUBLE) AS ast,
       CAST(s.blocks AS DOUBLE) AS blk,
       CAST(s.steals AS DOUBLE) AS stl,
       CAST(s.fieldGoalsAttempted AS DOUBLE) AS fga,
       CAST(s.fieldGoalsMade AS DOUBLE) AS fgm,
       CAST(s.fieldGoalsPercentage AS DOUBLE) AS fg_pct,
       CAST(s.threePointersAttempted AS DOUBLE) AS fg3a,
       CAST(s.threePointersMade AS DOUBLE) AS fg3m,
       CAST(s.threePointersPercentage AS DOUBLE) AS fg3_pct,
       CAST(s.freeThrowsAttempted AS DOUBLE) AS fta,
       CAST(s.freeThrowsMade AS DOUBLE) AS ftm,
       CAST(s.freeThrowsPercentage AS DOUBLE) AS ft_pct,
       CAST(s.reboundsDefensive AS DOUBLE) AS dreb,
       CAST(s.reboundsOffensive AS DOUBLE) AS oreb,
       CAST(s.reboundsTotal AS DOUBLE) AS reb,
       CAST(s.foulsPersonal AS DOUBLE) AS pf,
       CAST(s.turnovers AS DOUBLE) AS tov,
       CAST(s.plusMinusPoints AS DOUBLE) AS plus_minus,
       CAST(s.numMinutes AS DOUBLE) AS min,
       CAST(s.seasonWins AS BIGINT) AS w,
       CAST(s.seasonLosses AS BIGINT) AS l,
       CAST(s.q1Points AS INTEGER) AS q1_pts,
       CAST(s.q2Points AS INTEGER) AS q2_pts,
       CAST(s.q3Points AS INTEGER) AS q3_pts,
       CAST(s.q4Points AS INTEGER) AS q4_pts,
       CAST(s.ot1Points AS INTEGER) AS ot1_pts,
       CAST(s.ot2Points AS INTEGER) AS ot2_pts,
       CAST(s.otAllPoints AS INTEGER) AS ot_all_pts,
       CAST(s.benchPoints AS BIGINT) AS bench_points,
       CAST(s.biggestLead AS BIGINT) AS biggest_lead,
       CAST(s.biggestScoringRun AS BIGINT) AS biggest_scoring_run,
       CAST(s.leadChanges AS BIGINT) AS lead_changes,
       CAST(s.pointsFastBreak AS BIGINT) AS points_fast_break,
       CAST(s.pointsFromTurnovers AS BIGINT) AS points_from_turnovers,
       CAST(s.pointsInThePaint AS BIGINT) AS points_in_paint,
       CAST(s.pointsSecondChance AS BIGINT) AS points_second_chance,
       CAST(s.timesTied AS BIGINT) AS times_tied
FROM stg_kaggle_nba_team_statistics s
JOIN k_games kg ON kg.game_id = s.game_id
JOIN k_team_xw tx ON tx.source_team_id = CAST(s.teamId AS VARCHAR)
JOIN k_team_xw ox ON ox.source_team_id = CAST(s.opponentTeamId AS VARCHAR)
LEFT JOIN k_team_dim td ON td.team_id = tx.team_id
LEFT JOIN k_team_dim od ON od.team_id = ox.team_id;

INSERT INTO fact_team_game_log (
  season_id, team_id, team_abbreviation, team_name, game_id, game_date, matchup,
  wl, w, l, w_pct, min, fgm, fga, fg_pct, fg3m, fg3a, fg3_pct, ftm, fta,
  ft_pct, oreb, dreb, reb, ast, stl, blk, tov, pf, pts, plus_minus,
  video_available
)
SELECT season_year AS season_id,
       team_id,
       team_abbreviation,
       team_name,
       game_id,
       CAST(game_date AS VARCHAR) AS game_date,
       team_abbreviation || CASE WHEN is_home THEN ' vs. ' ELSE ' @ ' END || opponent_abbreviation AS matchup,
       CASE WHEN is_win THEN 'W' ELSE 'L' END AS wl,
       w,
       l,
       w::DOUBLE / NULLIF(w + l, 0) AS w_pct,
       min, fgm, fga, fg_pct, fg3m, fg3a, fg3_pct, ftm, fta, ft_pct,
       oreb, dreb, reb, ast, stl, blk, tov, pf, pts, plus_minus,
       CAST(NULL AS BIGINT) AS video_available
FROM k_team_stats s
ANTI JOIN fact_team_game_log t USING (game_id, team_id)
ORDER BY game_id, team_id;

INSERT INTO fact_box_score_team (
  game_id, team_id, team_name, team_abbreviation, team_city, team_slug, min,
  fgm, fga, fg_pct, fg3m, fg3a, fg3_pct, ftm, fta, ft_pct, oreb, dreb, reb,
  ast, stl, blk, tov, pf, pts, plus_minus
)
SELECT game_id, team_id, team_name, team_abbreviation, team_city,
       CAST(NULL AS VARCHAR) AS team_slug,
       min, fgm, fga, fg_pct, fg3m, fg3a, fg3_pct, ftm, fta, ft_pct,
       oreb, dreb, reb, ast, stl, blk, tov, pf, pts, plus_minus
FROM k_team_stats s
ANTI JOIN fact_box_score_team t USING (game_id, team_id)
ORDER BY game_id, team_id;

INSERT INTO fact_game_context (
  game_id, game_date, attendance, game_time, game_status_text, home_team_id,
  visitor_team_id, team_id, team_abbreviation, team_city, team_name, player_id,
  first_name, last_name, jersey_num, pts_paint, pts_2nd_chance, pts_fb,
  pts_off_to, largest_lead, lead_changes, times_tied, last_game_id,
  series_leader, video_available_flag, pt_available, pt_xyz_available,
  wh_status, hustle_status, historical_status, context_source
)
SELECT s.game_id,
       CAST(s.game_date AS VARCHAR) AS game_date,
       kg.attendance,
       CAST(NULL AS VARCHAR) AS game_time,
       'Final' AS game_status_text,
       kg.home_team_id,
       kg.away_team_id AS visitor_team_id,
       s.team_id,
       s.team_abbreviation,
       s.team_city,
       s.team_name,
       CAST(NULL AS BIGINT) AS player_id,
       CAST(NULL AS VARCHAR) AS first_name,
       CAST(NULL AS VARCHAR) AS last_name,
       CAST(NULL AS VARCHAR) AS jersey_num,
       s.points_in_paint AS pts_paint,
       s.points_second_chance AS pts_2nd_chance,
       s.points_fast_break AS pts_fb,
       s.points_from_turnovers AS pts_off_to,
       s.biggest_lead AS largest_lead,
       s.lead_changes,
       s.times_tied,
       CAST(NULL AS VARCHAR) AS last_game_id,
       CAST(NULL AS VARCHAR) AS series_leader,
       CAST(NULL AS BIGINT) AS video_available_flag,
       CAST(NULL AS BIGINT) AS pt_available,
       CAST(NULL AS BIGINT) AS pt_xyz_available,
       CAST(NULL AS BIGINT) AS wh_status,
       CAST(NULL AS BIGINT) AS hustle_status,
       CAST(NULL AS BIGINT) AS historical_status,
       'stg_kaggle_nba_team_statistics' AS context_source
FROM k_team_stats s
JOIN k_games kg USING (game_id)
ANTI JOIN fact_game_context c USING (game_id, team_id)
ORDER BY s.game_id, s.team_id;

CREATE OR REPLACE TEMP TABLE k_player_ext AS
SELECT s.game_id,
       kg.game_date,
       kg.season_year,
       kg.season_type,
       px.player_id,
       trim(s.firstName || ' ' || s.lastName) AS player_name,
       tx.team_id,
       ox.team_id AS opponent_team_id,
       td.abbreviation AS team_abbreviation,
       od.abbreviation AS opponent_abbreviation,
       td.team_name,
       CAST(s.home AS INTEGER) = 1 AS is_home,
       CAST(s.win AS INTEGER) = 1 AS is_win,
       NULLIF(s.comment, '') AS comment,
       NULLIF(s.startingPosition, '') AS starting_position,
       TRY_CAST(s.numMinutes AS DOUBLE) AS min,
       CAST(s.points AS DOUBLE) AS pts,
       CAST(s.assists AS DOUBLE) AS ast,
       CAST(s.reboundsTotal AS DOUBLE) AS reb,
       CAST(s.reboundsOffensive AS DOUBLE) AS oreb,
       CAST(s.reboundsDefensive AS DOUBLE) AS dreb,
       CAST(s.fieldGoalsMade AS DOUBLE) AS fgm,
       CAST(s.fieldGoalsAttempted AS DOUBLE) AS fga,
       CAST(s.fieldGoalsPercentage AS DOUBLE) AS fg_pct,
       CAST(s.threePointersMade AS DOUBLE) AS fg3m,
       CAST(s.threePointersAttempted AS DOUBLE) AS fg3a,
       CAST(s.threePointersPercentage AS DOUBLE) AS fg3_pct,
       CAST(s.freeThrowsMade AS DOUBLE) AS ftm,
       CAST(s.freeThrowsAttempted AS DOUBLE) AS fta,
       CAST(s.freeThrowsPercentage AS DOUBLE) AS ft_pct,
       CAST(s.steals AS DOUBLE) AS stl,
       CAST(s.blocks AS DOUBLE) AS blk,
       CAST(s.blocksAgainst AS DOUBLE) AS blka,
       CAST(s.turnovers AS DOUBLE) AS tov,
       CAST(s.foulsPersonal AS DOUBLE) AS pf,
       CAST(s.foulsAgainst AS DOUBLE) AS pfd,
       CAST(s.plusMinusPoints AS DOUBLE) AS plus_minus,
       CAST(s.doubleDouble AS BIGINT) AS dd2,
       CAST(s.tripleDouble AS BIGINT) AS td3,
       CAST(s.offensiveRating AS DOUBLE) AS off_rating,
       CAST(s.defensiveRating AS DOUBLE) AS def_rating,
       CAST(s.netRating AS DOUBLE) AS net_rating,
       CAST(s.assistPercentage AS DOUBLE) AS ast_pct,
       CAST(s.assistToTurnoverRatio AS DOUBLE) AS ast_to_turnover_ratio,
       CAST(s.assistRatio AS DOUBLE) AS ast_ratio,
       CAST(s.offensiveReboundPercentage AS DOUBLE) AS oreb_pct,
       CAST(s.defensiveReboundPercentage AS DOUBLE) AS dreb_pct,
       CAST(s.reboundPercentage AS DOUBLE) AS reb_pct,
       CAST(s.teamTurnoverPercentage AS DOUBLE) AS tov_pct,
       CAST(s.effectiveFieldGoalPercentage AS DOUBLE) AS efg_pct,
       CAST(s.trueShootingPercentage AS DOUBLE) AS ts_pct,
       CAST(s.usagePercentage AS DOUBLE) AS usg_pct,
       CAST(s.pace AS DOUBLE) AS pace,
       CAST(s.playerImpactEstimate AS DOUBLE) AS pie,
       CAST(s.possessions AS BIGINT) AS poss
FROM stg_kaggle_nba_player_statistics_extended s
JOIN k_games kg ON kg.game_id = s.game_id
JOIN k_player_xw px ON px.source_player_id = CAST(s.personId AS VARCHAR)
JOIN k_team_xw tx ON tx.source_team_id = CAST(s.playerteamId AS VARCHAR)
JOIN k_team_xw ox ON ox.source_team_id = CAST(s.opponentteamId AS VARCHAR)
LEFT JOIN k_team_dim td ON td.team_id = tx.team_id
LEFT JOIN k_team_dim od ON od.team_id = ox.team_id;

INSERT INTO fact_player_game_log (
  season_id, season_year, player_id, player_name, team_id, team_abbreviation,
  team_name, game_id, game_date, matchup, wl, min, fgm, fga, fg_pct, fg3m,
  fg3a, fg3_pct, ftm, fta, ft_pct, oreb, dreb, reb, ast, tov, stl, blk, blka,
  pf, pfd, pts, plus_minus, nba_fantasy_pts, dd2, td3, gp_rank, w_rank,
  l_rank, w_pct_rank, min_rank, fgm_rank, fga_rank, fg_pct_rank, fg3m_rank,
  fg3a_rank, fg3_pct_rank, ftm_rank, fta_rank, ft_pct_rank, oreb_rank,
  dreb_rank, reb_rank, ast_rank, tov_rank, stl_rank, blk_rank, blka_rank,
  pf_rank, pfd_rank, pts_rank, plus_minus_rank, nba_fantasy_pts_rank,
  dd2_rank, td3_rank, video_available, season_type
)
SELECT season_year AS season_id,
       season_year,
       player_id,
       player_name,
       team_id,
       team_abbreviation,
       team_name,
       game_id,
       CAST(game_date AS VARCHAR) AS game_date,
       team_abbreviation || CASE WHEN is_home THEN ' vs. ' ELSE ' @ ' END || opponent_abbreviation AS matchup,
       CASE WHEN is_win THEN '1' ELSE '0' END AS wl,
       min, fgm, fga, fg_pct, fg3m, fg3a, fg3_pct, ftm, fta, ft_pct,
       oreb, dreb, reb, ast, tov, stl, blk, blka, pf, pfd, pts, plus_minus,
       CASE
         WHEN pts IS NULL THEN NULL
         ELSE pts + 1.2 * coalesce(reb, 0) + 1.5 * coalesce(ast, 0)
              + 3 * coalesce(stl, 0) + 3 * coalesce(blk, 0) - coalesce(tov, 0)
       END AS nba_fantasy_pts,
       dd2, td3,
       CAST(NULL AS BIGINT) AS gp_rank,
       CAST(NULL AS BIGINT) AS w_rank,
       CAST(NULL AS BIGINT) AS l_rank,
       CAST(NULL AS BIGINT) AS w_pct_rank,
       CAST(NULL AS BIGINT) AS min_rank,
       CAST(NULL AS BIGINT) AS fgm_rank,
       CAST(NULL AS BIGINT) AS fga_rank,
       CAST(NULL AS BIGINT) AS fg_pct_rank,
       CAST(NULL AS BIGINT) AS fg3m_rank,
       CAST(NULL AS BIGINT) AS fg3a_rank,
       CAST(NULL AS BIGINT) AS fg3_pct_rank,
       CAST(NULL AS BIGINT) AS ftm_rank,
       CAST(NULL AS BIGINT) AS fta_rank,
       CAST(NULL AS BIGINT) AS ft_pct_rank,
       CAST(NULL AS BIGINT) AS oreb_rank,
       CAST(NULL AS BIGINT) AS dreb_rank,
       CAST(NULL AS BIGINT) AS reb_rank,
       CAST(NULL AS BIGINT) AS ast_rank,
       CAST(NULL AS BIGINT) AS tov_rank,
       CAST(NULL AS BIGINT) AS stl_rank,
       CAST(NULL AS BIGINT) AS blk_rank,
       CAST(NULL AS BIGINT) AS blka_rank,
       CAST(NULL AS BIGINT) AS pf_rank,
       CAST(NULL AS BIGINT) AS pfd_rank,
       CAST(NULL AS BIGINT) AS pts_rank,
       CAST(NULL AS BIGINT) AS plus_minus_rank,
       CAST(NULL AS BIGINT) AS nba_fantasy_pts_rank,
       CAST(NULL AS BIGINT) AS dd2_rank,
       CAST(NULL AS BIGINT) AS td3_rank,
       CAST(NULL AS BIGINT) AS video_available,
       season_type
FROM k_player_ext p
ANTI JOIN fact_player_game_log l USING (game_id, player_id, team_id)
ORDER BY game_id, team_id, player_id;

INSERT INTO fact_player_game_boxscore (
  game_id, player_id, team_id, opponent_team_id, is_home, is_win,
  starting_position, comment, min, points, assists, blocks, steals, turnovers,
  fga, fgm, fg_pct, fg3a, fg3m, fg3_pct, fta, ftm, ft_pct, oreb, dreb, reb,
  fouls_personal, plus_minus, off_rating, def_rating, net_rating, ast_pct,
  ast_to_turnover_ratio, ast_ratio, oreb_pct, dreb_pct, reb_pct, tov_pct,
  efg_pct, ts_pct, usg_pct, pace, pie
)
SELECT game_id, player_id, team_id, opponent_team_id, is_home, is_win,
       starting_position, comment, min,
       CAST(pts AS INTEGER) AS points,
       CAST(ast AS INTEGER) AS assists,
       CAST(blk AS INTEGER) AS blocks,
       CAST(stl AS INTEGER) AS steals,
       CAST(tov AS INTEGER) AS turnovers,
       CAST(fga AS INTEGER) AS fga,
       CAST(fgm AS INTEGER) AS fgm,
       fg_pct,
       CAST(fg3a AS INTEGER) AS fg3a,
       CAST(fg3m AS INTEGER) AS fg3m,
       fg3_pct,
       CAST(fta AS INTEGER) AS fta,
       CAST(ftm AS INTEGER) AS ftm,
       ft_pct,
       CAST(oreb AS INTEGER) AS oreb,
       CAST(dreb AS INTEGER) AS dreb,
       CAST(reb AS INTEGER) AS reb,
       CAST(pf AS INTEGER) AS fouls_personal,
       CAST(plus_minus AS INTEGER) AS plus_minus,
       off_rating, def_rating, net_rating, ast_pct, ast_to_turnover_ratio,
       ast_ratio, oreb_pct, dreb_pct, reb_pct, tov_pct, efg_pct, ts_pct,
       usg_pct, pace, pie
FROM k_player_ext p
ANTI JOIN fact_player_game_boxscore b USING (game_id, player_id, team_id)
ORDER BY game_id, team_id, player_id;

INSERT INTO fact_player_game_advanced (
  game_id, player_id, team_id, off_rating, def_rating, net_rating, ast_pct,
  ast_to, ast_ratio, oreb_pct, dreb_pct, reb_pct, efg_pct, ts_pct, usg_pct,
  pace, pie, poss, fta_rate, season_year
)
SELECT game_id, player_id, team_id, off_rating, def_rating, net_rating,
       ast_pct, ast_to_turnover_ratio AS ast_to, ast_ratio, oreb_pct, dreb_pct,
       reb_pct, efg_pct, ts_pct, usg_pct, pace, pie, poss,
       fta / NULLIF(fga, 0) AS fta_rate,
       season_year
FROM k_player_ext p
ANTI JOIN fact_player_game_advanced a USING (game_id, player_id, team_id)
ORDER BY game_id, team_id, player_id;

INSERT INTO fact_starting_lineup_player (
  game_id, team_id, person_id, starting_position
)
SELECT game_id, team_id, player_id AS person_id, starting_position
FROM k_player_ext p
ANTI JOIN fact_starting_lineup_player s
  ON s.game_id = p.game_id
 AND s.team_id = p.team_id
 AND s.person_id = p.player_id
WHERE starting_position IS NOT NULL
ORDER BY game_id, team_id, starting_position;

INSERT INTO fact_game_leaders (
  game_id, team_id, leader_type, person_id, name, player_slug, jersey_num,
  position, team_tricode, points, rebounds, assists
)
WITH ranked AS (
  SELECT game_id, team_id, team_abbreviation, player_id, player_name, pts, reb, ast,
         rank() OVER (PARTITION BY game_id, team_id ORDER BY pts DESC NULLS LAST, player_id) AS pts_rank,
         rank() OVER (PARTITION BY game_id, team_id ORDER BY reb DESC NULLS LAST, player_id) AS reb_rank,
         rank() OVER (PARTITION BY game_id, team_id ORDER BY ast DESC NULLS LAST, player_id) AS ast_rank
  FROM k_player_ext
  WHERE pts IS NOT NULL OR reb IS NOT NULL OR ast IS NOT NULL
),
leaders AS (
  SELECT game_id, team_id, 'pts_leader' AS leader_type, player_id AS person_id,
         player_name AS name, team_abbreviation AS team_tricode,
         pts AS points, CAST(NULL AS DOUBLE) AS rebounds, CAST(NULL AS DOUBLE) AS assists
  FROM ranked WHERE pts_rank = 1 AND pts IS NOT NULL
  UNION ALL
  SELECT game_id, team_id, 'reb_leader' AS leader_type, player_id AS person_id,
         player_name AS name, team_abbreviation AS team_tricode,
         CAST(NULL AS DOUBLE) AS points, reb AS rebounds, CAST(NULL AS DOUBLE) AS assists
  FROM ranked WHERE reb_rank = 1 AND reb IS NOT NULL
  UNION ALL
  SELECT game_id, team_id, 'ast_leader' AS leader_type, player_id AS person_id,
         player_name AS name, team_abbreviation AS team_tricode,
         CAST(NULL AS DOUBLE) AS points, CAST(NULL AS DOUBLE) AS rebounds, ast AS assists
  FROM ranked WHERE ast_rank = 1 AND ast IS NOT NULL
)
SELECT game_id, team_id, leader_type, person_id, name,
       CAST(NULL AS VARCHAR) AS player_slug,
       CAST(NULL AS VARCHAR) AS jersey_num,
       CAST(NULL AS VARCHAR) AS position,
       team_tricode, points, rebounds, assists
FROM leaders l
ANTI JOIN fact_game_leaders f USING (game_id, team_id, leader_type, person_id)
ORDER BY game_id, team_id, leader_type, person_id;

INSERT INTO fact_game_quarter_scores (
  game_id, team_id, period, pts, fgm, fga, fg3m, fg3a, ftm, fta, reb, ast,
  stl, tov, plus_minus
)
WITH period_rows AS (
  SELECT game_id, team_id, opponent_team_id, 1 AS period, q1_pts AS pts FROM k_team_stats
  UNION ALL SELECT game_id, team_id, opponent_team_id, 2, q2_pts FROM k_team_stats
  UNION ALL SELECT game_id, team_id, opponent_team_id, 3, q3_pts FROM k_team_stats
  UNION ALL SELECT game_id, team_id, opponent_team_id, 4, q4_pts FROM k_team_stats
  UNION ALL SELECT game_id, team_id, opponent_team_id, 5, ot1_pts FROM k_team_stats
  UNION ALL SELECT game_id, team_id, opponent_team_id, 6, ot2_pts FROM k_team_stats
),
with_margin AS (
  SELECT p.game_id, p.team_id, p.period, p.pts,
         p.pts - o.pts AS plus_minus
  FROM period_rows p
  LEFT JOIN period_rows o
    ON o.game_id = p.game_id
   AND o.team_id = p.opponent_team_id
   AND o.period = p.period
  WHERE p.pts IS NOT NULL
)
SELECT game_id, team_id, CAST(period AS SMALLINT) AS period, pts,
       CAST(NULL AS INTEGER) AS fgm,
       CAST(NULL AS INTEGER) AS fga,
       CAST(NULL AS INTEGER) AS fg3m,
       CAST(NULL AS INTEGER) AS fg3a,
       CAST(NULL AS INTEGER) AS ftm,
       CAST(NULL AS INTEGER) AS fta,
       CAST(NULL AS INTEGER) AS reb,
       CAST(NULL AS INTEGER) AS ast,
       CAST(NULL AS INTEGER) AS stl,
       CAST(NULL AS INTEGER) AS tov,
       CAST(plus_minus AS INTEGER) AS plus_minus
FROM with_margin q
ANTI JOIN fact_game_quarter_scores f USING (game_id, team_id, period)
ORDER BY game_id, period, team_id;

INSERT INTO fact_pbp_events (
  game_id, action_number, period, clock, seconds_elapsed, team_id, player_id,
  action_type, sub_type, description, is_field_goal, shot_value, shot_distance,
  shot_result, x, y, score_home, score_away, points_total, assist_player_id,
  steal_player_id, block_player_id, foul_drawn_player_id
)
SELECT p.game_id,
       p.actionNumber AS action_number,
       CAST(p.period AS SMALLINT) AS period,
       p.clock,
       pbp_seconds_elapsed(p.period, p.clock) AS seconds_elapsed,
       tx.team_id,
       px.player_id,
       p.actionType AS action_type,
       p.subType AS sub_type,
       p.description,
       p.isFieldGoal AS is_field_goal,
       CAST(coalesce(
         TRY_CAST(p.shotValue AS INTEGER),
         CASE
           WHEN p.actionType = '3pt' THEN 3
           WHEN p.actionType = '2pt' THEN 2
           ELSE 0
         END
       ) AS SMALLINT) AS shot_value,
       TRY_CAST(p.shotDistance AS DOUBLE) AS shot_distance,
       p.shotResult AS shot_result,
       TRY_CAST(p.x AS DOUBLE) AS x,
       TRY_CAST(p.y AS DOUBLE) AS y,
       CAST(p.scoreHome AS INTEGER) AS score_home,
       CAST(p.scoreAway AS INTEGER) AS score_away,
       CAST(p.pointsTotal AS INTEGER) AS points_total,
       assist_xw.player_id AS assist_player_id,
       steal_xw.player_id AS steal_player_id,
       block_xw.player_id AS block_player_id,
       foul_xw.player_id AS foul_drawn_player_id
FROM stg_kaggle_nba_play_by_play p
JOIN k_games kg ON kg.game_id = p.game_id
LEFT JOIN k_team_xw tx ON tx.source_team_id = CAST(p.teamId AS VARCHAR)
LEFT JOIN k_player_xw px ON px.source_player_id = CAST(p.personId AS VARCHAR)
LEFT JOIN k_player_xw assist_xw ON assist_xw.source_player_id = CAST(p.assistPersonId AS VARCHAR)
LEFT JOIN k_player_xw steal_xw ON steal_xw.source_player_id = CAST(p.stealPersonId AS VARCHAR)
LEFT JOIN k_player_xw block_xw ON block_xw.source_player_id = CAST(p.blockPersonId AS VARCHAR)
LEFT JOIN k_player_xw foul_xw ON foul_xw.source_player_id = CAST(p.foulDrawnPersonId AS VARCHAR)
ANTI JOIN fact_pbp_events f
  ON f.game_id = p.game_id
 AND f.action_number = p.actionNumber
ORDER BY p.game_id, p.actionNumber;

-- Verification summary.
WITH stg AS (
  SELECT game_id FROM k_games
),
unresolved AS (
  SELECT source_game_id
  FROM bridge_game_source_id
  WHERE source_system = 'kaggle_nba'
    AND is_unresolved
    AND unresolved_reason = 'not_in_dim_game'
    AND lpad(source_game_id, 10, '0') LIKE '004%'
)
SELECT 'staged 2025-26 playoff games' AS metric, count(*) AS n FROM stg
UNION ALL SELECT 'dim_game staged playoff games present', count(*) FROM dim_game WHERE game_id IN (SELECT game_id FROM stg)
UNION ALL SELECT 'fact_game staged playoff games present', count(*) FROM fact_game WHERE game_id IN (SELECT game_id FROM stg)
UNION ALL SELECT 'fact_team_game_log game rows', count(*) FROM fact_team_game_log WHERE game_id IN (SELECT game_id FROM stg)
UNION ALL SELECT 'fact_player_game_log game rows', count(*) FROM fact_player_game_log WHERE game_id IN (SELECT game_id FROM stg)
UNION ALL SELECT 'fact_player_game_boxscore game rows', count(*) FROM fact_player_game_boxscore WHERE game_id IN (SELECT game_id FROM stg)
UNION ALL SELECT 'fact_game_quarter_scores rows', count(*) FROM fact_game_quarter_scores WHERE game_id IN (SELECT game_id FROM stg)
UNION ALL SELECT 'fact_pbp_events rows', count(*) FROM fact_pbp_events WHERE game_id IN (SELECT game_id FROM stg)
UNION ALL SELECT 'currently unresolved 004* Kaggle games (pre resolve-only)', count(*) FROM unresolved
ORDER BY metric;
