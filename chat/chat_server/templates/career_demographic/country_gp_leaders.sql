-- Template: career_demographic.country_gp_leaders
-- Params:
--   min_gp   (int, default 500): minimum career GP to qualify as a "leader"
--   top_n    (int, default 5):   how many top countries to return (by player count DESC)
-- Returns one row per qualifying country (excluding USA) with:
--   - player_count: number of players from that country with career_gp >= min_gp
--   - top_scorer_full_name: highest career_pts player from that country (also >= min_gp)
--   - top_scorer_career_pts: that player's career points
WITH qualified_players AS (
  SELECT
    dp.player_id,
    dp.country,
    dp.full_name,
    mpc.career_gp,
    mpc.career_pts
  FROM dim_player AS dp
  INNER JOIN mart_player_career AS mpc ON dp.player_id = mpc.player_id
  WHERE
    dp.country IS NOT NULL
    AND dp.country <> 'USA'
    AND mpc.career_gp >= $min_gp
),

country_counts AS (
  SELECT
    country,
    COUNT(*) AS player_count
  FROM qualified_players
  GROUP BY country
),

ranked_scorers AS (
  SELECT
    qp.country,
    qp.full_name AS top_scorer_full_name,
    qp.career_pts AS top_scorer_career_pts,
    ROW_NUMBER() OVER (
      PARTITION BY qp.country
      ORDER BY qp.career_pts DESC, qp.full_name ASC
    ) AS rn
  FROM qualified_players AS qp
)

SELECT
  cc.country,
  cc.player_count,
  rs.top_scorer_full_name,
  rs.top_scorer_career_pts
FROM country_counts AS cc
INNER JOIN ranked_scorers AS rs
  ON cc.country = rs.country AND rs.rn = 1
ORDER BY cc.player_count DESC, cc.country ASC
LIMIT $top_n
