-- Template: clutch_terminal.buzzer_beaters
-- Params:
--   player_id     (int):            dim_player.player_id of the target player.
--   since_season  (str, '1996-97'): inclusive lower bound on dim_game.season_year.
--   clock_window  (float, 3.0):     max remaining-clock seconds for the play to qualify.
--
-- Definition (Option B — aligned with Basketball-Reference's canonical rule)
-- ---------------------------------------------------------------------
-- A "game-winning buzzer-beater" is the scoring play (field goal OR free
-- throw) that produced the game's FINAL lead change: the made shot after
-- which the eventual winner moved from tied/trailing into the lead, with
-- no later play flipping it back. BBR's methodology
-- (sports-reference.com/blog/2020/02/buzzer-beaters-explainer) defines
-- these as "successful shots taken with the shooter's team tied or
-- trailing which left no time on the clock" and explicitly counts free
-- throws ("772 such shots ... including free throws with time expired").
-- The r/nba "Call Game Award" uses the same "first points that eclipsed
-- the losing team's total" framing and separately counts Gamewinning FTs.
--
-- Why this replaced the prior FG-only logic
-- -----------------------------------------
-- The old query filtered ``is_field_goal = TRUE AND shot_result = 'Made'``
-- and took the last made FG of the game by the winner inside the clock
-- window. That had two correctness flaws, both fixed by the tied/trailing
-- -> leading rule below:
--   1. It was structurally blind to free throws, so any game WON at the
--      FT line in the final buzzer sequence was missed (e.g. Jimmy
--      Butler, Heat @ Bucks 2020-09-02 Bubble: tied 114-114, fouled at
--      0:00, made both FTs after the horn to win 116-114).
--   2. It had no tied/trailing condition, so an "insurance" FG scored by
--      the winner in the last few seconds while ALREADY leading also
--      qualified (e.g. three of Kobe's prior 17 "buzzer-beaters" were
--      last-3s FGs with the Lakers already up 3-7 -- now correctly
--      excluded; Kobe's true count under this definition is 14).
--
-- Algorithm
-- ----------
-- 1. ``scored`` -- every made scoring event (FG or FT; ``shot_value`` is
--    1 for a FT, 2/3 for a FG), carrying the post-event score snapshot
--    and the parsed remaining clock.
-- 2. ``scored_margin`` -- ``margin_after`` from the scoring team's
--    perspective (home or away), and ``margin_before = margin_after -
--    shot_value`` (the per-event score snapshot advances by exactly the
--    shot's value, so subtracting it reconstructs the score immediately
--    before the event).
-- 3. ``lead_flips`` -- events where the team was tied or trailing before
--    (``margin_before <= 0``) and leading after (``margin_after > 0``),
--    restricted to the eventual winner. ``ROW_NUMBER() ... ORDER BY
--    action_number DESC`` picks each game's LAST such flip (the winning
--    play -- since the winner ends up ahead, no later event reverses it).
-- 4. Keep only that last flip (rn_last_flip = 1), require period >= 4
--    and clock <= $clock_window, apply the player/season filters.
--
-- score_after_margin is the buzzer-beating shot's value (1 = FT, 2/3 =
-- FG); the final winning margin is recoverable from home_score/away_score.
WITH scored AS (
  SELECT
    pbp.game_id,
    pbp.action_number,
    pbp.period,
    pbp.clock,
    pbp.team_id,
    pbp.player_id,
    pbp.shot_value,
    pbp.score_home,
    pbp.score_away,
    CAST(SUBSTR(pbp.clock, 3, 2) AS INT) * 60
      + CAST(SUBSTR(pbp.clock, 6, 2) AS INT)
      + CAST(SUBSTR(pbp.clock, 9, 2) AS DOUBLE) / 100.0
      AS clock_seconds_remaining
  FROM fact_pbp_event AS pbp
  WHERE
    pbp.shot_result = 'Made'
    AND pbp.shot_value >= 1
),

scored_margin AS (
  SELECT
    s.game_id,
    s.action_number,
    s.period,
    s.clock,
    s.clock_seconds_remaining,
    s.team_id,
    s.player_id,
    s.shot_value,
    g.winner_team_id,
    g.home_team_id,
    CASE WHEN s.team_id = g.home_team_id
         THEN s.score_home - s.score_away
         ELSE s.score_away - s.score_home
    END AS margin_after,
    CASE WHEN s.team_id = g.home_team_id
         THEN s.score_home - s.score_away
         ELSE s.score_away - s.score_home
    END - s.shot_value AS margin_before
  FROM scored AS s
  INNER JOIN dim_game AS g ON s.game_id = g.game_id
),

lead_flips AS (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY game_id
      ORDER BY action_number DESC
    ) AS rn_last_flip
  FROM scored_margin
  WHERE
    margin_before <= 0
    AND margin_after > 0
    AND team_id = winner_team_id
)

SELECT
  f.game_id,
  g.season_year,
  g.game_date,
  f.period,
  f.clock,
  f.team_id AS scoring_team_id,
  CASE
    WHEN g.home_team_id = f.team_id THEN g.away_team_id
    ELSE g.home_team_id
  END AS opponent_team_id,
  g.home_score,
  g.away_score,
  f.shot_value AS score_after_margin
FROM lead_flips AS f
INNER JOIN dim_game AS g ON f.game_id = g.game_id
WHERE
  f.rn_last_flip = 1
  AND f.period >= 4
  AND f.clock_seconds_remaining <= $clock_window
  AND ($player_id IS NULL OR f.player_id = $player_id)
  AND g.season_year >= $since_season
ORDER BY g.game_date
