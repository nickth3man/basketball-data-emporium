-- Rebuild the leaders/ranks layer in-place from clean sources, keeping table
-- names and column shapes identical (companion to rebuild_curated_layer.sql;
-- run AFTER it, same invocation pattern):
--   duckdb data/nba.duckdb -c ".read data/audit/rebuild_leaders_layer.sql"
--
-- Targets (all still carried the franchise-era fan-out inflation the 2026-07
-- audit documented; verified via Kobe's LAL points 38,108 vs true 33,643 and
-- Jokic's 2022-23 PTS rank 26):
--   fact_franchise_leaders    <- per-team career leaders from fact_player_career
--                               (that table is verified clean: Kobe 33,643,
--                               MJ 32,292, Malone 36,928 — exact BBR)
--   agg_league_leaders        <- per-season per-game leaders + ranks from the
--                               rebuilt agg_player_season, with BBR-style
--                               qualification (58-of-82 games, or the stat
--                               total floor: 1400 pts / 800 reb / 400 ast /
--                               125 stl / 100 blk, scaled to schedule length)
--   fact_player_season_ranks  <- same qualification, full rank_* column set,
--                               from fact_player_season_stat_resolved
--   analytics_draft_value     <- draft_history (healed pick numbers) joined
--                               with fact_player_career career sums
-- Originals preserved once as *_legacy_fanout.

CREATE OR REPLACE MACRO end_year_to_season(y) AS
  CAST(y - 1 AS VARCHAR) || '-' || lpad(CAST(y % 100 AS VARCHAR), 2, '0');

CREATE TABLE IF NOT EXISTS fact_franchise_leaders_legacy_fanout AS SELECT * FROM fact_franchise_leaders;
CREATE TABLE IF NOT EXISTS agg_league_leaders_legacy_fanout AS SELECT * FROM agg_league_leaders;
CREATE TABLE IF NOT EXISTS fact_player_season_ranks_legacy_fanout AS SELECT * FROM fact_player_season_ranks;
CREATE TABLE IF NOT EXISTS analytics_draft_value_legacy_fanout AS SELECT * FROM analytics_draft_value;

-- ------------------------------------------------- fact_franchise_leaders

CREATE OR REPLACE TABLE fact_franchise_leaders AS
WITH per_team AS (
  SELECT c.team_id, c.player_id,
         sum(c.pts) AS pts, sum(c.ast) AS ast, sum(c.reb) AS reb,
         sum(c.blk) AS blk, sum(c.stl) AS stl
  FROM fact_player_career c
  WHERE c.league_id = 'NBA' AND c.career_type = 'Regular Season' AND c.team_id IS NOT NULL
  GROUP BY 1, 2
),
named AS (
  SELECT t.*, p.full_name
  FROM per_team t
  LEFT JOIN dim_player p ON p.player_id = t.player_id AND p.is_current
)
SELECT team_id,
       max_by(pts, pts)              AS pts,
       max_by(player_id, pts)        AS pts_person_id,
       max_by(full_name, pts)        AS pts_player,
       max_by(ast, ast)              AS ast,
       max_by(player_id, ast)        AS ast_person_id,
       max_by(full_name, ast)        AS ast_player,
       max_by(reb, reb)              AS reb,
       max_by(player_id, reb)        AS reb_person_id,
       max_by(full_name, reb)        AS reb_player,
       max_by(blk, blk)              AS blk,
       max_by(player_id, blk)        AS blk_person_id,
       max_by(full_name, blk)        AS blk_player,
       max_by(stl, stl)              AS stl,
       max_by(player_id, stl)        AS stl_person_id,
       max_by(stl_player_name, stl)  AS stl_player
FROM (SELECT *, full_name AS stl_player_name FROM named)
GROUP BY team_id;

-- ---------------------------------------------------- shared season frame
-- Player-season level (stints summed) from the resolved BBR stats, with the
-- diacritic-fallback through our crosswalk (same as rebuild_curated_layer).

CREATE OR REPLACE TEMP TABLE pxw AS
SELECT bbr_player_id, nba_player_id
FROM (
  SELECT b.bbr_player_id, b.nba_player_id,
         row_number() OVER (
           PARTITION BY b.bbr_player_id
           ORDER BY coalesce(g.gp, 0) DESC, b.nba_player_id
         ) AS rn
  FROM bridge_player_bbr b
  LEFT JOIN (SELECT player_id, count(*) AS gp FROM fact_player_game_log GROUP BY 1) g
    ON g.player_id = b.nba_player_id
) WHERE rn = 1;

CREATE OR REPLACE TEMP TABLE season_frame AS
WITH r AS (
  SELECT coalesce(x.person_id, p.nba_player_id) AS player_id, x.*
  FROM fact_player_season_stat_resolved x
  LEFT JOIN pxw p ON x.person_id IS NULL AND p.bbr_player_id = x.slug
),
summed AS (
  SELECT player_id, season,
         CASE WHEN is_playoffs THEN 'Playoffs' ELSE 'Regular' END AS season_type,
         sum(gp) AS gp, sum(gs) AS gs, sum(min) AS min,
         sum(pts) AS pts, sum(coalesce(trb, orb + drb)) AS reb,
         sum(orb) AS oreb, sum(drb) AS dreb,
         sum(ast) AS ast, sum(stl) AS stl, sum(blk) AS blk, sum(tov) AS tov,
         sum(fg) AS fgm, sum(fga) AS fga,
         sum(tp) AS fg3m, sum(tpa) AS fg3a,
         sum(ft) AS ftm, sum(fta) AS fta,
         max_by(team_id, gp) AS team_id,
         max_by(team_abbrev, gp) AS team_abbreviation
  FROM r
  WHERE player_id IS NOT NULL
  GROUP BY 1, 2, 3
),
sched AS (  -- schedule length proxy per season/type = max games any player logged
  SELECT season, season_type, max(gp) AS sched_gp FROM summed GROUP BY 1, 2
)
SELECT s.*, sc.sched_gp,
       -- BBR-style qualification (Regular only; Playoffs ranks are unqualified)
       s.season_type = 'Playoffs'
         OR s.gp >= round(58.0 * sc.sched_gp / 82.0) AS q_games,
       s.pts >= round(1400.0 * sc.sched_gp / 82.0) AS q_pts,
       s.reb >= round(800.0 * sc.sched_gp / 82.0)  AS q_reb,
       s.ast >= round(400.0 * sc.sched_gp / 82.0)  AS q_ast,
       s.stl >= round(125.0 * sc.sched_gp / 82.0)  AS q_stl,
       s.blk >= round(100.0 * sc.sched_gp / 82.0)  AS q_blk,
       s.fga  >= round(300.0 * sc.sched_gp / 82.0) AS q_fg_pct,
       s.fta  >= round(125.0 * sc.sched_gp / 82.0) AS q_ft_pct,
       s.fg3a >= round(82.0 * sc.sched_gp / 82.0)  AS q_fg3_pct
FROM summed s
JOIN sched sc USING (season, season_type);

-- ------------------------------------------------------ agg_league_leaders

CREATE OR REPLACE TABLE agg_league_leaders AS
SELECT player_id,
       end_year_to_season(season) AS season_year,
       season_type,
       CAST(gp AS BIGINT) AS gp,
       pts / nullif(gp, 0) AS avg_pts,
       reb / nullif(gp, 0) AS avg_reb,
       ast / nullif(gp, 0) AS avg_ast,
       stl / nullif(gp, 0) AS avg_stl,
       blk / nullif(gp, 0) AS avg_blk,
       fgm / nullif(fga, 0) AS fg_pct,
       fg3m / nullif(fg3a, 0) AS fg3_pct,
       ftm / nullif(fta, 0) AS ft_pct,
       CASE WHEN (q_games OR q_pts) AND pts IS NOT NULL THEN
         rank() OVER (PARTITION BY season, season_type
                      ORDER BY CASE WHEN (q_games OR q_pts) THEN pts / nullif(gp, 0) END DESC NULLS LAST) END AS pts_rank,
       CASE WHEN (q_games OR q_reb) AND reb IS NOT NULL THEN
         rank() OVER (PARTITION BY season, season_type
                      ORDER BY CASE WHEN (q_games OR q_reb) THEN reb / nullif(gp, 0) END DESC NULLS LAST) END AS reb_rank,
       CASE WHEN (q_games OR q_ast) AND ast IS NOT NULL THEN
         rank() OVER (PARTITION BY season, season_type
                      ORDER BY CASE WHEN (q_games OR q_ast) THEN ast / nullif(gp, 0) END DESC NULLS LAST) END AS ast_rank,
       CASE WHEN (q_games OR q_stl) AND stl IS NOT NULL THEN
         rank() OVER (PARTITION BY season, season_type
                      ORDER BY CASE WHEN (q_games OR q_stl) THEN stl / nullif(gp, 0) END DESC NULLS LAST) END AS stl_rank,
       CASE WHEN (q_games OR q_blk) AND blk IS NOT NULL THEN
         rank() OVER (PARTITION BY season, season_type
                      ORDER BY CASE WHEN (q_games OR q_blk) THEN blk / nullif(gp, 0) END DESC NULLS LAST) END AS blk_rank
FROM season_frame;

-- ------------------------------------------------ fact_player_season_ranks

CREATE OR REPLACE TABLE fact_player_season_ranks AS
SELECT player_id,
       end_year_to_season(season) AS season_id,
       '00' AS league_id,
       team_id,
       team_abbreviation,
       CAST(NULL AS BIGINT) AS player_age,
       CAST(gp AS BIGINT) AS gp,
       CAST(gs AS BIGINT) AS gs,
       CASE WHEN q_games AND min IS NOT NULL THEN
         rank() OVER (PARTITION BY season, season_type ORDER BY CASE WHEN q_games THEN min / nullif(gp, 0) END DESC NULLS LAST) END AS rank_min,
       CASE WHEN q_games AND fgm IS NOT NULL THEN
         rank() OVER (PARTITION BY season, season_type ORDER BY CASE WHEN q_games THEN fgm / nullif(gp, 0) END DESC NULLS LAST) END AS rank_fgm,
       CASE WHEN q_games AND fga IS NOT NULL THEN
         rank() OVER (PARTITION BY season, season_type ORDER BY CASE WHEN q_games THEN fga / nullif(gp, 0) END DESC NULLS LAST) END AS rank_fga,
       CASE WHEN (q_games OR q_fg_pct) AND fga > 0 THEN
         rank() OVER (PARTITION BY season, season_type ORDER BY CASE WHEN (q_games OR q_fg_pct) AND fga > 0 THEN fgm / nullif(fga, 0) END DESC NULLS LAST) END AS rank_fg_pct,
       CASE WHEN q_games AND fg3m IS NOT NULL THEN
         rank() OVER (PARTITION BY season, season_type ORDER BY CASE WHEN q_games THEN fg3m / nullif(gp, 0) END DESC NULLS LAST) END AS rank_fg3m,
       CASE WHEN q_games AND fg3a IS NOT NULL THEN
         rank() OVER (PARTITION BY season, season_type ORDER BY CASE WHEN q_games THEN fg3a / nullif(gp, 0) END DESC NULLS LAST) END AS rank_fg3a,
       CASE WHEN q_fg3_pct AND fg3a > 0 THEN
         rank() OVER (PARTITION BY season, season_type ORDER BY CASE WHEN q_fg3_pct AND fg3a > 0 THEN fg3m / nullif(fg3a, 0) END DESC NULLS LAST) END AS rank_fg3_pct,
       CASE WHEN q_games AND ftm IS NOT NULL THEN
         rank() OVER (PARTITION BY season, season_type ORDER BY CASE WHEN q_games THEN ftm / nullif(gp, 0) END DESC NULLS LAST) END AS rank_ftm,
       CASE WHEN q_games AND fta IS NOT NULL THEN
         rank() OVER (PARTITION BY season, season_type ORDER BY CASE WHEN q_games THEN fta / nullif(gp, 0) END DESC NULLS LAST) END AS rank_fta,
       CASE WHEN q_ft_pct AND fta > 0 THEN
         rank() OVER (PARTITION BY season, season_type ORDER BY CASE WHEN q_ft_pct AND fta > 0 THEN ftm / nullif(fta, 0) END DESC NULLS LAST) END AS rank_ft_pct,
       CASE WHEN q_games AND oreb IS NOT NULL THEN
         rank() OVER (PARTITION BY season, season_type ORDER BY CASE WHEN q_games THEN oreb / nullif(gp, 0) END DESC NULLS LAST) END AS rank_oreb,
       CASE WHEN q_games AND dreb IS NOT NULL THEN
         rank() OVER (PARTITION BY season, season_type ORDER BY CASE WHEN q_games THEN dreb / nullif(gp, 0) END DESC NULLS LAST) END AS rank_dreb,
       CASE WHEN (q_games OR q_reb) AND reb IS NOT NULL THEN
         rank() OVER (PARTITION BY season, season_type ORDER BY CASE WHEN (q_games OR q_reb) THEN reb / nullif(gp, 0) END DESC NULLS LAST) END AS rank_reb,
       CASE WHEN (q_games OR q_ast) AND ast IS NOT NULL THEN
         rank() OVER (PARTITION BY season, season_type ORDER BY CASE WHEN (q_games OR q_ast) THEN ast / nullif(gp, 0) END DESC NULLS LAST) END AS rank_ast,
       CASE WHEN (q_games OR q_stl) AND stl IS NOT NULL THEN
         rank() OVER (PARTITION BY season, season_type ORDER BY CASE WHEN (q_games OR q_stl) THEN stl / nullif(gp, 0) END DESC NULLS LAST) END AS rank_stl,
       CASE WHEN (q_games OR q_blk) AND blk IS NOT NULL THEN
         rank() OVER (PARTITION BY season, season_type ORDER BY CASE WHEN (q_games OR q_blk) THEN blk / nullif(gp, 0) END DESC NULLS LAST) END AS rank_blk,
       CASE WHEN q_games AND tov IS NOT NULL THEN
         rank() OVER (PARTITION BY season, season_type ORDER BY CASE WHEN q_games THEN tov / nullif(gp, 0) END DESC NULLS LAST) END AS rank_tov,
       CASE WHEN (q_games OR q_pts) AND pts IS NOT NULL THEN
         rank() OVER (PARTITION BY season, season_type ORDER BY CASE WHEN (q_games OR q_pts) THEN pts / nullif(gp, 0) END DESC NULLS LAST) END AS rank_pts,
       CAST(NULL AS BIGINT) AS rank_eff,
       season_type AS rank_type
FROM season_frame;

-- --------------------------------------------------- analytics_draft_value

CREATE OR REPLACE TABLE analytics_draft_value AS
WITH career AS (
  SELECT player_id,
         sum(gp) AS career_gp,
         sum(pts) AS career_pts,
         sum(pts) / nullif(sum(gp), 0) AS career_ppg,
         sum(reb) / nullif(sum(gp), 0) AS career_rpg,
         sum(ast) / nullif(sum(gp), 0) AS career_apg,
         sum(fgm) / nullif(sum(fga), 0) AS career_fg_pct,
         sum(fg3m) / nullif(sum(fg3a), 0) AS career_fg3_pct,
         count(DISTINCT season_id) AS seasons_played,
         min(season_id) AS first_season,
         max(season_id) AS last_season
  FROM fact_player_career
  WHERE league_id = 'NBA' AND career_type = 'Regular Season'
  GROUP BY 1
)
SELECT TRY_CAST(d.person_id AS BIGINT) AS person_id,
       d.season,
       d.round_number,
       d.round_pick,
       d.overall_pick,
       d.team_id,
       d.player_name,
       p.position,
       p.country,
       CAST(c.career_gp AS BIGINT) AS career_gp,
       c.career_pts,
       c.career_ppg,
       c.career_rpg,
       c.career_apg,
       c.career_fg_pct,
       c.career_fg3_pct,
       CAST(c.seasons_played AS BIGINT) AS seasons_played,
       c.first_season,
       c.last_season
FROM draft_history d
LEFT JOIN career c ON c.player_id = TRY_CAST(d.person_id AS BIGINT)
LEFT JOIN dim_player p ON p.player_id = TRY_CAST(d.person_id AS BIGINT) AND p.is_current;

-- ---------------------------------------------------------------- verify

SELECT 'kobe LAL franchise pts (expect 33643)' AS check_, CAST(pts AS VARCHAR) AS value
FROM fact_franchise_leaders WHERE team_id = 1610612747
UNION ALL
SELECT 'jokic 2022-23 reb/ast rank (expect top 5)',
       CAST(rank_reb AS VARCHAR) || '/' || CAST(rank_ast AS VARCHAR)
FROM fact_player_season_ranks WHERE player_id = 203999 AND season_id = '2022-23' AND rank_type = 'Regular'
UNION ALL
SELECT 'curry 2024-25 pts rank (BBR ~17-18)', CAST(pts_rank AS VARCHAR)
FROM agg_league_leaders WHERE player_id = 201939 AND season_year = '2024-25' AND season_type = 'Regular'
UNION ALL
SELECT 'jordan draft ppg (expect 30.1)', CAST(round(career_ppg, 2) AS VARCHAR)
FROM analytics_draft_value WHERE person_id = 893
UNION ALL
SELECT 'embiid 2022-23 pts rank (expect 1)', CAST(pts_rank AS VARCHAR)
FROM agg_league_leaders WHERE player_id = 203954 AND season_year = '2022-23' AND season_type = 'Regular';
