-- Materialise the NBA-lineage reference (playerstatistics.csv, NBA.com-derived,
-- 1946-2026 boxscores keyed by real NBA person ids) as a per-player-season
-- parquet for fast repeated joins during reconciliation.
--
-- Season is decoded from the gameId prefix: TYYxxxxx where T is the game
-- type (1 pre, 2 regular, 3 all-star, 4 playoffs, 5 play-in, 6 cup final)
-- and YY the season START year mod 100 — no date heuristics needed.
--
-- Run from repo root:  duckdb -c ".read data/audit/prepare_nba_lineage.sql"

CREATE OR REPLACE TEMP TABLE raw AS
SELECT personId,
       CAST(gameId AS VARCHAR) AS game_id,
       gameType,
       TRY_CAST(points AS DOUBLE)             AS pts,
       TRY_CAST(assists AS DOUBLE)            AS ast,
       TRY_CAST(reboundsTotal AS DOUBLE)      AS reb,
       TRY_CAST(steals AS DOUBLE)             AS stl,
       TRY_CAST(blocks AS DOUBLE)             AS blk,
       TRY_CAST(fieldGoalsMade AS DOUBLE)     AS fgm,
       TRY_CAST(fieldGoalsAttempted AS DOUBLE) AS fga,
       TRY_CAST(threePointersMade AS DOUBLE)  AS fg3m,
       TRY_CAST(freeThrowsMade AS DOUBLE)     AS ftm,
       numMinutes,
       win
FROM read_csv('C:/Users/nicolas/Documents/GitHub/basketball-data/csv/nba/playerstatistics.csv',
              strict_mode=false, sample_size=-1,
              types={'personId':'BIGINT','gameId':'VARCHAR','numMinutes':'VARCHAR'});

CREATE OR REPLACE TEMP TABLE typed AS
SELECT *,
       substr(game_id, 1, 1) AS game_type_code,
       CASE WHEN TRY_CAST(substr(game_id, 2, 2) AS INT) >= 46
            THEN 1900 + TRY_CAST(substr(game_id, 2, 2) AS INT)
            ELSE 2000 + TRY_CAST(substr(game_id, 2, 2) AS INT)
       END + 1 AS season          -- season END year, BBR convention
FROM raw
WHERE length(game_id) = 8
  -- DNP rows ("NWT - injury" etc.) carry empty minutes and would inflate GP
  AND numMinutes IS NOT NULL AND trim(numMinutes) NOT IN ('', '0', '0.0', '0:00');

COPY (
  SELECT personId AS player_id, season, game_type_code,
         count(*)  AS gp,
         sum(pts)  AS pts,
         sum(ast)  AS ast,
         sum(reb)  AS reb,
         sum(stl)  AS stl,
         sum(blk)  AS blk,
         sum(fgm)  AS fgm,
         sum(fga)  AS fga,
         sum(fg3m) AS fg3m,
         sum(ftm)  AS ftm
  FROM typed
  WHERE game_type_code IN ('2', '4')   -- regular season + playoffs
  GROUP BY 1, 2, 3
) TO 'data/audit/out/nba_lineage_player_season.parquet' (FORMAT PARQUET);

SELECT game_type_code, count(*) AS player_games, count(DISTINCT season) AS seasons
FROM typed GROUP BY 1 ORDER BY 1;
