-- Template: clutch_terminal.buzzer_beaters
-- PLAN §12 row 18: game-winning buzzer-beaters for a single player +
-- opponents.  Spike-gated (PLAN §15) — shipped REAL because Phase 6
-- verification showed the late-clock PBP data is reliable enough from
-- 1996-97 onward (17+ verified candidates for Kobe Bryant id=977;
-- see chat_tests/test_templates_part_c.py).
--
-- Buzzer-beater definition
-- ------------------------
-- A "game-winning buzzer-beater" is a made field goal in the last
-- $clock_window seconds (default 3.0) of Q4 or OT that:
--   1. is the LAST made field goal of the game (no other
--      ``shot_result='Made'`` row has a strictly higher
--      ``action_number`` in the same game);
--   2. was made by the team that ended up winning the game
--      (``pbp.team_id = g.winner_team_id``).
--
-- Clock filtering
-- ---------------
-- The PBP ``clock`` column is an ISO 8601 duration
-- (``PT{MM}M{SS}.{HUNDREDTHS}S``).  We parse it with ``SUBSTR``:
--   minutes   = CAST(SUBSTR(clock, 3, 2) AS INT)
--   seconds   = CAST(SUBSTR(clock, 6, 2) AS INT)
--   hundredths= CAST(SUBSTR(clock, 9, 2) AS INT)
-- Total remaining seconds = minutes*60 + seconds + hundredths/100.  We
-- require this to be <= $clock_window.
--
-- Opponent attribution
-- --------------------
-- If the player is on the winning team, the opponent is the other team
-- in the game.  ``score_after_margin`` is the shot's ``shot_value`` —
-- for a buzzer-beater it is the value of the made FG (2 or 3 points).
--
-- Performance
-- -----------
-- The CTE restricts to one player + a ``season_year`` window before
-- windowing; one season's worth of buzzer-beaters is <100 rows.
WITH parsed AS (
  SELECT
    pbp.game_id,
    pbp.action_number,
    pbp.period,
    pbp.clock,
    pbp.team_id,
    pbp.shot_value,
    pbp.player_id,
    CAST(SUBSTR(pbp.clock, 3, 2) AS INT) * 60
    + CAST(SUBSTR(pbp.clock, 6, 2) AS INT)
    + CAST(SUBSTR(pbp.clock, 9, 2) AS DOUBLE) / 100.0
      AS clock_seconds_remaining
  FROM fact_pbp_event AS pbp
  WHERE
    pbp.is_field_goal = TRUE
    AND pbp.shot_result = 'Made'
),

last_fg_per_game AS (
  SELECT
    game_id,
    MAX(action_number) AS last_fg_action
  FROM fact_pbp_event
  WHERE is_field_goal = TRUE AND shot_result = 'Made'
  GROUP BY game_id
)

SELECT
  p.game_id,
  g.season_year,
  g.game_date,
  p.period,
  p.clock,
  p.team_id AS scoring_team_id,
  CASE
    WHEN g.home_team_id = p.team_id THEN g.away_team_id
    ELSE g.home_team_id
  END AS opponent_team_id,
  g.home_score,
  g.away_score,
  p.shot_value AS score_after_margin
FROM parsed AS p
INNER JOIN last_fg_per_game AS lf ON p.game_id = lf.game_id
INNER JOIN dim_game AS g ON p.game_id = g.game_id
WHERE
  p.player_id = $player_id
  AND p.period >= 4
  AND p.action_number = lf.last_fg_action
  AND p.clock_seconds_remaining <= $clock_window
  AND p.team_id = g.winner_team_id
  AND g.season_year >= $since_season
ORDER BY g.game_date
