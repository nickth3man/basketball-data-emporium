-- Build player/team crosswalk mapping dictionaries between the warehouse
-- (NBA stats IDs, data/nba.duckdb) and the BBR-derived reference CSVs in
-- the sibling basketball-data repo.
--
-- Run from the repo root:
--   duckdb -c ".read data/audit/build_crosswalk.sql"
--
-- Outputs (data/audit/out/):
--   player_crosswalk.csv        nba_player_id <-> bbr player_id, with method + span score
--   player_unmatched_wh.csv     warehouse players with no BBR name match
--   player_unmatched_bbr.csv    BBR players with no warehouse match (incl. ABA-only flag)
--   team_crosswalk.csv          per-season team identity map (warehouse <-> BBR)
--   team_unmatched.csv          team-season rows that failed to align

ATTACH 'data/nba.duckdb' AS wh (READ_ONLY);

-- Name normalisation shared by both sides: accent-fold, lowercase, strip
-- punctuation, drop generational suffixes, collapse whitespace.
CREATE OR REPLACE MACRO norm_name(s) AS trim(regexp_replace(
  regexp_replace(
    regexp_replace(lower(strip_accents(CAST(s AS VARCHAR))), '[.''`,-]', '', 'g'),
    '\s+(jr|sr|ii|iii|iv|v)$', ''),
  '\s+', ' ', 'g'));

-- ---------------------------------------------------------------- players

-- Career spans derived from actual season rows on both sides (season END
-- years). dim_player.from_year/to_year is NULL for some players, and BBR's
-- career-info from/to includes ABA years the NBA warehouse doesn't track —
-- both would poison span-based disambiguation.
CREATE OR REPLACE TEMP TABLE wh_players AS
WITH spans AS (
  SELECT player_id,
         min(CAST(substr(season_year, 1, 4) AS INT)) + 1 AS from_end_year,
         max(CAST(substr(season_year, 1, 4) AS INT)) + 1 AS to_end_year
  FROM wh.agg_player_season
  GROUP BY 1
)
SELECT d.player_id AS nba_player_id,
       d.full_name,
       norm_name(d.full_name) AS nname,
       s.from_end_year,
       s.to_end_year
FROM wh.dim_player d
JOIN spans s ON d.player_id = s.player_id
WHERE d.is_current;

CREATE OR REPLACE TEMP TABLE bbr_players AS
WITH nba_spans AS (
  SELECT player_id,
         min(season) AS nba_from,
         max(season) AS nba_to
  FROM read_csv_auto('C:/Users/nicolas/Documents/GitHub/basketball-data/csv/nba/player_totals.csv')
  WHERE lg IN ('NBA', 'BAA')
  GROUP BY 1
)
SELECT c.player_id AS bbr_player_id,
       c.player    AS bbr_name,
       norm_name(c.player) AS nname,
       coalesce(s.nba_from, c."from") AS bbr_from,
       coalesce(s.nba_to, c."to")     AS bbr_to
FROM read_csv_auto('C:/Users/nicolas/Documents/GitHub/basketball-data/csv/nba/player_career_info.csv') c
LEFT JOIN nba_spans s ON c.player_id = s.player_id;

CREATE OR REPLACE TEMP TABLE cand AS
SELECT w.nba_player_id, w.full_name, w.nname,
       b.bbr_player_id, b.bbr_name, b.bbr_from, b.bbr_to,
       coalesce(abs(w.from_end_year - b.bbr_from), 100)
     + coalesce(abs(coalesce(w.to_end_year, 2026) - b.bbr_to), 100) AS span_score
FROM wh_players w
JOIN bbr_players b USING (nname);

-- Mutual best match: each side's rank-1 candidate by career-span proximity.
CREATE OR REPLACE TEMP TABLE matched AS
WITH ranked AS (
  SELECT *,
         row_number() OVER (PARTITION BY nba_player_id ORDER BY span_score, bbr_player_id) AS rn_w,
         row_number() OVER (PARTITION BY bbr_player_id ORDER BY span_score, nba_player_id) AS rn_b,
         count(*) OVER (PARTITION BY nname) AS n_pairs
  FROM cand
)
SELECT nba_player_id, full_name, bbr_player_id, bbr_name, span_score,
       CASE WHEN n_pairs = 1 THEN 'name_unique' ELSE 'name_plus_span' END AS method
FROM ranked
WHERE rn_w = 1 AND rn_b = 1;

-- Tier 2: fuzzy match the residue. Handles nicknames ("Al"/"Alvin" Attles),
-- middle-initial disambiguators ("Cliff T. Robinson"), and concatenated
-- names ("Billyray Bates"). Single-letter middle tokens are dropped and
-- Jaro-Winkler similarity is combined with career-span proximity; only
-- mutual best pairs above threshold are accepted.
CREATE OR REPLACE MACRO strip_initials(s) AS
  trim(regexp_replace(s, '(^|\s)[a-z](\s|$)', ' ', 'g'));

CREATE OR REPLACE TEMP TABLE fuzzy AS
WITH wh_rem AS (
  SELECT w.* FROM wh_players w
  LEFT JOIN matched m USING (nba_player_id) WHERE m.nba_player_id IS NULL
),
bbr_rem AS (
  SELECT b.* FROM bbr_players b
  LEFT JOIN matched m USING (bbr_player_id) WHERE m.bbr_player_id IS NULL
),
cand2 AS (
  SELECT w.nba_player_id, w.full_name, b.bbr_player_id, b.bbr_name,
         jaro_winkler_similarity(strip_initials(w.nname), strip_initials(b.nname)) AS sim,
         coalesce(abs(w.from_end_year - b.bbr_from), 100)
       + coalesce(abs(coalesce(w.to_end_year, 2026) - b.bbr_to), 100) AS span_score
  FROM wh_rem w
  JOIN bbr_rem b
    ON split_part(w.nname, ' ', -1) = split_part(b.nname, ' ', -1)  -- same surname
    OR jaro_winkler_similarity(strip_initials(w.nname), strip_initials(b.nname)) >= 0.93
  WHERE span_score <= 2
),
ranked AS (
  SELECT *,
         row_number() OVER (PARTITION BY nba_player_id ORDER BY span_score, sim DESC, bbr_player_id) AS rn_w,
         row_number() OVER (PARTITION BY bbr_player_id ORDER BY span_score, sim DESC, nba_player_id) AS rn_b
  FROM cand2
  WHERE sim >= 0.80
)
SELECT nba_player_id, full_name, bbr_player_id, bbr_name, span_score,
       'fuzzy(' || round(sim, 2) || ')' AS method
FROM ranked
WHERE rn_w = 1 AND rn_b = 1;

-- Tier 3: strong nickname divergence ("Bob" vs "Slick" Leonard) where
-- Jaro-Winkler fails. Exact surname + near-exact career span, accepted only
-- when the surname is unique within both residues so no cross-wiring is
-- possible.
CREATE OR REPLACE TEMP TABLE surname_matched AS
WITH wh_rem AS (
  SELECT w.*, split_part(w.nname, ' ', -1) AS surname FROM wh_players w
  LEFT JOIN matched m USING (nba_player_id)
  LEFT JOIN fuzzy f USING (nba_player_id)
  WHERE m.nba_player_id IS NULL AND f.nba_player_id IS NULL
),
bbr_rem AS (
  SELECT b.*, split_part(b.nname, ' ', -1) AS surname FROM bbr_players b
  LEFT JOIN matched m USING (bbr_player_id)
  LEFT JOIN fuzzy f USING (bbr_player_id)
  WHERE m.bbr_player_id IS NULL AND f.bbr_player_id IS NULL
),
wh_uniq AS (SELECT * FROM wh_rem QUALIFY count(*) OVER (PARTITION BY surname) = 1),
bbr_uniq AS (SELECT * FROM bbr_rem QUALIFY count(*) OVER (PARTITION BY surname) = 1)
SELECT w.nba_player_id, w.full_name, b.bbr_player_id, b.bbr_name,
       abs(w.from_end_year - b.bbr_from) + abs(coalesce(w.to_end_year, 2026) - b.bbr_to) AS span_score,
       'surname_span' AS method
FROM wh_uniq w
JOIN bbr_uniq b USING (surname)
WHERE abs(w.from_end_year - b.bbr_from) + abs(coalesce(w.to_end_year, 2026) - b.bbr_to) <= 1;

-- Tier 4: token-order / token-subset matches — "Jianlian Yi" vs
-- "Yi Jianlian", "Nene Hilario" vs "Nenê" — with tight span agreement.
CREATE OR REPLACE TEMP TABLE token_matched AS
WITH wh_rem AS (
  SELECT w.*, list_sort(string_split(w.nname, ' ')) AS toks FROM wh_players w
  LEFT JOIN matched m USING (nba_player_id)
  LEFT JOIN fuzzy f USING (nba_player_id)
  LEFT JOIN surname_matched sm USING (nba_player_id)
  WHERE m.nba_player_id IS NULL AND f.nba_player_id IS NULL AND sm.nba_player_id IS NULL
),
bbr_rem AS (
  SELECT b.*, list_sort(string_split(b.nname, ' ')) AS toks FROM bbr_players b
  LEFT JOIN matched m USING (bbr_player_id)
  LEFT JOIN fuzzy f USING (bbr_player_id)
  LEFT JOIN surname_matched sm USING (bbr_player_id)
  WHERE m.bbr_player_id IS NULL AND f.bbr_player_id IS NULL AND sm.bbr_player_id IS NULL
),
cand4 AS (
  SELECT w.nba_player_id, w.full_name, b.bbr_player_id, b.bbr_name,
         abs(w.from_end_year - b.bbr_from) + abs(w.to_end_year - b.bbr_to) AS span_score
  FROM wh_rem w
  JOIN bbr_rem b
    ON (w.toks = b.toks OR list_has_all(w.toks, b.toks) OR list_has_all(b.toks, w.toks))
  WHERE abs(w.from_end_year - b.bbr_from) + abs(w.to_end_year - b.bbr_to) <= 2
)
SELECT nba_player_id, full_name, bbr_player_id, bbr_name, span_score,
       'token_set' AS method
FROM cand4
QUALIFY row_number() OVER (PARTITION BY nba_player_id ORDER BY span_score, bbr_player_id) = 1
    AND count(*) OVER (PARTITION BY bbr_player_id) = 1;

-- Tier 5: hand-curated overrides for pairs no heuristic can reach (heavy
-- nickname divergence with ambiguous surnames, etc.). Kept in a reviewed
-- JSON file next to this script.
CREATE OR REPLACE TEMP TABLE override_matched AS
SELECT o.nba_player_id,
       w.full_name,
       o.bbr_player_id,
       b.bbr_name,
       0 AS span_score,
       'manual_override' AS method
FROM read_json('data/audit/player_crosswalk_overrides.json',
               columns = {nba_player_id: 'BIGINT', bbr_player_id: 'VARCHAR', note: 'VARCHAR'}) o
JOIN wh_players w ON w.nba_player_id = o.nba_player_id
JOIN bbr_players b ON b.bbr_player_id = o.bbr_player_id;

CREATE OR REPLACE TEMP TABLE matched_all AS
SELECT * FROM (
  SELECT * FROM matched
  UNION ALL SELECT * FROM fuzzy
  UNION ALL SELECT * FROM surname_matched
  UNION ALL SELECT * FROM token_matched
  UNION ALL SELECT * FROM override_matched
)
-- overrides win if a heuristic tier disagrees
QUALIFY row_number() OVER (
  PARTITION BY nba_player_id
  ORDER BY CASE WHEN method = 'manual_override' THEN 0 ELSE 1 END
) = 1;

COPY (SELECT * FROM matched_all ORDER BY full_name)
TO 'data/audit/out/player_crosswalk.csv' (HEADER);

COPY (
  SELECT w.nba_player_id, w.full_name, w.from_end_year, w.to_end_year
  FROM wh_players w
  LEFT JOIN matched_all m USING (nba_player_id)
  WHERE m.nba_player_id IS NULL
  ORDER BY w.full_name
) TO 'data/audit/out/player_unmatched_wh.csv' (HEADER);

-- BBR players with no warehouse counterpart; flag ABA-only careers, which
-- NBA.com (and hence the warehouse) legitimately does not track.
COPY (
  WITH leagues AS (
    SELECT player_id AS bbr_player_id,
           bool_and(lg = 'ABA') AS aba_only
    FROM read_csv_auto('C:/Users/nicolas/Documents/GitHub/basketball-data/csv/nba/player_totals.csv')
    GROUP BY 1
  )
  SELECT b.bbr_player_id, b.bbr_name, b.bbr_from, b.bbr_to,
         coalesce(l.aba_only, false) AS aba_only
  FROM bbr_players b
  LEFT JOIN matched_all m USING (bbr_player_id)
  LEFT JOIN leagues l USING (bbr_player_id)
  WHERE m.bbr_player_id IS NULL
  ORDER BY b.bbr_name
) TO 'data/audit/out/player_unmatched_bbr.csv' (HEADER);

-- ------------------------------------------------------------------ teams

-- Warehouse team-season identity: game logs where present (1996-97+),
-- the historical `game` table before that. Seasons keyed by END year to
-- match BBR convention.
CREATE OR REPLACE TEMP TABLE wh_team_seasons AS
SELECT DISTINCT
       CAST(substr(season_id, 1, 4) AS INT) + 1 AS season,
       team_id, team_abbreviation, team_name
FROM wh.fact_team_game_log
UNION
SELECT DISTINCT
       CAST(substr(season_id, 2) AS INT) + 1 AS season,
       team_id_home, team_abbreviation_home, team_name_home
FROM wh.game
WHERE season_type = 'Regular Season'
  AND CAST(substr(season_id, 2) AS INT) < 1996
UNION
SELECT DISTINCT
       CAST(substr(season_id, 2) AS INT) + 1 AS season,
       team_id_away, team_abbreviation_away, team_name_away
FROM wh.game
WHERE season_type = 'Regular Season'
  AND CAST(substr(season_id, 2) AS INT) < 1996;

-- NBA.com and BBR disagree on a handful of display names; normalise the
-- NBA.com side toward BBR before joining.
CREATE OR REPLACE MACRO norm_team(s) AS
  CASE lower(trim(CAST(s AS VARCHAR)))
    WHEN 'la clippers' THEN 'los angeles clippers'
    ELSE lower(trim(CAST(s AS VARCHAR)))
  END;

CREATE OR REPLACE TEMP TABLE bbr_team_seasons AS
SELECT season, lg, team AS bbr_team_name, abbreviation AS bbr_abbreviation
FROM read_csv_auto('C:/Users/nicolas/Documents/GitHub/basketball-data/csv/nba/team_abbrev.csv');

CREATE OR REPLACE TEMP TABLE team_matched AS
WITH by_name AS (
  SELECT w.season, w.team_id, w.team_abbreviation, w.team_name,
         b.bbr_abbreviation, b.bbr_team_name, b.lg, 'name' AS method
  FROM wh_team_seasons w
  JOIN bbr_team_seasons b
    ON w.season = b.season
   AND norm_team(w.team_name) = norm_team(b.bbr_team_name)
),
-- Fallback: identical abbreviation in the same season (covers spelling
-- variants like "Ft. Wayne Zollner Pistons" vs "Fort Wayne Pistons").
by_abbrev AS (
  SELECT w.season, w.team_id, w.team_abbreviation, w.team_name,
         b.bbr_abbreviation, b.bbr_team_name, b.lg, 'abbrev' AS method
  FROM wh_team_seasons w
  JOIN bbr_team_seasons b
    ON w.season = b.season AND w.team_abbreviation = b.bbr_abbreviation
  WHERE NOT EXISTS (SELECT 1 FROM by_name n WHERE n.season = w.season AND n.team_id = w.team_id)
)
SELECT * FROM by_name UNION ALL SELECT * FROM by_abbrev;

COPY (SELECT * FROM team_matched ORDER BY season, team_abbreviation)
TO 'data/audit/out/team_crosswalk.csv' (HEADER);

COPY (
  SELECT 'warehouse' AS side, w.season, CAST(w.team_id AS VARCHAR) AS id,
         w.team_abbreviation AS abbreviation, w.team_name
  FROM wh_team_seasons w
  LEFT JOIN team_matched m ON w.season = m.season AND w.team_id = m.team_id
  WHERE m.team_id IS NULL
  UNION ALL
  SELECT 'bbr', b.season, b.lg, b.bbr_abbreviation, b.bbr_team_name
  FROM bbr_team_seasons b
  LEFT JOIN team_matched m
    ON b.season = m.season AND b.bbr_abbreviation = m.bbr_abbreviation
  WHERE m.season IS NULL
  ORDER BY season, side, team_name
) TO 'data/audit/out/team_unmatched.csv' (HEADER);

-- ---------------------------------------------------------------- summary
SELECT 'wh players' AS metric, count(*) AS n FROM wh_players
UNION ALL SELECT 'bbr players', count(*) FROM bbr_players
UNION ALL SELECT 'matched players', count(*) FROM matched_all
UNION ALL SELECT 'matched name_unique', count(*) FROM matched_all WHERE method = 'name_unique'
UNION ALL SELECT 'matched fuzzy', count(*) FROM matched_all WHERE method LIKE 'fuzzy%'
UNION ALL SELECT 'unmatched wh', (SELECT count(*) FROM wh_players w LEFT JOIN matched_all m USING (nba_player_id) WHERE m.nba_player_id IS NULL)
UNION ALL SELECT 'wh team-seasons', count(*) FROM wh_team_seasons
UNION ALL SELECT 'matched team-seasons', count(*) FROM team_matched
ORDER BY metric;
