-- Template: career_demographic.hs_draftee_career_ws
-- Params:
--   top_n   (int, default 3): how many HS draftees to return, ranked by career WS DESC
-- Source-backed notes:
--   Win shares live on `src_agg_player_season_advanced` (NOT a
--   `src_bref_advanced` table — verified at load time). The aggregate
--   table stores one row per (player_id, team_id, season_year, season_type);
--   we sum `ws` over Regular season_type per player.
SELECT
  dp.player_id,
  dp.full_name,
  fd.team_abbreviation AS drafting_team,
  ROUND(SUM(sa.ws), 1) AS career_ws
FROM fact_draft AS fd
INNER JOIN dim_player AS dp ON fd.player_id = dp.player_id
INNER JOIN src_agg_player_season_advanced AS sa
  ON
    dp.player_id = sa.player_id
    AND sa.season_type = 'Regular'
WHERE fd.organization_type = 'High School'
GROUP BY dp.player_id, dp.full_name, fd.team_abbreviation
ORDER BY career_ws DESC
LIMIT $top_n
