-- Template: season_thresholds.fifty_forty_ninety
-- Params:
--   min_ppg (float, default 25.0): minimum points per game
--   fg_min  (float, default 0.50): minimum FG%
--   fg3_min (float, default 0.40): minimum 3P%
--   ft_min  (float, default 0.90): minimum FT%
--   season_type (str, default 'Regular'): Regular | Playoffs | Cup
SELECT
    mps.player_id,
    dp.full_name,
    mps.season_year,
    mps.fg_pct,
    mps.fg3_pct,
    mps.ft_pct,
    mps.avg_pts
FROM mart_player_season AS mps
JOIN dim_player AS dp
    ON mps.player_id = dp.player_id
WHERE mps.fg_pct >= $fg_min
    AND mps.fg3_pct >= $fg3_min
    AND mps.ft_pct >= $ft_min
    AND mps.avg_pts >= $min_ppg
    AND mps.season_type = $season_type
ORDER BY mps.avg_pts DESC, mps.season_year DESC