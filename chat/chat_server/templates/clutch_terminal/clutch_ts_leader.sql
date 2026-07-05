-- Template: clutch_terminal.clutch_ts_leader
-- PLAN §12 row 8: highest True Shooting percentage in the postseason
-- clutch window (last N minutes of 4th quarter + OT, score within M).
--
-- Clutch definition
-- -----------------
-- * Period >= 4 (Q4 and any OT periods).
-- * ``seconds_elapsed >= period_end - $clutch_minutes * 60``, where
--   ``period_end`` = 2880 for Q4 and ``2880 + (period - 4) * 300`` for
--   OT periods (OT is 5 min / 300s; Q4 is 12 min / 720s).
-- * ``ABS(score_home - score_away) <= $clutch_margin`` — score within
--   $clutch_margin at the time of the event.
--
-- True Shooting Percentage
-- ------------------------
-- TS% = PTS / (2 * (FGA + 0.44 * FTA)), where
--   PTS  = sum(shot_value) for made FGs + count(made FTs)
--   FGA  = count of all FG attempts (``is_field_goal = true``)
--   FTA  = count of all FT attempts (``action_type IN
--          ('freethrow','free throw')``).
-- The action-type values alternate between Pascal-case and lowercase
-- across seasons, hence the LOWER() predicate (verified Phase 6).
--
-- Performance
-- -----------
-- ``season_year`` + ``season_type`` filter bounds the PBP scan to a
-- single postseason (~80 games * ~500 events).  Sub-second locally.
-- 300s hard timeout via the metadata module.
WITH clutch AS (
  SELECT pbp.*
  FROM fact_pbp_event AS pbp
  INNER JOIN dim_game AS g ON pbp.game_id = g.game_id
  WHERE
    g.season_year = $season_year
    AND g.season_type = $season_type
    AND pbp.period >= 4
    AND pbp.seconds_elapsed >= (
      CASE
        WHEN pbp.period = 4 THEN 2880
        ELSE 2880 + (CAST(pbp.period AS INTEGER) - 4) * 300
      END
    ) - $clutch_minutes * 60
    AND ABS(COALESCE(pbp.score_home, 0) - COALESCE(pbp.score_away, 0))
    <= $clutch_margin
    AND pbp.player_id IS NOT NULL
),

clutch_per_player AS (
  SELECT
    c.player_id,
    SUM(CASE
      WHEN c.is_field_goal = TRUE AND c.shot_result = 'Made'
        THEN COALESCE(c.shot_value, 0)
      ELSE 0
    END) AS clutch_pts_fg,
    SUM(CASE
      WHEN c.is_field_goal = TRUE
        THEN 1
      ELSE 0
    END) AS clutch_fga,
    SUM(CASE
      WHEN
        LOWER(c.action_type) IN ('freethrow', 'free throw')
        AND c.shot_result = 'Made'
        THEN 1
      ELSE 0
    END) AS clutch_ftm,
    SUM(CASE
      WHEN LOWER(c.action_type) IN ('freethrow', 'free throw')
        THEN 1
      ELSE 0
    END) AS clutch_fta
  FROM clutch AS c
  GROUP BY c.player_id
  HAVING SUM(CASE WHEN c.is_field_goal = TRUE THEN 1 ELSE 0 END) >= $min_attempts
)

SELECT
  cpp.player_id,
  dp.full_name,
  cpp.clutch_pts_fg + cpp.clutch_ftm AS clutch_pts,
  cpp.clutch_fga,
  cpp.clutch_ftm,
  cpp.clutch_fta,
  (cpp.clutch_pts_fg + cpp.clutch_ftm) * 1.0
  / (2.0 * (cpp.clutch_fga + 0.44 * cpp.clutch_fta)) AS clutch_ts_pct
FROM clutch_per_player AS cpp
INNER JOIN dim_player AS dp ON cpp.player_id = dp.player_id
ORDER BY clutch_ts_pct DESC, cpp.clutch_fga + cpp.clutch_fta DESC
LIMIT $top_n
