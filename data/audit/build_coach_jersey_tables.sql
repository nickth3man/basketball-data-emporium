-- Materialize coach history and per-player/per-team/per-season jersey
-- numbers as queryable warehouse tables, so neither requires the web
-- server's request-time JSONL reads.
--
--   fact_coach_season          one row per (team, season, coach) with that
--                              coach's W-L for the stint; mid-season changes
--                              appear as multiple rows for the team-season.
--                              Source: data/anchors/bbr_coaches.jsonl (BBR
--                              franchise-page scrape). Covers 1946-47 ->
--                              2025-26 for the 30 current franchises;
--                              defunct franchises are absent.
--
--   fact_player_jersey_season  one row per (player, team, season) with the
--                              resolved jersey number — traded players get
--                              one row per team that season. Materializes
--                              the same 4-tier source-priority logic the web
--                              server runs per player in getPlayerProfile
--                              (web/server/queries/players.ts):
--                                1 game_inactive_list  per-game jersey
--                                  observations from inactive_players
--                                2 bbr_roster          BBR team-roster scrape
--                                  (data/anchors/bbr_jerseys.jsonl)
--                                3 inferred            player's single trusted
--                                  number on that team, filled backwards
--                                4 bridge_roster       bridge_player_team_season
--                                  fill (its current-number-leaks-into-history
--                                  problem is suppressed wherever BBR covers
--                                  the team-season)
--                              One deliberate upgrade vs the server query:
--                              game seasons resolve via fact_game (complete
--                              1946->present) instead of the legacy `game`
--                              table (missing whole seasons).
--
-- Accuracy notes: since 2026-07-04 bbr_jerseys.jsonl comes from
-- scrape_uniform_numbers.py (BBR's uniform-number index pages), which
-- covers essentially every NBA team-season, so tier 2 dominates: the
-- inference tier is nearly extinct and bridge fill is fully suppressed.
-- Historic wrong-number cases are verified fixed (Kobe 1996-2006 = 8,
-- Harden HOU 2020-21 = 13). inactive_players ends after 2022-23, so
-- 2023-24+ rows are BBR-only — fine, the number pages carry those seasons.
-- Do NOT be tempted by the other jersey-bearing tables:
-- stg_espn_nba_player_box / stg_espn_nba_game_rosters / fact_cumulative_stats
-- all stamp the player's CURRENT number onto historical rows (verified:
-- Kobe shows 24 back to 1996/2002 in all of them), and the hustle/fantasy
-- tables are empty shells.
--
-- Run from the repo root with the dev server STOPPED (needs the write lock):
--   duckdb data/nba.duckdb -c ".read data/audit/build_coach_jersey_tables.sql"
-- Idempotent: CREATE OR REPLACE both tables.

CREATE OR REPLACE MACRO end_year_to_season(y) AS
  CAST(y - 1 AS VARCHAR) || '-' || lpad(CAST(y % 100 AS VARCHAR), 2, '0');

-- ---------------------------------------------------------- fact_coach_season

CREATE OR REPLACE TABLE fact_coach_season AS
SELECT
  c.team_id,
  th.abbreviation AS team_abbreviation,
  th.nickname AS team_name,
  c.bbr_team AS bbr_team_abbreviation,
  c.season_year,
  c.season_end_year,
  c.bbr_slug AS coach_bbr_slug,
  -- first/last name are NULL for some historical coaches; fall back to the
  -- franchise-page label ("N. McMillan").
  coalesce(nullif(trim(concat_ws(' ', c.first_name, c.last_name)), ''), c.coach_label) AS coach_name,
  c.coach_label,
  c.first_name,
  c.last_name,
  c.wins,
  c.losses,
  round(c.wins / nullif(c.wins + c.losses, 0), 3) AS win_pct,
  count(*) OVER (PARTITION BY c.team_id, c.season_year) AS coaches_in_season,
  c.source_url
FROM read_json_auto('data/anchors/bbr_coaches.jsonl') c
LEFT JOIN dim_team_era th ON th.team_id = c.team_id AND th.is_current
ORDER BY c.team_id, c.season_end_year, coach_name;

-- -------------------------------------------------- fact_player_jersey_season

CREATE OR REPLACE TABLE fact_player_jersey_season AS
WITH pxw AS (
  -- NBA <-> BBR player crosswalk, one warehouse id per BBR player
  -- (duplicate-identity ids rank by game-log volume) — same dedup rule as
  -- PLAYER_BBR_XWALK_CTE in the server. map_player_bbr already carries this
  -- ranking (is_preferred), computed once in build_nba.py's build_maps() —
  -- read it directly instead of re-deriving it here.
  SELECT bbr_player_id, player_id AS nba_player_id
  FROM map_player_bbr
  WHERE is_preferred
),
-- Player-team-seasons where the player actually appeared (regular season,
-- gp > 0) — the candidate grain, and the validity gate for observed/bridge
-- jerseys. Traded players have one row per team.
stats AS (
  -- fact_player_season_box already carries a resolved canonical player_id
  -- directly (no pxw/slug fallback needed, unlike the pre-migration
  -- fact_player_season_stat_resolved this replaces).
  SELECT DISTINCT
    player_id,
    team_id,
    season_year
  FROM fact_player_season_box
  WHERE season_type = 'Regular'
    AND gp > 0
    AND team_id IS NOT NULL
),
-- Tier 1: jersey numbers observed on real game inactive lists (1996-97+).
per_game AS (
  SELECT
    try_cast(ip.player_id AS BIGINT) AS player_id,
    try_cast(ip.team_id AS BIGINT) AS team_id,
    trim(ip.jersey_num) AS jersey_num,
    g.season_year
  FROM src_inactive_players ip
  JOIN dim_game g ON g.game_id = ip.game_id
  WHERE trim(ip.jersey_num) != ''
),
per_season_ip AS (
  SELECT p.player_id, p.team_id, p.jersey_num, p.season_year
  FROM per_game p
  JOIN stats s
    ON s.player_id = p.player_id AND s.team_id = p.team_id AND s.season_year = p.season_year
  GROUP BY 1, 2, 3, 4
  -- majority number wins within a player-team-season
  QUALIFY row_number() OVER (
    PARTITION BY p.player_id, p.team_id, p.season_year
    ORDER BY count(*) DESC, p.jersey_num
  ) = 1
),
-- Tier 2: BBR per-season team-roster scrape.
bbr_raw AS (
  SELECT
    try_cast(player_id AS BIGINT) AS player_id,
    try_cast(team_id AS BIGINT) AS team_id,
    trim(jersey_num) AS jersey_num,
    season_year
  FROM read_json_auto('data/anchors/bbr_jerseys.jsonl')
  WHERE team_id IS NOT NULL AND season_year IS NOT NULL
),
-- A team-season counts as BBR-covered when the scrape has a plausible
-- roster's worth of players — the same >= 5 rule the server uses to decide
-- when bridge rows should be suppressed.
bbr_covered AS (
  SELECT team_id, season_year
  FROM bbr_raw
  GROUP BY 1, 2
  HAVING count(DISTINCT player_id) >= 5
),
bbr_dedup AS (
  SELECT DISTINCT player_id, team_id, jersey_num, season_year
  FROM bbr_raw
  WHERE player_id IS NOT NULL AND jersey_num IS NOT NULL AND jersey_num != ''
),
-- Tier 4 input: bridge roster rows, only for real player-team-seasons, and
-- only where BBR doesn't already cover the team-season (bridge leaks a
-- player's current number into historical seasons).
bridge_dedup AS (
  SELECT DISTINCT
    b.player_id,
    try_cast(b.team_id AS BIGINT) AS team_id,
    trim(b.jersey_number) AS jersey_num,
    b.season_year
  FROM src_bridge_player_team_season b
  JOIN stats s
    ON s.player_id = b.player_id
    AND s.team_id = try_cast(b.team_id AS BIGINT)
    AND s.season_year = b.season_year
  WHERE b.jersey_number IS NOT NULL
    AND trim(b.jersey_number) != ''
    AND NOT EXISTS (
      SELECT 1 FROM bbr_covered c
      WHERE c.team_id = try_cast(b.team_id AS BIGINT) AND c.season_year = b.season_year
    )
),
trusted AS (
  SELECT player_id, team_id, jersey_num, season_year,
         1 AS source_priority, 'game_inactive_list' AS source
  FROM per_season_ip
  UNION ALL
  SELECT player_id, team_id, jersey_num, season_year, 2, 'bbr_roster'
  FROM bbr_dedup
),
exact_trusted AS (
  SELECT *
  FROM trusted
  QUALIFY row_number() OVER (
    PARTITION BY player_id, team_id, season_year
    ORDER BY source_priority, jersey_num
  ) = 1
),
-- Tier 3: when every trusted observation of a player on a team shows the
-- same number, fill that number backwards into his earlier stat seasons on
-- that team that have no direct observation.
trusted_team_numbers AS (
  SELECT player_id, team_id,
         min(jersey_num) AS jersey_num,
         max(try_cast(left(season_year, 4) AS INTEGER)) AS last_trusted_start_year,
         count(DISTINCT jersey_num) AS distinct_jersey_nums
  FROM trusted
  GROUP BY 1, 2
),
inferred AS (
  SELECT s.player_id, s.team_id, t.jersey_num, s.season_year,
         3 AS source_priority, 'inferred' AS source
  FROM stats s
  JOIN trusted_team_numbers t
    ON t.player_id = s.player_id AND t.team_id = s.team_id
  WHERE t.distinct_jersey_nums = 1
    AND try_cast(left(s.season_year, 4) AS INTEGER) <= t.last_trusted_start_year
    AND NOT EXISTS (
      SELECT 1 FROM exact_trusted e
      WHERE e.player_id = s.player_id AND e.team_id = s.team_id AND e.season_year = s.season_year
    )
),
bridge_fill AS (
  SELECT b.player_id, b.team_id, b.jersey_num, b.season_year,
         4 AS source_priority, 'bridge_roster' AS source
  FROM bridge_dedup b
  WHERE NOT EXISTS (
      SELECT 1 FROM exact_trusted e
      WHERE e.player_id = b.player_id AND e.team_id = b.team_id AND e.season_year = b.season_year
    )
    AND NOT EXISTS (
      SELECT 1 FROM inferred i
      WHERE i.player_id = b.player_id AND i.team_id = b.team_id AND i.season_year = b.season_year
    )
),
combined AS (
  SELECT * FROM exact_trusted
  UNION ALL SELECT * FROM inferred
  UNION ALL SELECT * FROM bridge_fill
),
resolved AS (
  SELECT *
  FROM combined
  WHERE EXISTS (SELECT 1 FROM dim_team_era th WHERE th.team_id = combined.team_id)
  QUALIFY row_number() OVER (
    PARTITION BY player_id, team_id, season_year
    ORDER BY source_priority, jersey_num
  ) = 1
),
player_names AS (
  -- dim_player is already one row per player_id; no dedup needed.
  SELECT player_id, full_name
  FROM dim_player
)
SELECT
  r.player_id,
  coalesce(pn.full_name, cap.display_first_last) AS player_name,
  r.team_id,
  th.abbreviation AS team_abbreviation,
  th.nickname AS team_name,
  r.season_year,
  try_cast(left(r.season_year, 4) AS INTEGER) + 1 AS season_end_year,
  r.jersey_num AS jersey_number,
  r.source
FROM resolved r
LEFT JOIN player_names pn ON pn.player_id = r.player_id
LEFT JOIN src_dim_all_players cap ON cap.person_id = r.player_id
LEFT JOIN dim_team_era th ON th.team_id = r.team_id AND th.is_current
ORDER BY player_name, r.season_year, r.team_id;
