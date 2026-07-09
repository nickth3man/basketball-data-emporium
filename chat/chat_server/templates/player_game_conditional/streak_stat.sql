-- Template: player_game_conditional.streak_stat
-- Params:
--   player_id       (int | NULL, NULL): filter to one player; NULL = all players.
--   season_type     (str, 'Regular'):   Regular | Playoffs | Cup.
--   min_streak_games (int, 2):         discard runs shorter than this.
--
-- Gaps-and-islands pattern over **consecutive games PLAYED** (not calendar
-- days) with BLK >= 1. Two-step sequencing:
--
--   1. ``played`` rows every fact_player_game_box row for the player
--      (no BLK filter) and numbers them with ROW_NUMBER() ORDER BY
--      game_date -> game_seq. Because the warehouse only contains rows
--      for games the player actually appeared in, game_seq is the
--      player's game-appearance index: 1 = first game played, 2 = second
--      game played, etc. Games where the player was absent (DNP, injury)
--      are simply absent rows and do NOT advance the sequence, which is
--      the correct semantics (a player can't break a block streak by
--      sitting out).
--
--   2. ``blk_games`` filters that sequence to rows with BLK >= 1, then
--      ``ordered`` numbers those rows with ROW_NUMBER() ORDER BY
--      game_seq -> rn. Within a contiguous run of BLK>=1 appearances,
--      rn advances in lockstep with game_seq, so ``game_seq - rn`` is
--      constant. As soon as the player appears in a game without a
--      block (or misses), game_seq advances but rn doesn't, breaking the
--      streak -- exactly the consecutive-games-PLAYED definition.
--
-- Prior bug: a single ``game_date - rn`` over BLK>=1 rows used
-- calendar-day differences as the grouping key. Because game_date jumps
-- by 1 day on a back-to-back but by 2-4 days across a normal week, that
-- key flips on every non-consecutive-night game. The warehouse's longest
-- such run was 5 (Kareem, Feb-Mar 1974) and was obviously wrong --
-- Mark Eaton's widely-cited 94-game streak and Patrick Ewing's 145-game
-- streak (below the cap) never appeared. The two-step sequence
-- ``game_seq - rn`` fixes this and matches the basketball-reference
-- "longest block streaks" leaderboard.
WITH played AS (
  SELECT
    player_id,
    game_date,
    blk,
    ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY game_date) AS game_seq
  FROM fact_player_game_box
  WHERE
    season_type = $season_type
),

blk_games AS (
  SELECT
    player_id,
    game_date,
    game_seq
  FROM played
  WHERE blk >= 1
),

ordered AS (
  SELECT
    player_id,
    game_date,
    game_seq,
    ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY game_seq) AS rn
  FROM blk_games
),

grouped AS (
  SELECT
    player_id,
    game_date,
    game_seq - rn AS grp_key
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