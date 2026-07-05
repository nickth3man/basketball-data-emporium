-- Template: pbp_aggregate.largest_scoring_run
-- PLAN §12 row 10: largest scoring run in an NBA Finals game since 2010.
--
-- Strategy (gaps-and-islands across the play-by-play event sequence):
--   1. Restrict to NBA Finals games (dim_game.game_label = 'NBA Finals') with
--      season_year >= $since_season.  This bounds the scan to ~100 games
--      rather than the full 18.7M-row fact_pbp_event table.
--   2. Identify every scoring play.  We use shot_result='Made' AND
--      team_id IS NOT NULL.  The scoring team is the row's team_id
--      (verified against dim_game — for Made Shot / Made 2pt / Made 3pt /
--      Free Throw rows, team_id equals the side that scored).
--      NB: fact_pbp_event.points_total is the cumulative (home+away) score
--      at the time of the row, NOT the points this play just scored; we
--      therefore do NOT use points_total to detect scoring plays.
--   3. Order scoring events within each game by (period, seconds_elapsed,
--      action_number).  seconds_elapsed is monotonic across periods, so a
--      total ordering is unambiguous.
--   4. Number each "island" — a maximal run of consecutive scoring plays
--      by the same team — with a windowed SUM-of-island-starts.
--   5. Aggregate each island to its run_points (sum of shot_value),
--      run_length (count of plays), and the first/last seconds_elapsed.
--   6. Return the top-N runs ordered by run_points DESC.
--
-- Performance:
--   * With $since_season='2009-10', this scans ~100 Finals games * ~150
--     scoring events per game = ~15K rows after the where-clause; full
--     execution is sub-second on the local warehouse.
--   * 300s hard timeout via TIMEOUT_SECONDS in the metadata module.
WITH finals_games AS (
  SELECT game_id
  FROM dim_game
  WHERE
    game_label = 'NBA Finals'
    AND season_year >= $since_season
),

scoring AS (
  SELECT
    pbp.game_id,
    pbp.period,
    pbp.seconds_elapsed,
    pbp.action_number,
    pbp.team_id,
    COALESCE(pbp.shot_value, 0) AS shot_value
  FROM fact_pbp_event AS pbp
  INNER JOIN finals_games AS fg ON pbp.game_id = fg.game_id
  WHERE
    pbp.shot_result = 'Made'
    AND pbp.team_id IS NOT NULL
),

ordered AS (
  SELECT
    s.*,
    LAG(s.team_id) OVER (
      PARTITION BY s.game_id
      ORDER BY s.period, s.seconds_elapsed, s.action_number
    ) AS prev_team
  FROM scoring AS s
),

islands AS (
  SELECT
    o.game_id,
    o.team_id,
    o.period,
    o.seconds_elapsed,
    o.action_number,
    o.shot_value,
    SUM(CASE WHEN o.team_id = o.prev_team THEN 0 ELSE 1 END) OVER (
      PARTITION BY o.game_id
      ORDER BY o.period, o.seconds_elapsed, o.action_number
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS island_id
  FROM ordered AS o
)

SELECT
  i.game_id,
  g.season_year,
  g.game_date,
  i.team_id AS scoring_team_id,
  SUM(i.shot_value) AS run_points,
  COUNT(*) AS scoring_plays,
  MIN(i.period) AS run_start_period,
  MIN(i.seconds_elapsed) AS run_start_elapsed,
  MAX(i.seconds_elapsed) AS run_end_elapsed
FROM islands AS i
INNER JOIN dim_game AS g ON i.game_id = g.game_id
GROUP BY i.game_id, g.season_year, g.game_date, i.team_id, i.island_id
ORDER BY run_points DESC, scoring_plays DESC
LIMIT $top_n
