-- Rebuild the corrupted/incomplete curated layer in-place from the clean
-- sources imported by import_source_tables.sql, keeping table names and
-- column shapes identical so web/server/queries.ts keeps working unchanged.
--
-- Fixes (see docs/data-quality-audit.md):
--   #1 agg_player_season / _per36 / _per48 / _advanced / agg_player_career
--      were inflated by the franchise-era fan-out -> rebuilt from
--      fact_player_season_stat_resolved (BBR, all eras) + fact_player_game_log
--      (Cup rows) + fact_player_game_advanced (NBA tracking metrics).
--   #3 fact_player_awards dropped diacritic players -> rebuilt from the
--      stg_bref_* award tables via the crosswalk (id-based, lossless).
--   #4 fact_standings counted play-in games / stale 2023-24 snapshot ->
--      W-L healed from stg_bref_team_summaries, ranks/games-back recomputed
--      for the affected seasons.
--   #5 draft_history.overall_pick skipped forfeited slots -> healed from
--      stg_bref_draft_pick_history.
--
-- Run from the repo root with the dev server STOPPED:
--   duckdb data/nba.duckdb -c ".read data/audit/rebuild_curated_layer.sql"
-- Idempotent. Originals are kept as *_legacy_fanout on first run.

CREATE OR REPLACE MACRO end_year_to_season(y) AS
  CAST(y - 1 AS VARCHAR) || '-' || lpad(CAST(y % 100 AS VARCHAR), 2, '0');

-- The source's own player resolution dropped diacritic names (Doncic's
-- person_id is NULL in fact_player_season_stat_resolved) — recover those
-- rows through our crosswalk (bridge_player_bbr), deduped to one warehouse
-- id per BBR player (duplicate-identity ids rank by game-log volume).
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

CREATE OR REPLACE TEMP VIEW resolved_with_fallback AS
SELECT coalesce(r.person_id, x.nba_player_id) AS resolved_player_id, r.*
FROM fact_player_season_stat_resolved r
LEFT JOIN pxw x ON r.person_id IS NULL AND x.bbr_player_id = r.slug;

-- Keep the corrupt originals around for forensics (no-op on re-run).
CREATE TABLE IF NOT EXISTS agg_player_season_legacy_fanout AS SELECT * FROM agg_player_season;
CREATE TABLE IF NOT EXISTS agg_player_career_legacy_fanout AS SELECT * FROM agg_player_career;
CREATE TABLE IF NOT EXISTS agg_player_season_advanced_legacy_fanout AS SELECT * FROM agg_player_season_advanced;
CREATE TABLE IF NOT EXISTS fact_player_awards_legacy_names AS SELECT * FROM fact_player_awards;

-- ------------------------------------------------------- agg_player_season

CREATE OR REPLACE TABLE agg_player_season AS
WITH bref AS (
  SELECT resolved_player_id AS player_id,
         team_id,
         team_abbrev AS team_abbreviation,
         end_year_to_season(season) AS season_year,
         CASE WHEN is_playoffs THEN 'Playoffs' ELSE 'Regular' END AS season_type,
         CAST(gp AS BIGINT) AS gp,
         CAST(min AS DOUBLE) AS total_min,
         min / nullif(gp, 0)  AS avg_min,
         CAST(pts AS DOUBLE) AS total_pts, pts / nullif(gp, 0) AS avg_pts,
         -- BBR carries either total rebounds (pre-1974) or the orb/drb split
         CAST(coalesce(trb, orb + drb) AS DOUBLE) AS total_reb,
         coalesce(trb, orb + drb) / nullif(gp, 0) AS avg_reb,
         CAST(ast AS DOUBLE) AS total_ast, ast / nullif(gp, 0) AS avg_ast,
         CAST(stl AS DOUBLE) AS total_stl, stl / nullif(gp, 0) AS avg_stl,
         CAST(blk AS DOUBLE) AS total_blk, blk / nullif(gp, 0) AS avg_blk,
         CAST(tov AS DOUBLE) AS total_tov, tov / nullif(gp, 0) AS avg_tov,
         CAST(fg AS DOUBLE)  AS total_fgm, CAST(fga AS DOUBLE) AS total_fga,
         fg / nullif(fga, 0) AS fg_pct,
         CAST(tp AS DOUBLE)  AS total_fg3m, CAST(tpa AS DOUBLE) AS total_fg3a,
         tp / nullif(tpa, 0) AS fg3_pct,
         CAST(ft AS DOUBLE)  AS total_ftm, CAST(fta AS DOUBLE) AS total_fta,
         ft / nullif(fta, 0) AS ft_pct,
         ortg AS avg_off_rating,
         drtg AS avg_def_rating,
         ortg - drtg AS avg_net_rating,
         pts / nullif(2 * (fga + 0.44 * fta), 0) AS avg_ts_pct,
         usgp / 100.0 AS avg_usg_pct,
         CAST(NULL AS DOUBLE) AS avg_pie
  FROM resolved_with_fallback
  WHERE resolved_player_id IS NOT NULL
),
-- NBA-Cup rows (BBR folds Cup group games into the regular season; the app
-- additionally shows the Cup line separately, sourced from the clean game log)
cup AS (
  SELECT player_id, team_id,
         max(team_abbreviation) AS team_abbreviation,
         season_year, season_type,
         count(*) AS gp,
         sum(min) AS total_min, avg(min) AS avg_min,
         sum(pts) AS total_pts, avg(pts) AS avg_pts,
         sum(reb) AS total_reb, avg(reb) AS avg_reb,
         sum(ast) AS total_ast, avg(ast) AS avg_ast,
         sum(stl) AS total_stl, avg(stl) AS avg_stl,
         sum(blk) AS total_blk, avg(blk) AS avg_blk,
         sum(tov) AS total_tov, avg(tov) AS avg_tov,
         sum(fgm) AS total_fgm, sum(fga) AS total_fga,
         sum(fgm) / nullif(sum(fga), 0) AS fg_pct,
         sum(fg3m) AS total_fg3m, sum(fg3a) AS total_fg3a,
         sum(fg3m) / nullif(sum(fg3a), 0) AS fg3_pct,
         sum(ftm) AS total_ftm, sum(fta) AS total_fta,
         sum(ftm) / nullif(sum(fta), 0) AS ft_pct,
         CAST(NULL AS DOUBLE) AS avg_off_rating,
         CAST(NULL AS DOUBLE) AS avg_def_rating,
         CAST(NULL AS DOUBLE) AS avg_net_rating,
         sum(pts) / nullif(2 * (sum(fga) + 0.44 * sum(fta)), 0) AS avg_ts_pct,
         CAST(NULL AS DOUBLE) AS avg_usg_pct,
         CAST(NULL AS DOUBLE) AS avg_pie
  FROM fact_player_game_log
  WHERE season_type = 'Cup'
  GROUP BY player_id, team_id, season_year, season_type
)
SELECT * FROM bref UNION ALL SELECT * FROM cup;

-- ------------------------------------------------------- agg_player_career

CREATE OR REPLACE TABLE agg_player_career AS
SELECT player_id,
       CAST(sum(gp) AS BIGINT) AS career_gp,
       sum(total_min) AS career_min,
       sum(total_pts) AS career_pts,
       sum(total_pts) / nullif(sum(gp), 0) AS career_ppg,
       sum(total_reb) / nullif(sum(gp), 0) AS career_rpg,
       sum(total_ast) / nullif(sum(gp), 0) AS career_apg,
       sum(total_stl) / nullif(sum(gp), 0) AS career_spg,
       sum(total_blk) / nullif(sum(gp), 0) AS career_bpg,
       sum(total_fgm) / nullif(sum(total_fga), 0) AS career_fg_pct,
       sum(total_fg3m) / nullif(sum(total_fg3a), 0) AS career_fg3_pct,
       sum(total_ftm) / nullif(sum(total_fta), 0) AS career_ft_pct,
       min(season_year) AS first_season,
       max(season_year) AS last_season,
       CAST(count(DISTINCT season_year) AS BIGINT) AS seasons_played
FROM agg_player_season
WHERE season_type = 'Regular'
GROUP BY player_id;

-- ------------------------------------------- per-36 / per-48 rate tables

CREATE OR REPLACE TABLE agg_player_season_per36 AS
SELECT player_id, team_id, season_year, season_type, gp, avg_min,
       total_pts * 36 / nullif(total_min, 0) AS pts_per36,
       total_reb * 36 / nullif(total_min, 0) AS reb_per36,
       total_ast * 36 / nullif(total_min, 0) AS ast_per36,
       total_stl * 36 / nullif(total_min, 0) AS stl_per36,
       total_blk * 36 / nullif(total_min, 0) AS blk_per36,
       total_tov * 36 / nullif(total_min, 0) AS tov_per36
FROM agg_player_season;

CREATE OR REPLACE TABLE agg_player_season_per48 AS
SELECT player_id, team_id, season_year, season_type, gp, avg_min,
       total_pts * 48 / nullif(total_min, 0) AS pts_per48,
       total_reb * 48 / nullif(total_min, 0) AS reb_per48,
       total_ast * 48 / nullif(total_min, 0) AS ast_per48,
       total_stl * 48 / nullif(total_min, 0) AS stl_per48,
       total_blk * 48 / nullif(total_min, 0) AS blk_per48,
       total_tov * 48 / nullif(total_min, 0) AS tov_per48
FROM agg_player_season;

-- --------------------------------------------- agg_player_season_advanced
-- NBA tracking metrics (fractions) possession-weighted from the clean
-- per-game advanced facts (1996-97+); BBR-derived rows fill earlier eras.
-- New columns appended (per/ows/dws/ws/obpm/dbpm/bpm/vorp) flow through
-- getPlayerAdvancedStats's SELECT s.* automatically.

CREATE OR REPLACE TABLE agg_player_season_advanced AS
WITH nba AS (
  SELECT a.player_id, a.team_id, a.season_year, gl.season_type,
         count(*) AS gp,
         sum(a.off_rating * a.poss) / nullif(sum(a.poss), 0) AS avg_off_rating,
         sum(a.def_rating * a.poss) / nullif(sum(a.poss), 0) AS avg_def_rating,
         sum(a.net_rating * a.poss) / nullif(sum(a.poss), 0) AS avg_net_rating,
         sum(a.ts_pct  * a.poss) / nullif(sum(a.poss), 0) AS avg_ts_pct,
         sum(a.usg_pct * a.poss) / nullif(sum(a.poss), 0) AS avg_usg_pct,
         sum(a.efg_pct * a.poss) / nullif(sum(a.poss), 0) AS avg_efg_pct,
         sum(a.ast_pct * a.poss) / nullif(sum(a.poss), 0) AS avg_ast_pct,
         sum(a.ast_ratio * a.poss) / nullif(sum(a.poss), 0) AS avg_ast_ratio,
         sum(a.oreb_pct * a.poss) / nullif(sum(a.poss), 0) AS avg_oreb_pct,
         sum(a.dreb_pct * a.poss) / nullif(sum(a.poss), 0) AS avg_dreb_pct,
         sum(a.reb_pct  * a.poss) / nullif(sum(a.poss), 0) AS avg_reb_pct,
         CAST(NULL AS DOUBLE) AS avg_tov_pct,   -- no per-game TOV% in the NBA facts; BBR side supplies it
         sum(a.pace * a.poss) / nullif(sum(a.poss), 0) AS avg_pace,
         sum(a.pie  * a.poss) / nullif(sum(a.poss), 0) AS avg_pie
  FROM fact_player_game_advanced a
  JOIN fact_player_game_log gl
    ON gl.game_id = a.game_id AND gl.player_id = a.player_id
  GROUP BY a.player_id, a.team_id, a.season_year, gl.season_type
),
bref AS (
  SELECT resolved_player_id AS player_id, team_id,
         end_year_to_season(season) AS season_year,
         CASE WHEN is_playoffs THEN 'Playoffs' ELSE 'Regular' END AS season_type,
         CAST(gp AS BIGINT) AS gp,
         ortg AS avg_off_rating, drtg AS avg_def_rating,
         ortg - drtg AS avg_net_rating,
         pts / nullif(2 * (fga + 0.44 * fta), 0) AS avg_ts_pct,
         usgp / 100.0 AS avg_usg_pct,
         (fg + 0.5 * tp) / nullif(fga, 0) AS avg_efg_pct,
         astp / 100.0 AS avg_ast_pct,
         orbp / 100.0 AS avg_oreb_pct,
         drbp / 100.0 AS avg_dreb_pct,
         trbp / 100.0 AS avg_reb_pct,
         tovp / 100.0 AS avg_tov_pct,
         per, ows, dws, ows + dws AS ws,
         obpm, dbpm, obpm + dbpm AS bpm, vorp
  FROM resolved_with_fallback
  WHERE resolved_player_id IS NOT NULL
)
SELECT coalesce(n.player_id, b.player_id) AS player_id,
       coalesce(n.team_id, b.team_id) AS team_id,
       coalesce(n.season_year, b.season_year) AS season_year,
       coalesce(n.season_type, b.season_type) AS season_type,
       coalesce(n.gp, b.gp) AS gp,
       coalesce(n.avg_off_rating, b.avg_off_rating) AS avg_off_rating,
       coalesce(n.avg_def_rating, b.avg_def_rating) AS avg_def_rating,
       coalesce(n.avg_net_rating, b.avg_net_rating) AS avg_net_rating,
       coalesce(n.avg_ts_pct, b.avg_ts_pct) AS avg_ts_pct,
       coalesce(n.avg_usg_pct, b.avg_usg_pct) AS avg_usg_pct,
       coalesce(n.avg_efg_pct, b.avg_efg_pct) AS avg_efg_pct,
       coalesce(n.avg_ast_pct, b.avg_ast_pct) AS avg_ast_pct,
       n.avg_ast_ratio,
       coalesce(n.avg_oreb_pct, b.avg_oreb_pct) AS avg_oreb_pct,
       coalesce(n.avg_dreb_pct, b.avg_dreb_pct) AS avg_dreb_pct,
       coalesce(n.avg_reb_pct, b.avg_reb_pct) AS avg_reb_pct,
       coalesce(n.avg_tov_pct, b.avg_tov_pct) AS avg_tov_pct,
       n.avg_pace,
       n.avg_pie,
       b.per, b.ows, b.dws, b.ws, b.obpm, b.dbpm, b.bpm, b.vorp
FROM nba n
FULL OUTER JOIN bref b
  ON b.player_id = n.player_id
 AND b.team_id IS NOT DISTINCT FROM n.team_id
 AND b.season_year = n.season_year
 AND b.season_type = n.season_type;

-- --------------------------------------------------- fact_player_awards
-- Same 11-column shape and award_type/subtype1 vocabulary the app filters
-- on; rebuilt losslessly from the BBR layer via the crosswalk.

CREATE OR REPLACE TABLE fact_player_awards AS
-- major-award voting records; winners flagged subtype1 = 'Selected'
SELECT s.nba_player_id AS player_id,
       s.award || CASE WHEN s.winner THEN ' winner' ELSE ' (received votes)' END AS description,
       CAST(NULL AS BIGINT) AS all_nba_team_number,
       CAST(s.season AS VARCHAR) AS season,
       CAST(NULL AS VARCHAR) AS month,
       CAST(NULL AS VARCHAR) AS week,
       CAST(NULL AS VARCHAR) AS conference,
       s.award AS award_type,
       CASE WHEN s.winner THEN 'Selected' END AS subtype1,
       CAST(s.share AS VARCHAR) AS subtype2,
       CAST(NULL AS VARCHAR) AS subtype3
FROM stg_bref_player_award_shares s
WHERE s.nba_player_id IS NOT NULL AND s.award LIKE 'nba %'
UNION ALL
-- end-of-season teams (every row is a real selection)
SELECT t.nba_player_id,
       t.type || ' ' || t.number_tm || ' Team',
       CASE t.number_tm WHEN '1st' THEN 1 WHEN '2nd' THEN 2 WHEN '3rd' THEN 3 END,
       CAST(t.season AS VARCHAR),
       NULL, NULL, NULL,
       t.type,
       NULL, NULL, NULL
FROM stg_bref_end_of_season_teams t
WHERE t.nba_player_id IS NOT NULL AND t.lg = 'NBA'
  AND t.type IN ('All-NBA', 'All-Defense', 'All-Rookie')
UNION ALL
-- All-Star selections
SELECT a.nba_player_id,
       'NBA All-Star',
       NULL,
       CAST(a.season AS VARCHAR),
       NULL, NULL, NULL,
       'All-Star',
       NULL, NULL, NULL
FROM stg_bref_all_star_selections a
WHERE a.nba_player_id IS NOT NULL AND a.lg = 'NBA';

-- ------------------------------------------------------- fact_standings
-- Heal W-L from BBR official records (play-in pollution + stale snapshots),
-- then recompute rank/games-back for the affected seasons only.

UPDATE fact_standings s
SET wins = b.w, losses = b.l,
    win_pct = round(b.w / CAST(b.w + b.l AS DOUBLE), 3)
FROM (
  SELECT nba_team_id, end_year_to_season(season) AS season_year, w, l
  FROM stg_bref_team_summaries
  WHERE nba_team_id IS NOT NULL AND lg IN ('NBA', 'BAA') AND team <> 'League Average'
) b
WHERE s.team_id = b.nba_team_id
  AND s.season_year = b.season_year
  AND s.season_type = 'Regular'
  AND (s.wins <> b.w OR s.losses <> b.l);

UPDATE fact_standings s
SET conf_rank = r.new_conf_rank,
    div_rank = r.new_div_rank,
    games_back = r.new_gb
FROM (
  SELECT team_id, season_year, season_type,
         row_number() OVER (PARTITION BY season_year, conference ORDER BY win_pct DESC, wins DESC) AS new_conf_rank,
         row_number() OVER (PARTITION BY season_year, division ORDER BY win_pct DESC, wins DESC) AS new_div_rank,
         ((max(wins) OVER (PARTITION BY season_year, conference)) - wins
          + losses - (min(losses) OVER (PARTITION BY season_year, conference))) / 2.0 AS new_gb
  FROM fact_standings
  WHERE season_type = 'Regular'
    AND season_year IN ('2020-21', '2021-22', '2022-23', '2023-24', '2024-25', '2025-26')
) r
WHERE s.team_id = r.team_id AND s.season_year = r.season_year AND s.season_type = 'Regular'
  AND s.season_year IN ('2020-21', '2021-22', '2022-23', '2023-24', '2024-25', '2025-26');

-- --------------------------------------------------------- draft_history

UPDATE draft_history d
SET overall_pick = CAST(b.overall_pick AS BIGINT)
FROM (
  SELECT nba_player_id, season, overall_pick
  FROM stg_bref_draft_pick_history
  WHERE nba_player_id IS NOT NULL AND TRY_CAST(overall_pick AS INT) IS NOT NULL
) b
WHERE TRY_CAST(d.person_id AS BIGINT) = b.nba_player_id
  AND TRY_CAST(d.season AS INT) = b.season
  AND d.overall_pick <> CAST(b.overall_pick AS BIGINT);

-- ---------------------------------------------------------------- verify

SELECT 'harden 2020-21 reg gp/pts (expect 44/1083)' AS check_,
       CAST(sum(gp) AS VARCHAR) || '/' || CAST(CAST(sum(total_pts) AS INT) AS VARCHAR) AS value
FROM agg_player_season WHERE player_id = 201935 AND season_year = '2020-21' AND season_type = 'Regular'
UNION ALL
SELECT 'unseld career gp (expect 984)', CAST(c.career_gp AS VARCHAR)
FROM agg_player_career c JOIN dim_player p ON p.player_id = c.player_id AND p.is_current
WHERE p.full_name = 'Wes Unseld'
UNION ALL
SELECT 'luka roy 2019 rows (expect 1)', CAST(count(*) AS VARCHAR)
FROM fact_player_awards WHERE player_id = 1629029 AND award_type = 'nba roy' AND subtype1 = 'Selected'
UNION ALL
SELECT 'luka all-nba rows (expect 5+)', CAST(count(*) AS VARCHAR)
FROM fact_player_awards WHERE player_id = 1629029 AND award_type = 'All-NBA'
UNION ALL
SELECT 'atl 2021-22 w-l (expect 43-39)', CAST(wins AS VARCHAR) || '-' || CAST(losses AS VARCHAR)
FROM fact_standings WHERE season_year = '2021-22' AND season_type = 'Regular' AND team_id = 1610612737
UNION ALL
SELECT 'boozer 2002 pick (expect 35)', CAST(overall_pick AS VARCHAR)
FROM draft_history WHERE player_name = 'Carlos Boozer' AND season = '2002';
