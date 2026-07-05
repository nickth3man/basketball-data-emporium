-- Template: player_game_conditional.streak_stat
-- Params:
--   player_id       (int | NULL, NULL): filter to one player; NULL = all players.
--   season_type     (str, 'Regular'):   Regular | Playoffs | Cup.
--   min_streak_games (int, 2):         discard runs shorter than this.
--
-- Gaps-and-islands pattern: for each (player_id, BLK>=1 game) row,
-- compute ROW_NUMBER() over the player's calendar. Two rows that are
-- consecutive in date order share the same `grp_key` (game_date - rn).
-- A streak = rows that share the same grp_key for a given player.
--
-- Limitation: fact_player_game_box only contains rows for games the
-- player appeared in, so a streak spans only consecutive games the
-- player played with BLK >= 1. Games where the player was absent are
-- simply absent rows, which is the documented assumption (PLAN §12
-- row 14 notes "needs missed-game definition").
WITH ordered AS (
  SELECT
    player_id,
    game_date,
    ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY game_date) AS rn
  FROM fact_player_game_box
  WHERE
    season_type = $season_type
    AND blk >= 1
),

grouped AS (
  SELECT
    player_id,
    game_date,
    CAST(game_date AS DATE) - CAST(rn AS INTEGER) AS grp_key
  FROM ordered
),

runs AS (
  SELECT
    player_id,
    grp_key,
    COUNT(*) AS streak_games,
    MIN(game_date) AS streak_start,
    MAX(game_date) AS streak_end
  FROM grouped
  GROUP BY player_id, grp_key
)

SELECT
  r.player_id,
  dp.full_name,
  r.streak_games,
  CAST(r.streak_start AS VARCHAR) AS streak_start,
  CAST(r.streak_end AS VARCHAR) AS streak_end
FROM runs AS r
INNER JOIN dim_player AS dp
  ON r.player_id = dp.player_id
WHERE
  r.streak_games >= $min_streak_games
  AND ($player_id IS NULL OR r.player_id = $player_id)
ORDER BY r.streak_games DESC, r.streak_start ASC
