-- Bulk reconciliation: warehouse (data/nba.duckdb) vs BBR-derived reference
-- CSVs, joined through the crosswalks built by build_crosswalk.sql.
--
-- Run from the repo root (build_crosswalk.sql must have been run first):
--   duckdb -c ".read data/audit/reconcile.sql"
--
-- Outputs (data/audit/out/):
--   recon_player_season.csv       every joined player-season with both sides' stats + diffs
--   recon_player_season_major.csv rows where a counting stat differs by >2 and >2%
--   recon_missing_seasons.csv     player-seasons present on one side only
--   recon_career.csv              career GP/PTS: agg_player_career vs summed seasons vs BBR
--   recon_team_wl.csv             team season W-L: warehouse-derived vs BBR team_summaries
--   recon_awards.csv              major-award winners: warehouse vs BBR shares file
--   recon_draft.csv               draft picks that disagree (round/pick/team)

ATTACH 'data/nba.duckdb' AS wh (READ_ONLY);

CREATE OR REPLACE TEMP TABLE xwalk AS
SELECT nba_player_id, bbr_player_id, full_name, method
FROM read_csv_auto('data/audit/out/player_crosswalk.csv');

CREATE OR REPLACE TEMP TABLE team_xwalk AS
SELECT DISTINCT season, team_id, team_abbreviation, bbr_abbreviation, bbr_team_name, lg
FROM read_csv_auto('data/audit/out/team_crosswalk.csv');

-- ------------------------------------------------ 1. player-season totals

-- Warehouse: one row per (player, season); stints summed. Season keyed by
-- BBR END year. Regular season only (BBR reference has no playoff totals here).
CREATE OR REPLACE TEMP TABLE wh_ps AS
SELECT player_id,
       CAST(substr(season_year, 1, 4) AS INT) + 1 AS season,
       sum(gp)         AS gp,
       sum(total_pts)  AS pts,
       sum(total_ast)  AS ast,
       sum(total_reb)  AS reb,
       sum(total_stl)  AS stl,
       sum(total_blk)  AS blk,
       sum(total_fgm)  AS fgm,
       sum(total_fga)  AS fga,
       sum(total_fg3m) AS fg3m,
       sum(total_ftm)  AS ftm,
       sum(total_min)  AS min
FROM wh.agg_player_season
WHERE season_type = 'Regular'
GROUP BY 1, 2;

CREATE OR REPLACE TEMP TABLE bbr_ps AS
SELECT player_id AS bbr_player_id,
       season,
       sum(TRY_CAST(g   AS DOUBLE)) AS gp,
       sum(TRY_CAST(pts AS DOUBLE)) AS pts,
       sum(TRY_CAST(ast AS DOUBLE)) AS ast,
       sum(TRY_CAST(trb AS DOUBLE)) AS reb,
       sum(TRY_CAST(stl AS DOUBLE)) AS stl,
       sum(TRY_CAST(blk AS DOUBLE)) AS blk,
       sum(TRY_CAST(fg  AS DOUBLE)) AS fgm,
       sum(TRY_CAST(fga AS DOUBLE)) AS fga,
       sum(TRY_CAST(x3p AS DOUBLE)) AS fg3m,
       sum(TRY_CAST(ft  AS DOUBLE)) AS ftm,
       sum(TRY_CAST(mp  AS DOUBLE)) AS min
FROM read_csv_auto('C:/Users/nicolas/Documents/GitHub/basketball-data/csv/nba/player_totals.csv', nullstr='NA', sample_size=-1)
WHERE lg IN ('NBA', 'BAA') AND team NOT LIKE '%TM'
GROUP BY 1, 2;

CREATE OR REPLACE TEMP TABLE ps_joined AS
SELECT x.full_name, x.nba_player_id, x.bbr_player_id, w.season,
       w.gp AS wh_gp,   b.gp AS bbr_gp,
       w.pts AS wh_pts, b.pts AS bbr_pts,
       w.ast AS wh_ast, b.ast AS bbr_ast,
       w.reb AS wh_reb, b.reb AS bbr_reb,
       w.stl AS wh_stl, b.stl AS bbr_stl,
       w.blk AS wh_blk, b.blk AS bbr_blk,
       w.fgm AS wh_fgm, b.fgm AS bbr_fgm,
       w.fga AS wh_fga, b.fga AS bbr_fga,
       w.fg3m AS wh_fg3m, b.fg3m AS bbr_fg3m,
       w.ftm AS wh_ftm, b.ftm AS bbr_ftm
FROM wh_ps w
JOIN xwalk x ON x.nba_player_id = w.player_id
JOIN bbr_ps b ON b.bbr_player_id = x.bbr_player_id AND b.season = w.season;

COPY (SELECT * FROM ps_joined ORDER BY full_name, season)
TO 'data/audit/out/recon_player_season.csv' (HEADER);

-- "Major" = a counting stat off by more than 2 units AND more than 2%.
CREATE OR REPLACE MACRO major(a, b) AS
  a IS NOT NULL AND b IS NOT NULL
  AND abs(a - b) > 2 AND abs(a - b) > 0.02 * greatest(abs(b), 1);

COPY (
  SELECT * FROM ps_joined
  WHERE major(wh_gp, bbr_gp) OR major(wh_pts, bbr_pts) OR major(wh_ast, bbr_ast)
     OR major(wh_reb, bbr_reb) OR major(wh_fgm, bbr_fgm) OR major(wh_ftm, bbr_ftm)
  ORDER BY abs(wh_pts - bbr_pts) DESC NULLS LAST
) TO 'data/audit/out/recon_player_season_major.csv' (HEADER);

-- Season rows that exist on only one side.
COPY (
  SELECT 'wh_only' AS side, x.full_name, x.nba_player_id, x.bbr_player_id, w.season, w.gp, w.pts
  FROM wh_ps w
  JOIN xwalk x ON x.nba_player_id = w.player_id
  LEFT JOIN bbr_ps b ON b.bbr_player_id = x.bbr_player_id AND b.season = w.season
  WHERE b.bbr_player_id IS NULL
  UNION ALL
  SELECT 'bbr_only', x.full_name, x.nba_player_id, x.bbr_player_id, b.season, b.gp, b.pts
  FROM bbr_ps b
  JOIN xwalk x ON x.bbr_player_id = b.bbr_player_id
  LEFT JOIN wh_ps w ON w.player_id = x.nba_player_id AND w.season = b.season
  WHERE w.player_id IS NULL
  ORDER BY full_name, season
) TO 'data/audit/out/recon_missing_seasons.csv' (HEADER);

-- ---------------------------------------- 1b. four-way source comparison

-- Same player-season totals from every available lineage:
--   agg  = wh.agg_player_season (what the app serves today)
--   gl   = wh.fact_player_game_log summed (warehouse's own facts, 1996-97+)
--   nba  = NBA.com-lineage boxscores (playerstatistics.csv, 1946+, direct id join)
--   bbr  = Basketball-Reference lineage (player_totals.csv, via crosswalk)
CREATE OR REPLACE TEMP TABLE wh_gl AS
SELECT player_id,
       CAST(substr(season_year, 1, 4) AS INT) + 1 AS season,
       count(*) AS gp, sum(pts) AS pts, sum(ast) AS ast, sum(reb) AS reb
FROM wh.fact_player_game_log
WHERE season_type = 'Regular'
GROUP BY 1, 2;

CREATE OR REPLACE TEMP TABLE nba_ps AS
SELECT player_id, season, gp, pts, ast, reb
FROM read_parquet('data/audit/out/nba_lineage_player_season.parquet')
WHERE game_type_code = '2';

CREATE OR REPLACE TEMP TABLE fourway AS
SELECT coalesce(a.player_id, g.player_id, n.player_id) AS player_id,
       coalesce(a.season, g.season, n.season) AS season,
       x.full_name,
       a.gp AS agg_gp, g.gp AS gl_gp, n.gp AS nba_gp, b.gp AS bbr_gp,
       a.pts AS agg_pts, g.pts AS gl_pts, n.pts AS nba_pts, b.pts AS bbr_pts,
       a.ast AS agg_ast, g.ast AS gl_ast, n.ast AS nba_ast, b.ast AS bbr_ast,
       a.reb AS agg_reb, g.reb AS gl_reb, n.reb AS nba_reb, b.reb AS bbr_reb
FROM wh_ps a
FULL OUTER JOIN wh_gl g ON g.player_id = a.player_id AND g.season = a.season
FULL OUTER JOIN nba_ps n ON n.player_id = coalesce(a.player_id, g.player_id)
                         AND n.season = coalesce(a.season, g.season)
LEFT JOIN xwalk x ON x.nba_player_id = coalesce(a.player_id, g.player_id, n.player_id)
LEFT JOIN bbr_ps b ON b.bbr_player_id = x.bbr_player_id
                   AND b.season = coalesce(a.season, g.season, n.season);

COPY (SELECT * FROM fourway ORDER BY full_name, season)
TO 'data/audit/out/recon_fourway.csv' (HEADER);

SELECT '--- four-way agreement (player-season, regular) ---' AS section;
SELECT
  count(*) AS rows_total,
  count(*) FILTER (agg_pts IS NOT NULL AND bbr_pts IS NOT NULL)               AS agg_bbr_joined,
  count(*) FILTER (agg_pts = bbr_pts)                                          AS agg_eq_bbr,
  count(*) FILTER (gl_pts IS NOT NULL AND bbr_pts IS NOT NULL)                 AS gl_bbr_joined,
  count(*) FILTER (gl_pts = bbr_pts)                                           AS gl_eq_bbr,
  count(*) FILTER (nba_pts IS NOT NULL AND bbr_pts IS NOT NULL)                AS nba_bbr_joined,
  count(*) FILTER (nba_pts = bbr_pts)                                          AS nba_eq_bbr,
  count(*) FILTER (agg_pts IS NOT NULL AND gl_pts IS NOT NULL)                 AS agg_gl_joined,
  count(*) FILTER (agg_pts = gl_pts)                                           AS agg_eq_gl
FROM fourway;

SELECT '--- same, GP ---' AS section;
SELECT
  count(*) FILTER (agg_gp = bbr_gp)  AS agg_eq_bbr_gp,
  count(*) FILTER (gl_gp = bbr_gp)   AS gl_eq_bbr_gp,
  count(*) FILTER (nba_gp = bbr_gp)  AS nba_eq_bbr_gp,
  count(*) FILTER (agg_gp = gl_gp)   AS agg_eq_gl_gp
FROM fourway;

-- --------------------------------------------------------- 2. career GP/PTS

COPY (
  WITH summed AS (
    SELECT player_id, sum(gp) AS sum_gp, sum(pts) AS sum_pts
    FROM wh_ps GROUP BY 1
  ),
  bbr_car AS (
    SELECT bbr_player_id, sum(gp) AS bbr_gp, sum(pts) AS bbr_pts
    FROM bbr_ps GROUP BY 1
  )
  SELECT x.full_name, x.nba_player_id, x.bbr_player_id,
         c.career_gp  AS agg_career_gp,  s.sum_gp,  b.bbr_gp,
         c.career_pts AS agg_career_pts, s.sum_pts, b.bbr_pts,
         (c.career_gp  IS DISTINCT FROM s.sum_gp)  AS career_vs_sum_gp_mismatch,
         (s.sum_gp     IS DISTINCT FROM b.bbr_gp)  AS sum_vs_bbr_gp_mismatch
  FROM xwalk x
  LEFT JOIN wh.agg_player_career c ON c.player_id = x.nba_player_id
  LEFT JOIN summed s ON s.player_id = x.nba_player_id
  LEFT JOIN bbr_car b ON b.bbr_player_id = x.bbr_player_id
  ORDER BY abs(coalesce(s.sum_gp, 0) - coalesce(b.bbr_gp, 0)) DESC
) TO 'data/audit/out/recon_career.csv' (HEADER);

-- --------------------------------------------------------- 3. team W-L

-- Warehouse W-L derived from the game table (regular season), the same way
-- queries.ts re-derives playoff series (fact_playoff_series is known-bad).
COPY (
  WITH wh_games AS (
    SELECT CAST(substr(season_id, 2) AS INT) + 1 AS season,
           team_id_home AS team_id,
           CASE wl_home WHEN 'W' THEN 1 ELSE 0 END AS w,
           CASE wl_home WHEN 'L' THEN 1 ELSE 0 END AS l
    FROM wh.game WHERE season_type = 'Regular Season'
    UNION ALL
    SELECT CAST(substr(season_id, 2) AS INT) + 1,
           team_id_away,
           CASE wl_away WHEN 'W' THEN 1 ELSE 0 END,
           CASE wl_away WHEN 'L' THEN 1 ELSE 0 END
    FROM wh.game WHERE season_type = 'Regular Season'
  ),
  wh_wl AS (
    SELECT season, team_id, sum(w) AS wh_w, sum(l) AS wh_l, count(*) AS wh_g
    FROM wh_games GROUP BY 1, 2
  ),
  bbr_wl AS (
    SELECT season, abbreviation, team, w AS bbr_w, l AS bbr_l
    FROM read_csv_auto('C:/Users/nicolas/Documents/GitHub/basketball-data/csv/nba/team_summaries.csv', nullstr='NA', sample_size=-1)
    WHERE lg IN ('NBA', 'BAA') AND team <> 'League Average'
  )
  SELECT t.season, t.bbr_team_name, t.team_abbreviation, t.bbr_abbreviation,
         w.wh_w, b.bbr_w, w.wh_l, b.bbr_l,
         (w.wh_w IS DISTINCT FROM b.bbr_w OR w.wh_l IS DISTINCT FROM b.bbr_l) AS mismatch
  FROM team_xwalk t
  LEFT JOIN wh_wl w ON w.season = t.season AND w.team_id = t.team_id
  LEFT JOIN bbr_wl b ON b.season = t.season AND b.abbreviation = t.bbr_abbreviation
  ORDER BY mismatch DESC, t.season, t.bbr_team_name
) TO 'data/audit/out/recon_team_wl.csv' (HEADER);

-- --------------------------------------------------------- 4. awards

COPY (
  WITH wh_win AS (
    SELECT CAST(season AS INT) AS season, award_type, player_id
    FROM wh.fact_player_awards
    WHERE award_type IN ('nba mvp', 'nba roy', 'nba dpoy', 'nba mip', 'nba smoy')
      AND subtype1 = 'Selected'
  ),
  bbr_win AS (
    SELECT season, award, player_id AS bbr_player_id, player
    FROM read_csv_auto('C:/Users/nicolas/Documents/GitHub/basketball-data/csv/nba/player_award_shares.csv', nullstr='NA', sample_size=-1)
    WHERE winner AND award IN ('nba mvp', 'nba roy', 'nba dpoy', 'nba mip', 'nba smoy')
  )
  -- Compare winner SETS per (season, award): shared awards (1971/1995/2000
  -- ROY etc.) have two legitimate winners and must not cross-join as errors.
  WITH wh_sets AS (
    SELECT w.season, w.award_type AS award,
           list_sort(list(coalesce(xw.bbr_player_id, CAST(w.player_id AS VARCHAR)))) AS wh_ids,
           list_sort(list(coalesce(xw.full_name, CAST(w.player_id AS VARCHAR))))     AS wh_names
    FROM wh_win w
    LEFT JOIN xwalk xw ON xw.nba_player_id = w.player_id
    GROUP BY 1, 2
  ),
  bbr_sets AS (
    SELECT season, award,
           list_sort(list(bbr_player_id)) AS bbr_ids,
           list_sort(list(player))        AS bbr_names
    FROM bbr_win GROUP BY 1, 2
  )
  SELECT coalesce(b.season, w.season) AS season,
         coalesce(b.award, w.award) AS award,
         w.wh_names AS wh_winner,
         b.bbr_names AS bbr_winner,
         (w.wh_ids IS DISTINCT FROM b.bbr_ids) AS mismatch
  FROM bbr_sets b
  FULL OUTER JOIN wh_sets w ON w.season = b.season AND w.award = b.award
  ORDER BY mismatch DESC, season DESC, award
) TO 'data/audit/out/recon_awards.csv' (HEADER);

-- --------------------------------------------------------- 5. draft

COPY (
  WITH wh_draft AS (
    SELECT TRY_CAST(person_id AS BIGINT) AS player_id,
           player_name,
           TRY_CAST(season AS INT) + 0 AS draft_year,
           TRY_CAST(round_number AS INT) AS round,
           TRY_CAST(overall_pick AS INT) AS overall_pick
    FROM wh.draft_history
  ),
  bbr_draft AS (
    SELECT season AS draft_year, TRY_CAST(round AS INT) AS round,
           TRY_CAST(overall_pick AS INT) AS overall_pick, player, player_id AS bbr_player_id
    FROM read_csv_auto('C:/Users/nicolas/Documents/GitHub/basketball-data/csv/nba/draft_pick_history.csv', nullstr='NA', sample_size=-1)
    WHERE lg = 'NBA'
  )
  SELECT w.draft_year, w.player_name, b.player AS bbr_player,
         w.round AS wh_round, b.round AS bbr_round,
         w.overall_pick AS wh_pick, b.overall_pick AS bbr_pick
  FROM wh_draft w
  JOIN xwalk x ON x.nba_player_id = w.player_id
  JOIN bbr_draft b ON b.bbr_player_id = x.bbr_player_id AND b.draft_year = w.draft_year
  WHERE w.overall_pick IS DISTINCT FROM b.overall_pick
     OR w.round IS DISTINCT FROM b.round
  ORDER BY w.draft_year DESC
) TO 'data/audit/out/recon_draft.csv' (HEADER);

-- --------------------------------------------------------- summary

SELECT 'player-seasons joined' AS metric, count(*) AS n FROM ps_joined
UNION ALL SELECT 'gp exact match', count(*) FROM ps_joined WHERE wh_gp = bbr_gp
UNION ALL SELECT 'pts exact match', count(*) FROM ps_joined WHERE wh_pts = bbr_pts
UNION ALL SELECT 'pts within 1%', count(*) FROM ps_joined WHERE abs(wh_pts - bbr_pts) <= 0.01 * greatest(bbr_pts, 1)
UNION ALL SELECT 'major rows', (SELECT count(*) FROM ps_joined
  WHERE major(wh_gp, bbr_gp) OR major(wh_pts, bbr_pts) OR major(wh_ast, bbr_ast)
     OR major(wh_reb, bbr_reb) OR major(wh_fgm, bbr_fgm) OR major(wh_ftm, bbr_ftm))
ORDER BY metric;
