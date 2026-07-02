-- Reconciliation, part 2: remaining app-served surfaces.
--   duckdb -c ".read data/audit/reconcile2.sql"
-- Outputs (data/audit/out/):
--   recon_standings.csv        fact_standings W-L vs BBR team_summaries (all seasons)
--   recon_game_scores.csv      warehouse game table vs NBA-lineage Games.csv (scores)
--   recon_game_coverage.csv    seasons where either side is missing games
--   recon_allstar.csv          All-Star selections wh vs BBR
--   recon_allnba.csv           All-NBA/All-Defense/All-Rookie teams wh vs BBR
--   recon_team_season_stats.csv agg_team_season per-game averages vs BBR team_totals
--   recon_player_bio.csv       dim_player height/weight/birthdate vs NBA players.csv

ATTACH 'data/nba.duckdb' AS wh (READ_ONLY);

CREATE OR REPLACE TEMP TABLE xwalk AS
SELECT nba_player_id, bbr_player_id, full_name FROM read_csv_auto('data/audit/out/player_crosswalk.csv');
CREATE OR REPLACE TEMP TABLE team_xwalk AS
SELECT DISTINCT season, team_id, team_abbreviation, bbr_abbreviation, bbr_team_name
FROM read_csv_auto('data/audit/out/team_crosswalk.csv');

-- ------------------------------------------------ standings vs BBR
COPY (
  WITH st AS (
    SELECT CAST(substr(season_year,1,4) AS INT)+1 AS season, team_id,
           CAST(wins AS INT) AS wh_w, CAST(losses AS INT) AS wh_l
    FROM wh.fact_standings WHERE season_type IN ('Regular','Regular Season')
  ),
  bbr AS (
    SELECT season, abbreviation, w AS bbr_w, l AS bbr_l
    FROM read_csv_auto('C:/Users/nicolas/Documents/GitHub/basketball-data/csv/nba/team_summaries.csv', nullstr='NA', sample_size=-1)
    WHERE lg IN ('NBA','BAA') AND team <> 'League Average'
  )
  SELECT t.season AS season, t.bbr_team_name, s.wh_w, b.bbr_w, s.wh_l, b.bbr_l,
         (s.wh_w IS DISTINCT FROM b.bbr_w OR s.wh_l IS DISTINCT FROM b.bbr_l) AS mismatch
  FROM team_xwalk t
  JOIN st s ON s.season = t.season AND s.team_id = t.team_id
  LEFT JOIN bbr b ON b.season = t.season AND b.abbreviation = t.bbr_abbreviation
  ORDER BY mismatch DESC, season, bbr_team_name
) TO 'data/audit/out/recon_standings.csv' (HEADER);

-- ------------------------------------------------ game scores vs NBA lineage
CREATE OR REPLACE TEMP TABLE nba_games AS
SELECT CAST(gameId AS VARCHAR) AS game_id,
       TRY_CAST(hometeamId AS BIGINT) AS home_team_id,
       TRY_CAST(awayteamId AS BIGINT) AS away_team_id,
       TRY_CAST(homeScore AS INT) AS home_score,
       TRY_CAST(awayScore AS INT) AS away_score
FROM read_csv('C:/Users/nicolas/Documents/GitHub/basketball-data/csv/nba/games.csv',
              strict_mode=false, sample_size=-1, types={'gameId':'VARCHAR'})
WHERE substr(CAST(gameId AS VARCHAR),1,1) IN ('2','4') AND length(CAST(gameId AS VARCHAR)) = 8;

CREATE OR REPLACE TEMP TABLE wh_games AS
SELECT game_id, team_id_home, team_id_away, pts_home, pts_away, season_id, season_type
FROM wh.game WHERE season_type IN ('Regular Season','Playoffs');

COPY (
  SELECT w.game_id, w.season_id, w.season_type,
         w.pts_home, n.home_score, w.pts_away, n.away_score
  FROM wh_games w
  JOIN nba_games n USING (game_id)
  WHERE w.pts_home IS DISTINCT FROM n.home_score
     OR w.pts_away IS DISTINCT FROM n.away_score
  ORDER BY w.game_id
) TO 'data/audit/out/recon_game_scores.csv' (HEADER);

COPY (
  WITH w AS (SELECT substr(game_id,2,2) AS yy, substr(game_id,1,1) AS t, count(*) AS wh_n
             FROM wh_games GROUP BY 1,2),
       n AS (SELECT substr(game_id,2,2) AS yy, substr(game_id,1,1) AS t, count(*) AS nba_n
             FROM nba_games GROUP BY 1,2)
  SELECT coalesce(w.yy, n.yy) AS yy, coalesce(w.t, n.t) AS game_type,
         w.wh_n, n.nba_n
  FROM w FULL OUTER JOIN n ON w.yy = n.yy AND w.t = n.t
  WHERE coalesce(w.wh_n,0) <> coalesce(n.nba_n,0)
  ORDER BY 1, 2
) TO 'data/audit/out/recon_game_coverage.csv' (HEADER);

-- ------------------------------------------------ All-Star selections
COPY (
  WITH wh_as AS (
    SELECT CAST(season AS INT) AS season, player_id FROM wh.fact_player_awards
    WHERE award_type = 'All-Star' GROUP BY 1, 2
  ),
  bbr_as AS (
    SELECT season, player_id AS bbr_player_id, player
    FROM read_csv_auto('C:/Users/nicolas/Documents/GitHub/basketball-data/csv/nba/all-star_selections.csv', nullstr='NA', sample_size=-1)
    WHERE lg = 'NBA'
  )
  SELECT 'wh_only' AS side, w.season, x.full_name AS player
  FROM wh_as w LEFT JOIN xwalk x ON x.nba_player_id = w.player_id
  LEFT JOIN bbr_as b ON b.season = w.season AND b.bbr_player_id = x.bbr_player_id
  WHERE b.bbr_player_id IS NULL
  UNION ALL
  SELECT 'bbr_only', b.season, b.player
  FROM bbr_as b LEFT JOIN xwalk x ON x.bbr_player_id = b.bbr_player_id
  LEFT JOIN wh_as w ON w.season = b.season AND w.player_id = x.nba_player_id
  WHERE w.player_id IS NULL
  ORDER BY season, side, player
) TO 'data/audit/out/recon_allstar.csv' (HEADER);

-- ------------------------------------------------ All-NBA / All-Defense / All-Rookie
COPY (
  WITH wh_teams AS (
    SELECT CAST(season AS INT) AS season, player_id,
           CASE award_type WHEN 'All-NBA' THEN 'All-NBA'
                           WHEN 'All-Defense' THEN 'All-Defense'
                           WHEN 'All-Rookie' THEN 'All-Rookie' END AS honor
    FROM wh.fact_player_awards
    WHERE award_type IN ('All-NBA','All-Defense','All-Rookie')
    GROUP BY 1, 2, 3
  ),
  bbr_teams AS (
    SELECT season, player_id AS bbr_player_id, player,
           CASE type WHEN 'All-NBA' THEN 'All-NBA'
                     WHEN 'All-Defense' THEN 'All-Defense'
                     WHEN 'All-Rookie' THEN 'All-Rookie' END AS honor
    FROM read_csv_auto('C:/Users/nicolas/Documents/GitHub/basketball-data/csv/nba/end_of_season_teams.csv', nullstr='NA', sample_size=-1)
    WHERE lg = 'NBA' AND type IN ('All-NBA','All-Defense','All-Rookie')
  )
  SELECT 'wh_only' AS side, w.season, w.honor, x.full_name AS player
  FROM wh_teams w LEFT JOIN xwalk x ON x.nba_player_id = w.player_id
  LEFT JOIN bbr_teams b ON b.season = w.season AND b.honor = w.honor AND b.bbr_player_id = x.bbr_player_id
  WHERE b.bbr_player_id IS NULL
  UNION ALL
  SELECT 'bbr_only', b.season, b.honor, b.player
  FROM bbr_teams b LEFT JOIN xwalk x ON x.bbr_player_id = b.bbr_player_id
  LEFT JOIN wh_teams w ON w.season = b.season AND w.honor = b.honor AND w.player_id = x.nba_player_id
  WHERE w.player_id IS NULL
  ORDER BY season, honor, side, player
) TO 'data/audit/out/recon_allnba.csv' (HEADER);

-- ------------------------------------------------ team season per-game stats
COPY (
  WITH wh_ts AS (
    SELECT CAST(substr(season_year,1,4) AS INT)+1 AS season, team_id,
           gp, avg_pts, avg_reb, avg_ast
    FROM wh.agg_team_season WHERE season_type = 'Regular'
  ),
  bbr_ts AS (
    SELECT season, abbreviation,
           g, round(pts/g, 1) AS ppg, round(trb/g, 1) AS rpg, round(ast/g, 1) AS apg
    FROM read_csv_auto('C:/Users/nicolas/Documents/GitHub/basketball-data/csv/nba/team_totals.csv', nullstr='NA', sample_size=-1)
    WHERE lg IN ('NBA','BAA') AND team <> 'League Average'
  )
  SELECT t.season AS season, t.bbr_team_name,
         w.gp AS wh_gp, b.g AS bbr_g,
         round(w.avg_pts,1) AS wh_ppg, b.ppg AS bbr_ppg,
         round(w.avg_reb,1) AS wh_rpg, b.rpg AS bbr_rpg,
         round(w.avg_ast,1) AS wh_apg, b.apg AS bbr_apg,
         (w.gp IS DISTINCT FROM b.g
          OR abs(coalesce(w.avg_pts,0) - coalesce(b.ppg,0)) > 0.15) AS mismatch
  FROM team_xwalk t
  JOIN wh_ts w ON w.season = t.season AND w.team_id = t.team_id
  LEFT JOIN bbr_ts b ON b.season = t.season AND b.abbreviation = t.bbr_abbreviation
  ORDER BY mismatch DESC, season, bbr_team_name
) TO 'data/audit/out/recon_team_season_stats.csv' (HEADER);

-- ------------------------------------------------ player bio vs NBA lineage
COPY (
  WITH nbap AS (
    SELECT personId, birthDate, TRY_CAST(heightInches AS DOUBLE) AS height_in,
           TRY_CAST(bodyWeightLbs AS DOUBLE) AS weight_lb,
           TRY_CAST(draftYear AS INT) AS draft_year, TRY_CAST(draftNumber AS INT) AS draft_number
    FROM read_csv_auto('C:/Users/nicolas/Documents/GitHub/basketball-data/csv/nba/players.csv', sample_size=-1)
  ),
  whp AS (
    SELECT player_id, full_name, birth_date, height, TRY_CAST(weight AS DOUBLE) AS weight,
           TRY_CAST(draft_year AS INT) AS draft_year, TRY_CAST(draft_number AS INT) AS draft_number,
           -- height stored as 'F-II'
           TRY_CAST(split_part(height,'-',1) AS INT)*12 + TRY_CAST(split_part(height,'-',2) AS INT) AS height_in
    FROM wh.dim_player WHERE is_current
  )
  SELECT w.player_id, w.full_name,
         w.birth_date AS wh_birth, n.birthDate AS nba_birth,
         w.height_in AS wh_height_in, n.height_in AS nba_height_in,
         w.weight AS wh_weight, n.weight_lb AS nba_weight,
         w.draft_year AS wh_draft_year, n.draft_year AS nba_draft_year,
         w.draft_number AS wh_draft_number, n.draft_number AS nba_draft_number,
         (CAST(w.birth_date AS DATE) IS DISTINCT FROM TRY_CAST(n.birthDate AS DATE)) AS birth_mismatch,
         (w.height_in IS DISTINCT FROM n.height_in) AS height_mismatch,
         (w.weight IS DISTINCT FROM n.weight_lb) AS weight_mismatch
  FROM whp w
  JOIN nbap n ON n.personId = w.player_id
  WHERE (CAST(w.birth_date AS DATE) IS DISTINCT FROM TRY_CAST(n.birthDate AS DATE))
     OR (w.height_in IS DISTINCT FROM n.height_in)
     OR (w.weight IS DISTINCT FROM n.weight_lb)
  ORDER BY w.full_name
) TO 'data/audit/out/recon_player_bio.csv' (HEADER);

-- ------------------------------------------------ summary
SELECT 'standings mismatches' AS metric,
       (SELECT count(*) FROM read_csv_auto('data/audit/out/recon_standings.csv') WHERE mismatch) AS n
UNION ALL SELECT 'game score mismatches',
       (SELECT count(*) FROM read_csv_auto('data/audit/out/recon_game_scores.csv'))
UNION ALL SELECT 'game coverage gaps (season-type rows)',
       (SELECT count(*) FROM read_csv_auto('data/audit/out/recon_game_coverage.csv'))
UNION ALL SELECT 'all-star diffs',
       (SELECT count(*) FROM read_csv_auto('data/audit/out/recon_allstar.csv'))
UNION ALL SELECT 'all-nba/def/rookie diffs',
       (SELECT count(*) FROM read_csv_auto('data/audit/out/recon_allnba.csv'))
UNION ALL SELECT 'team season stat mismatches',
       (SELECT count(*) FROM read_csv_auto('data/audit/out/recon_team_season_stats.csv') WHERE mismatch)
UNION ALL SELECT 'player bio diffs',
       (SELECT count(*) FROM read_csv_auto('data/audit/out/recon_player_bio.csv'));
