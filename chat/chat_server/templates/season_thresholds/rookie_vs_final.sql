-- Template: season_thresholds.rookie_vs_final
-- Params:
--   player_id   (int):  dim_player.player_id of the target player.
--   season_type (str, default 'Regular'): Regular | Playoffs | Cup
-- Source-backed notes:
--   `avg_pts` and `avg_reb` live on `mart_player_season` (verified — the
--   mart does expose per-game averages, contrary to the simplified plan
--   summary in §11.1). The "rookie" season is the earliest season_year
--   the player appears in for the given season_type; "final" is the latest.
SELECT
  dp.player_id,
  dp.full_name,
  mps.season_year,
  mps.avg_pts,
  mps.avg_reb,
  row_kind
FROM (
  SELECT
    mps_inner.player_id,
    mps_inner.season_year,
    mps_inner.avg_pts,
    mps_inner.avg_reb,
    CASE
      WHEN ROW_NUMBER() OVER (
        PARTITION BY mps_inner.player_id
        ORDER BY mps_inner.season_year ASC
      ) = 1 THEN 'rookie'
      WHEN ROW_NUMBER() OVER (
        PARTITION BY mps_inner.player_id
        ORDER BY mps_inner.season_year DESC
      ) = 1 THEN 'final'
    END AS row_kind
  FROM mart_player_season AS mps_inner
  WHERE mps_inner.season_type = $season_type
) AS mps
INNER JOIN dim_player AS dp ON mps.player_id = dp.player_id
WHERE
  mps.player_id = $player_id
  AND mps.row_kind IS NOT NULL
ORDER BY mps.row_kind
