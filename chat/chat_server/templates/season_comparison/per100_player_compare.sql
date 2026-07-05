-- Template: season_comparison.per100_player_compare
-- Params:
--   player_a_id (int):            dim_player.player_id (player A).
--   player_b_id (int):            dim_player.player_id (player B).
--   season_year (str):            Canonical season_year, e.g. '2021-22'.
--
-- Source-backed against src_stg_bref_per_100_poss (BBR per-100-possession
-- player-season table). The table's grain is per-player-season, so a
-- multi-team season produces one row per (player, team) plus a "2TM"
-- aggregate row; we keep the row with the most games (the aggregate when
-- it exists) via ROW_NUMBER().
WITH ranked AS (
  SELECT
    nba_player_id,
    season,
    season_end_year,
    g,
    mp,
    pts_per_100_poss,
    ast_per_100_poss,
    ROW_NUMBER() OVER (
      PARTITION BY nba_player_id
      ORDER BY g DESC
    ) AS rn
  FROM src_stg_bref_per_100_poss
  WHERE
    season_end_year = CAST(SUBSTR($season_year, 1, 4) AS INTEGER) + 1
    AND lg = 'NBA'
    AND nba_player_id IN ($player_a_id, $player_b_id)
)

SELECT
  r.nba_player_id AS player_id,
  dp.full_name,
  r.pts_per_100_poss AS per_100_pts,
  r.ast_per_100_poss AS per_100_ast,
  r.g AS games
FROM ranked AS r
INNER JOIN dim_player AS dp
  ON r.nba_player_id = dp.player_id
WHERE r.rn = 1
ORDER BY per_100_pts DESC
