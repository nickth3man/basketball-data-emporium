import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { queryObjects } from "./db.ts";
import type { DuckDBValue } from "@duckdb/node-api";
import { colorForEra } from "./teamColorEras.ts";

type Row = Record<string, unknown>;

// Supplemental jersey-history table scraped from Basketball-Reference
// per-season team-roster pages. The scraper lives at
// ``data/anchors/scrape_team_rosters.py``; its output is a JSONL file
// with one record per (player_id, team_id, season_year, jersey_num).
// The file is read on every player-profile request via DuckDB's
// ``read_json_auto`` — at <300 rows the per-request cost is
// negligible, and a read on every call means re-running the scraper
// (which writes a new file and atomically replaces the old one) is
// picked up without a server restart. The path resolves relative to
// this file (web/server/queries.ts) so it works regardless of CWD
// (npm scripts, tsx watch, production start, etc.). ``BBR_JERSEYS_PATH``
// overrides the default for tests / alternate deployments. When the
// file is missing (fresh checkout, before the scraper has been run)
// the BBR CTE is omitted entirely — the bridge fallback still works.
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BBR_JERSEYS_PATH =
  process.env.BBR_JERSEYS_PATH ?? path.resolve(__dirname, "../../data/anchors/bbr_jerseys.jsonl");
const BBR_JERSEYS_AVAILABLE = existsSync(BBR_JERSEYS_PATH);
if (BBR_JERSEYS_AVAILABLE) {
  // One-time diagnostic so the dev can tell from the server log
  // whether the BBR layer is wired in. The path is logged, not
  // interpolated into SQL, so no escaping needed.
  console.log(`[queries] BBR jersey fallback enabled: ${BBR_JERSEYS_PATH}`);
}

// Jersey-history SQL template. ``$BBR_CTE`` is replaced with the BBR
// CTE (or empty when the file is missing); ``$BBR_UNION`` is replaced
// with the corresponding ranked source branch (or empty). The body is the
// long, hand-tuned gaps-and-islands query that the previous
// per-row / per-season logic depends on; details are in the inline
// comments above the call site. We avoid an embedded template string
// here because that would intermingle SQL and JS-quoting concerns —
// a plain string with two placeholders, spliced in buildJerseyQuery,
// is the cleanest split.
const JERSEY_SQL_TEMPLATE = `WITH per_game AS (
         SELECT
           TRY_CAST(ip.team_id AS BIGINT) AS team_id,
           TRIM(ip.jersey_num) AS jersey_num,
           CASE WHEN MONTH(g.game_date) >= 8
             THEN CAST(YEAR(g.game_date) AS VARCHAR) || '-' || RIGHT(CAST(YEAR(g.game_date) + 1 AS VARCHAR), 2)
             ELSE CAST(YEAR(g.game_date) - 1 AS VARCHAR) || '-' || RIGHT(CAST(YEAR(g.game_date) AS VARCHAR), 2)
           END AS season_year
         FROM inactive_players ip
         JOIN game g ON g.game_id = ip.game_id
         WHERE ip.player_id = ? AND TRIM(ip.jersey_num) != ''
           AND EXISTS (
             SELECT 1
             FROM agg_player_season inactive_season
             WHERE inactive_season.player_id = ip.player_id
               AND inactive_season.team_id = TRY_CAST(ip.team_id AS BIGINT)
               AND inactive_season.season_year = CASE WHEN MONTH(g.game_date) >= 8
                 THEN CAST(YEAR(g.game_date) AS VARCHAR) || '-' || RIGHT(CAST(YEAR(g.game_date) + 1 AS VARCHAR), 2)
                 ELSE CAST(YEAR(g.game_date) - 1 AS VARCHAR) || '-' || RIGHT(CAST(YEAR(g.game_date) AS VARCHAR), 2)
               END
               AND inactive_season.season_type = 'Regular'
               AND inactive_season.gp > 0
           )
       ),
       per_season_ip AS (
         SELECT season_year, team_id, jersey_num, count(*) AS n
         FROM per_game
         GROUP BY 1, 2, 3
         QUALIFY ROW_NUMBER() OVER (PARTITION BY season_year, team_id ORDER BY n DESC) = 1
       ),
       $BBR_CTE
       bridge_dedup AS (
         SELECT DISTINCT
           team_id,
           TRIM(jersey_number) AS jersey_num,
           season_year
         FROM bridge_player_team_season
         WHERE player_id = ?
           AND jersey_number IS NOT NULL
           AND TRIM(jersey_number) != ''
           AND EXISTS (
             SELECT 1
             FROM agg_player_season bridge_season
             WHERE bridge_season.player_id = bridge_player_team_season.player_id
               AND bridge_season.team_id = TRY_CAST(bridge_player_team_season.team_id AS BIGINT)
               AND bridge_season.season_year = bridge_player_team_season.season_year
               AND bridge_season.season_type = 'Regular'
           )
           $BRIDGE_BBR_EXCLUSION
       ),
       combined_candidates AS (
         SELECT team_id, jersey_num, season_year, 1 AS source_priority FROM per_season_ip
         $BBR_UNION
         UNION ALL
         SELECT team_id, jersey_num, season_year, 3 AS source_priority FROM bridge_dedup
       ),
       valid_candidates AS (
         SELECT *
         FROM combined_candidates candidate
         WHERE EXISTS (
           SELECT 1
           FROM dim_team_history history_team
           WHERE history_team.team_id = candidate.team_id
         )
       ),
       combined AS (
         SELECT team_id, jersey_num, season_year
         FROM valid_candidates
         QUALIFY ROW_NUMBER() OVER (
           PARTITION BY team_id, season_year
           ORDER BY source_priority
         ) = 1
       ),
       combined_with_first AS (
         SELECT team_id, jersey_num, season_year,
                MIN(season_year) OVER (PARTITION BY team_id, jersey_num) AS first_season
         FROM combined
       ),
       grouped AS (
         SELECT
           team_id, jersey_num, season_year,
           ROW_NUMBER() OVER (ORDER BY season_year, first_season, team_id, jersey_num)
             - ROW_NUMBER() OVER (PARTITION BY team_id, jersey_num ORDER BY season_year) AS stint_group
         FROM combined_with_first
       ),
       stint_bounds AS (
         SELECT team_id, jersey_num, stint_group, MIN(season_year) AS stint_first_season
         FROM grouped
         GROUP BY team_id, jersey_num, stint_group
       )
       SELECT
         g.team_id,
         g.jersey_num,
         g.season_year,
         g.stint_group,
         COALESCE(dt.abbreviation, th.abbreviation) AS abbreviation,
         COALESCE(dt.full_name, th.nickname) AS team_name
       FROM grouped g
       JOIN stint_bounds sb
         ON sb.team_id = g.team_id AND sb.jersey_num = g.jersey_num AND sb.stint_group = g.stint_group
       JOIN dim_team_history th
         ON th.team_id = g.team_id
         AND (g.season_year >= th.valid_from OR th.is_current)
         AND (th.valid_to IS NULL OR g.season_year < th.valid_to)
       LEFT JOIN dim_team dt
         ON dt.team_id = g.team_id
         AND TRY_CAST(LEFT(g.season_year, 4) AS INTEGER) >= TRY_CAST(dt.year_founded AS INTEGER)
       QUALIFY ROW_NUMBER() OVER (
         PARTITION BY g.team_id, g.jersey_num, g.stint_group, g.season_year
         ORDER BY
           CASE WHEN dt.team_id IS NOT NULL THEN 0 ELSE 1 END,
           CASE WHEN dt.year_founded IS NULL THEN 1 ELSE 0 END,
           TRY_CAST(dt.year_founded AS INTEGER) DESC,
           CASE WHEN g.season_year >= th.valid_from AND (th.valid_to IS NULL OR g.season_year < th.valid_to) THEN 0 ELSE 1 END,
           th.valid_from ASC
       ) = 1
        ORDER BY sb.stint_first_season, g.season_year, g.team_id, g.jersey_num`;

/** Builds the SQL for the per-player jersey history query, optionally
 *  splicing in the BBR-scraped roster CTE when its JSONL file
 *  is present. Returns the SQL string and the parameter array (in
 *  the order DuckDB will bind them). The BBR CTE and UNION are
 *  omitted entirely when the file is missing so a fresh checkout
 *  with no scraped data still gets a working bridge-fallback query. */
function buildJerseyQuery(playerId: number): { sql: string; params: DuckDBValue[] } {
  if (!BBR_JERSEYS_AVAILABLE) {
    // Empty placeholders yield the same shape as the bridge-only query
    // that shipped before BBR was introduced. The two ``?``s bind to
    // per_game and bridge_dedup.
    return {
      sql: JERSEY_SQL_TEMPLATE.replace("$BBR_CTE", "")
        .replace("$BRIDGE_BBR_EXCLUSION", "")
        .replace("$BBR_UNION", ""),
      params: [playerId, playerId],
    };
  }
  // SQL-string-escape the path: DuckDB's read_json_auto takes a string
  // literal, not a parameter. Forward slashes work on every platform
  // (including Windows) so we normalize, then double any embedded
  // single quotes.
  const safePath = BBR_JERSEYS_PATH.replaceAll("\\", "/").replaceAll("'", "''");
  const bbrCte = `bbr_raw AS (
          SELECT
            TRY_CAST(player_id AS BIGINT) AS player_id,
            TRY_CAST(team_id AS BIGINT) AS team_id,
            TRIM(jersey_num) AS jersey_num,
            season_year
          FROM read_json_auto('${safePath}')
          WHERE team_id IS NOT NULL
            AND season_year IS NOT NULL
        ),
        bbr_covered_team_seasons AS (
          SELECT team_id, season_year
          FROM bbr_raw
          GROUP BY team_id, season_year
          HAVING COUNT(DISTINCT player_id) >= 5
        ),
        bbr_dedup AS (
          SELECT DISTINCT
            team_id,
            jersey_num,
            season_year
          FROM bbr_raw
          WHERE player_id = ?
            AND jersey_num IS NOT NULL
            AND jersey_num != ''
        ),
        `;
  const bridgeBbrExclusion = `
           AND NOT EXISTS (
             SELECT 1
             FROM bbr_covered_team_seasons bbr_coverage
             WHERE bbr_coverage.team_id = TRY_CAST(bridge_player_team_season.team_id AS BIGINT)
               AND bbr_coverage.season_year = bridge_player_team_season.season_year
           )`;
  const bbrUnion = `
         UNION ALL
         SELECT team_id, jersey_num, season_year, 2 AS source_priority FROM bbr_dedup`;
  const sql = JERSEY_SQL_TEMPLATE.replace("$BBR_CTE", bbrCte)
    .replace("$BRIDGE_BBR_EXCLUSION", bridgeBbrExclusion)
    .replace("$BBR_UNION", bbrUnion);
  return { sql, params: [playerId, playerId, playerId] };
}

// ---------------------------------------------------------------------------
// Players
//
// dim_player is a slowly-changing-dimension table (one row per team stint),
// so every lookup filters to is_current=true to get exactly one row per
// player. agg_player_season/career/fact_player_awards/draft_history key
// directly on player_id and don't need that filter.
// ---------------------------------------------------------------------------

export async function searchPlayers(q: string, limit = 25): Promise<Row[]> {
  return queryObjects(
    `SELECT p.player_id, p.full_name, p.position, p.is_active, th.abbreviation AS team_abbreviation
     FROM dim_player p
     LEFT JOIN dim_team_history th ON th.team_id = p.team_id AND th.is_current
     WHERE p.is_current AND p.full_name ILIKE ?
     ORDER BY p.full_name
     LIMIT ?`,
    [`%${q}%`, limit],
  );
}

export interface Badge {
  season: string;
  label: string;
}

export interface JerseyStint {
  team_id: number;
  abbreviation: string;
  team_name: string;
  jersey_num: string;
  start_year: number;
  end_year: number;
  primary: string;
  trim: string;
}

interface JerseySeasonRow {
  team_id: number;
  jersey_num: string;
  season_year: string;
  stint_group: number;
  abbreviation: string;
  team_name: string;
}

/** Splits each (team, number) stint further wherever the team's jersey
 *  color changed mid-stint (e.g. a rebrand with no relocation/rename, like
 *  Detroit's 1996-97 switch to teal — same team_id, same player could keep
 *  the same number straight through it). Operates on the season-year start
 *  (e.g. "2003-04" -> 2003) since that's what TEAM_COLOR_ERAS.from uses. */
function splitJerseyStintsByColorEra(rows: JerseySeasonRow[]): JerseyStint[] {
  const stints: JerseyStint[] = [];
  let current: {
    row: JerseySeasonRow;
    color: { primary: string; trim: string };
    firstYear: number;
    lastYear: number;
  } | null = null;

  for (const row of rows) {
    const calendarYear = Number(row.season_year.slice(0, 4));
    const color = colorForEra(row.abbreviation, calendarYear, row.team_id);
    const sameRun =
      current?.row.team_id === row.team_id &&
      current?.row.abbreviation === row.abbreviation &&
      current?.row.team_name === row.team_name &&
      current?.row.jersey_num === row.jersey_num &&
      current?.row.stint_group === row.stint_group &&
      current?.color.primary === color.primary &&
      current?.color.trim === color.trim;

    if (sameRun && current) {
      current.lastYear = calendarYear;
    } else {
      if (current) stints.push(finalizeJerseyStint(current));
      current = { row, color, firstYear: calendarYear, lastYear: calendarYear };
    }
  }
  if (current) stints.push(finalizeJerseyStint(current));
  return stints;
}

function finalizeJerseyStint(current: {
  row: JerseySeasonRow;
  color: { primary: string; trim: string };
  firstYear: number;
  lastYear: number;
}): JerseyStint {
  return {
    team_id: current.row.team_id,
    abbreviation: current.row.abbreviation,
    team_name: current.row.team_name,
    jersey_num: current.row.jersey_num,
    start_year: current.firstYear,
    end_year: current.lastYear + 1,
    primary: current.color.primary,
    trim: current.color.trim,
  };
}

export interface PlayerProfile {
  bio: Row | null;
  career: Row | null;
  seasons: Row[];
  awards: Row[];
  draft: Row | null;
  hallOfFameYear: number | null;
  isGreatest75: boolean;
  allStarCount: number;
  careerEfgPct: number | null;
  badges: Badge[];
  jerseyHistory: JerseyStint[];
}

/** Formats a fact_player_awards-style single end-year ("1969") as a BBR-style
 *  season range ("1968-69"). agg_league_leaders.season_year is already in
 *  that range form and needs no conversion. */
function seasonRangeFromEndYear(yearLike: unknown): string {
  const year = Number(yearLike);
  if (!Number.isFinite(year)) return String(yearLike);
  return `${year - 1}-${String(year).slice(-2)}`;
}

const HONOR_LABELS: Record<string, string> = {
  "All-NBA": "All-NBA",
  "All-Rookie": "All-Rookie",
  "All-Defense": "All-Defense",
  "nba mvp": "MVP",
  "nba roy": "ROY",
  "nba dpoy": "DPOY",
  "nba mip": "MIP",
  "nba smoy": "SMOY",
};

export async function getPlayerProfile(playerId: number): Promise<PlayerProfile> {
  const [
    bioRows,
    commonInfoRows,
    career,
    seasons,
    awards,
    draft,
    hofRows,
    efgRows,
    allStarRows,
    honorRows,
    leaderRows,
    jerseyRows,
  ] = await Promise.all([
    queryObjects(
      `SELECT p.*, th.abbreviation AS team_abbreviation, th.nickname AS team_name
       FROM dim_player p
       LEFT JOIN dim_team_history th ON th.team_id = p.team_id AND th.is_current
       WHERE p.player_id = ? AND p.is_current
       LIMIT 1`,
      [playerId],
    ),
    // common_player_info carries BBR-header fields dim_player doesn't have:
    // school (college *or* high school — the source doesn't distinguish),
    // country, full position name, and season_exp (career length in years).
    queryObjects(
      `SELECT * FROM common_player_info WHERE TRY_CAST(person_id AS BIGINT) = ? LIMIT 1`,
      [playerId],
    ),
    // agg_player_career is unreliable — verified corrupt for at least some
    // players (e.g. Wes Unseld: career_gp=1103 vs the real/BBR 984, and
    // career_fg3_pct=1.5 i.e. 150%, traced to a single season row with
    // fg3m=12 > fg3a=6, which is impossible). Its "total_*" columns are
    // similarly untrustworthy (found inflated ~6x vs gp*avg_* on the same
    // rows). Recomputed instead as a games-weighted average of the
    // per-season avg_*/`*_pct` fields (which check out against known
    // history), from agg_player_season directly. Any single-season pct
    // outside [0,1] is treated as missing rather than averaged in.
    queryObjects(
      `SELECT
         SUM(gp) AS career_gp,
         SUM(avg_pts * gp) / NULLIF(SUM(gp), 0) AS career_ppg,
         SUM(avg_reb * gp) / NULLIF(SUM(gp), 0) AS career_rpg,
         SUM(avg_ast * gp) / NULLIF(SUM(gp), 0) AS career_apg,
         SUM(CASE WHEN fg_pct BETWEEN 0 AND 1 THEN fg_pct * gp END)
           / NULLIF(SUM(CASE WHEN fg_pct BETWEEN 0 AND 1 THEN gp END), 0) AS career_fg_pct,
         SUM(CASE WHEN fg3_pct BETWEEN 0 AND 1 THEN fg3_pct * gp END)
           / NULLIF(SUM(CASE WHEN fg3_pct BETWEEN 0 AND 1 THEN gp END), 0) AS career_fg3_pct,
         SUM(CASE WHEN ft_pct BETWEEN 0 AND 1 THEN ft_pct * gp END)
           / NULLIF(SUM(CASE WHEN ft_pct BETWEEN 0 AND 1 THEN gp END), 0) AS career_ft_pct
       FROM agg_player_season
       WHERE player_id = ? AND season_type = 'Regular'`,
      [playerId],
    ),
    queryObjects(
      // agg_player_season.team_abbreviation is unreliable in the source data
      // (e.g. it shows "PHI" for every Stephen Curry season including ones
      // with the Warriors) even though team_id is correct, so the
      // abbreviation is re-derived from team_id, matched to the
      // dim_team_history era whose [valid_from, valid_to) range contains
      // that season (e.g. Durant's 2007-08 resolves to SEA, 2008-09+ to
      // OKC) rather than always using the team's current name. dim_team_history
      // only tracks eras from 1996-97 onward, so seasons before that fall back
      // to the earliest known era for the team (tiebreak rank 1 below) — a team
      // renamed/relocated before 1996-97 (e.g. Minneapolis->LA Lakers in 1960)
      // will still show its post-1996-97 name for those older seasons.
      // QUALIFY (not a plain dedup) is needed because in-season trades give a
      // player multiple team_id rows in the same season_year/season_type.
      `SELECT s.* EXCLUDE (team_abbreviation), th.abbreviation AS team_abbreviation
       FROM agg_player_season s
       LEFT JOIN dim_team_history th ON th.team_id = s.team_id
       WHERE s.player_id = ?
       QUALIFY ROW_NUMBER() OVER (
         PARTITION BY s.team_id, s.season_year, s.season_type
         ORDER BY
           CASE WHEN s.season_year >= th.valid_from AND (th.valid_to IS NULL OR s.season_year < th.valid_to)
                THEN 0 ELSE 1 END,
           th.valid_from ASC
       ) = 1
       ORDER BY s.season_year, s.season_type`,
      [playerId],
    ),
    queryObjects(
      `SELECT * FROM fact_player_awards WHERE player_id = ? ORDER BY season, award_type`,
      [playerId],
    ),
    queryObjects(`SELECT * FROM draft_history WHERE TRY_CAST(person_id AS BIGINT) = ? LIMIT 1`, [
      playerId,
    ]),
    // stg_team_retired is mislabeled — its actual content is Hall of Fame
    // induction records, not retired jersey numbers. Verified against known
    // inductions (Magic Johnson 2002, Kobe Bryant 2020, Dirk Nowitzki 2023);
    // its "jersey" column is unpopulated (NULL) so it can't supply retired-
    // number banners. A player can have multiple rows here (one per team
    // they were affiliated with) but `year` is identical across them.
    queryObjects(`SELECT DISTINCT year FROM stg_team_retired WHERE playerid = ? LIMIT 1`, [
      playerId,
    ]),
    // No career-level eFG% column exists; derive a games-weighted average
    // from the per-season advanced table. PER and Win Shares aren't present
    // anywhere in this database (BBR-proprietary metrics, not part of the
    // NBA stats API this warehouse is built from).
    queryObjects(
      `SELECT SUM(CASE WHEN avg_efg_pct BETWEEN 0 AND 1 THEN avg_efg_pct * gp END)
                / NULLIF(SUM(CASE WHEN avg_efg_pct BETWEEN 0 AND 1 THEN gp END), 0) AS career_efg_pct
       FROM agg_player_season_advanced
       WHERE player_id = ? AND season_type = 'Regular'`,
      [playerId],
    ),
    queryObjects(
      `SELECT count(*) AS n FROM fact_player_awards WHERE player_id = ? AND award_type = 'All-Star'`,
      [playerId],
    ),
    // All-NBA/All-Rookie/All-Defense rows are always real selections. The
    // lowercase award_types (nba mvp/roy/dpoy/mip/smoy) are voting *records*
    // — most rows are "received votes", only subtype1='Selected' rows are
    // actual wins (verified: LeBron's 4 real MVPs are exactly the 4 rows
    // with subtype1='Selected' out of ~16 nba-mvp rows total).
    queryObjects(
      `SELECT season, award_type
       FROM fact_player_awards
       WHERE player_id = ?
         AND (
           award_type IN ('All-NBA', 'All-Rookie', 'All-Defense')
           OR (award_type IN ('nba mvp', 'nba roy', 'nba dpoy', 'nba mip', 'nba smoy') AND subtype1 = 'Selected')
         )
       ORDER BY season`,
      [playerId],
    ),
    // League-leader "Champ" badges (e.g. BBR's "1974-75 TRB Champ"), regular
    // season only — statistical titles aren't awarded for the playoffs.
    // Each stat has an era cutoff below which agg_league_leaders' rank=1 is a
    // mass tie-at-zero artifact from before the NBA tracked that stat, not a
    // real title (verified: e.g. 253 different players "tied" for the 1968-69
    // steals lead, all at 0.0 — steals/blocks weren't recorded until 1973-74;
    // rebounds/assists were unreliable before 1950-51/1949-50 respectively).
    queryObjects(
      `SELECT season_year, 'Scoring Champ' AS label FROM agg_league_leaders
        WHERE player_id = ? AND season_type = 'Regular' AND pts_rank = 1
       UNION ALL
       SELECT season_year, 'Rebounding Champ' FROM agg_league_leaders
        WHERE player_id = ? AND season_type = 'Regular' AND reb_rank = 1 AND season_year >= '1950-51'
       UNION ALL
       SELECT season_year, 'Assists Champ' FROM agg_league_leaders
        WHERE player_id = ? AND season_type = 'Regular' AND ast_rank = 1 AND season_year >= '1949-50'
       UNION ALL
       SELECT season_year, 'Steals Champ' FROM agg_league_leaders
        WHERE player_id = ? AND season_type = 'Regular' AND stl_rank = 1 AND season_year >= '1973-74'
       UNION ALL
       SELECT season_year, 'Blocks Champ' FROM agg_league_leaders
        WHERE player_id = ? AND season_type = 'Regular' AND blk_rank = 1 AND season_year >= '1973-74'
       ORDER BY 1`,
      [playerId, playerId, playerId, playerId, playerId],
    ),
    // Jersey numbers worn per team-stint, for the BBR-style jersey graphic.
    // dim_player.jersey_number and bridge_player_team_season.jersey_number
    // are both stale — they show "23" for every LeBron James season
    // including his Miami Heat years, when he actually wore 6 there. The
    // real number is recovered from inactive_players (a per-game scratch/
    // DNP list that happens to capture the jersey worn at the time),
    // joined to `game` for the date. Verified against well-known real
    // history for LeBron and Curry (30 GSW throughout, one stint).
    //
    // bridge_player_team_season is used as a **per-player** fallback when
    // the player has zero rows in inactive_players at all (typically pre-
    // 1996-97 players, since inactive_players coverage starts 1996-97). It
    // is NOT used to fill individual season gaps for players who already
    // have inactive_players data — the stale bridge rows would inject
    // wrong numbers (e.g. MIA #23 for LeBron's 2011-12 / 2012-13 gap years
    // when he actually wore #6 the whole time, splitting his MIA#6 stint).
    //
    // The BBR-scraped roster file is the authoritative fallback when
    // inactive_players has no rows for the player. It is ranked ahead of
    // bridge_player_team_season per (team, season), because bridge can carry
    // stale current jersey numbers into historical seasons. Verified for
    // Pete Maravich (player 77459): bridge says ATL/BOS #7, while BBR roster
    // pages correctly say ATL/BOS #44.
    //
    // A plain GROUP BY (team_id, jersey_num) is wrong: LeBron wore 23 for
    // two non-contiguous Cleveland stints (2003-10, then 2014-18 after the
    // Miami years), which a naive group collapses into one bogus
    // "2003-2018" span. This needs gaps-and-islands grouping: bucket to one
    // (team, number) per season first (majority vote, for the rare in-season
    // trade/number-change), then split into a new group wherever the
    // (team, number) at position N in the chronological *filtered* sequence
    // differs from position N-1 — the classic
    // `ROW_NUMBER() OVER (ORDER BY season) - ROW_NUMBER() OVER (PARTITION BY team, number ORDER BY season)`
    // trick. Verified against LeBron: produces exactly 5 stints (CLE#23
    // 2003-10, MIA#6 2010-14, CLE#23 2014-18, LAL#23 2018-21, LAL#6
    // 2021-23), matching real history.
    //
    // Coverage starts 1996-97 (`inactive_players`' earliest game) and only
    // includes players/stints with at least one tracked inactive-game
    // appearance, so this is sparse/empty for some players — not a complete
    // jersey history. The INNER JOIN to dim_team_history both supplies the
    // team name/abbreviation and filters out non-franchise team_ids that
    // pollute inactive_players (All-Star teams, international exhibition
    // opponents like Real Madrid/CSKA), since those have no
    // dim_team_history row at all. dim_team_history itself only has rows
    // from 1996-97 onward, so for bridge-only pre-1996-07 stints the
    // valid_from range check fails for every row; the `is_current` clause
    // falls back to the current-era name for that team_id, and the QUALIFY
    // picks the earliest valid_from when a franchise has split rows
    // (NJN→BKN, SEA→OKC, etc.) so the pre-era stint gets the original name.
    // For jersey stints, the SELECT also consults dim_team.year_founded so
    // pre-1996 historical names such as New Orleans Jazz (warehouse
    // abbreviation NEO) can be displayed even though dim_team_history starts
    // at 1996-97.
    // SQL and params are built by buildJerseyQuery (above) so the
    // BBR roster CTE is spliced in only when its JSONL file
    // is on disk. The first two placeholders bind to per_game and
    // bridge_dedup; a third is added when the BBR CTE is present.
    (() => {
      const { sql, params } = buildJerseyQuery(playerId);
      return queryObjects(sql, params);
    })(),
  ]);

  const honorBadges: Badge[] = honorRows.map((r) => ({
    season: seasonRangeFromEndYear(r.season),
    label: HONOR_LABELS[String(r.award_type)] ?? String(r.award_type),
  }));
  const leaderBadges: Badge[] = leaderRows.map((r) => ({
    season: String(r.season_year),
    label: String(r.label),
  }));
  const badges = [...honorBadges, ...leaderBadges].sort((a, b) => a.season.localeCompare(b.season));

  const bio = bioRows[0] ? { ...bioRows[0], ...commonInfoRows[0] } : null;
  const hofYear = hofRows[0]?.year;
  const efg = efgRows[0]?.career_efg_pct;

  return {
    bio,
    career: career[0] ?? null,
    seasons,
    awards,
    draft: draft[0] ?? null,
    hallOfFameYear: hofYear !== undefined && hofYear !== null ? Number(hofYear) : null,
    isGreatest75: bio?.greatest_75_flag === "Y",
    allStarCount: Number(allStarRows[0]?.n ?? 0),
    careerEfgPct: efg !== undefined && efg !== null ? Number(efg) : null,
    badges,
    jerseyHistory: splitJerseyStintsByColorEra(
      jerseyRows.map((r) => ({
        team_id: Number(r.team_id),
        jersey_num: String(r.jersey_num),
        season_year: String(r.season_year),
        stint_group: Number(r.stint_group),
        abbreviation: String(r.abbreviation),
        team_name: String(r.team_name),
      })),
    ),
  };
}

// ---------------------------------------------------------------------------
// Teams
//
// dim_team_history (is_current=true) is the canonical "30 real franchises"
// list with clean names; dim_team has duplicate per-era rows for relocated
// franchises (e.g. Minneapolis vs Los Angeles Lakers) and always-NULL
// conference/division, so conference/division come from fact_standings
// instead.
// ---------------------------------------------------------------------------

export async function searchTeams(q: string, limit = 40): Promise<Row[]> {
  return queryObjects(
    `SELECT team_id, nickname AS team_name, city, abbreviation
     FROM dim_team_history
     WHERE is_current AND (nickname ILIKE ? OR city ILIKE ? OR abbreviation ILIKE ?)
     ORDER BY nickname
     LIMIT ?`,
    [`%${q}%`, `%${q}%`, `%${q}%`, limit],
  );
}

export interface TeamProfile {
  bio: Row | null;
  currentStanding: Row | null;
  seasons: Row[];
  franchiseHistory: Row[];
  recentGames: Row[];
}

export async function getTeamProfile(teamId: number): Promise<TeamProfile> {
  const teamIdStr = String(teamId);
  const [identity, extra, currentStanding, seasons, franchiseHistory, recentGames] =
    await Promise.all([
      queryObjects(`SELECT * FROM dim_team_history WHERE team_id = ? AND is_current LIMIT 1`, [
        teamId,
      ]),
      queryObjects(
        `SELECT arena, year_founded FROM dim_team WHERE team_id = ? ORDER BY year_founded DESC LIMIT 1`,
        [teamId],
      ),
      queryObjects(
        `SELECT * FROM fact_standings WHERE team_id = ? ORDER BY season_year DESC, season_type LIMIT 1`,
        [teamId],
      ),
      queryObjects(
        `SELECT * FROM agg_team_season WHERE team_id = ? ORDER BY season_year DESC, season_type`,
        [teamId],
      ),
      queryObjects(`SELECT * FROM dim_team_history WHERE team_id = ? ORDER BY valid_from`, [
        teamId,
      ]),
      queryObjects(
        `SELECT
         game_id,
         game_date,
         CASE WHEN team_id_home = ? THEN team_abbreviation_away ELSE team_abbreviation_home END AS opponent,
         CASE WHEN team_id_home = ? THEN 'Home' ELSE 'Away' END AS location,
         CASE WHEN team_id_home = ? THEN pts_home ELSE pts_away END AS team_pts,
         CASE WHEN team_id_home = ? THEN pts_away ELSE pts_home END AS opp_pts,
         CASE WHEN team_id_home = ? THEN wl_home ELSE wl_away END AS result
       FROM game
       WHERE team_id_home = ? OR team_id_away = ?
       ORDER BY game_date DESC
       LIMIT 20`,
        [teamIdStr, teamIdStr, teamIdStr, teamIdStr, teamIdStr, teamIdStr, teamIdStr],
      ),
    ]);
  const bio = identity[0] ? { ...identity[0], ...extra[0] } : null;
  return {
    bio,
    currentStanding: currentStanding[0] ?? null,
    seasons,
    franchiseHistory,
    recentGames,
  };
}

// ---------------------------------------------------------------------------
// Standings
// ---------------------------------------------------------------------------

export async function listStandingsSeasons(): Promise<string[]> {
  const rows = await queryObjects<{ season_year: string }>(
    `SELECT DISTINCT season_year FROM fact_standings ORDER BY season_year DESC`,
  );
  return rows.map((r) => r.season_year);
}

export async function getStandings(season: string, seasonType: string): Promise<Row[]> {
  // Era-matched the same way as player seasons (see getPlayerProfile), so
  // 1996-97 Seattle standings show "SuperSonics" rather than "Thunder".
  // fact_standings never has data before 1996-97, so unlike the player-season
  // query this always finds a match and needs no earliest-era fallback.
  return queryObjects(
    `SELECT s.*, th.nickname AS team_name, th.abbreviation
     FROM fact_standings s
     LEFT JOIN dim_team_history th
       ON th.team_id = s.team_id
       AND s.season_year >= th.valid_from
       AND (th.valid_to IS NULL OR s.season_year < th.valid_to)
     WHERE s.season_year = ? AND s.season_type = ?
     ORDER BY s.conference, s.conf_rank`,
    [season, seasonType],
  );
}

// ---------------------------------------------------------------------------
// Draft
// ---------------------------------------------------------------------------

export async function listDraftYears(): Promise<string[]> {
  const rows = await queryObjects<{ season: string }>(
    `SELECT DISTINCT season FROM draft_history ORDER BY season DESC`,
  );
  return rows.map((r) => r.season);
}

export async function getDraftYear(season: string): Promise<Row[]> {
  return queryObjects(`SELECT * FROM draft_history WHERE season = ? ORDER BY overall_pick`, [
    season,
  ]);
}

// ---------------------------------------------------------------------------
// Awards
// ---------------------------------------------------------------------------

export async function listAwardSeasons(): Promise<string[]> {
  const rows = await queryObjects<{ season: string }>(
    `SELECT DISTINCT season FROM fact_player_awards ORDER BY season DESC`,
  );
  return rows.map((r) => r.season);
}

export async function listAwardTypes(): Promise<string[]> {
  const rows = await queryObjects<{ award_type: string }>(
    `SELECT DISTINCT award_type FROM fact_player_awards ORDER BY award_type`,
  );
  return rows.map((r) => r.award_type);
}

export async function getAwards(season: string, awardType: string | null): Promise<Row[]> {
  const conditions = ["a.season = ?"];
  const params: DuckDBValue[] = [season];
  if (awardType) {
    conditions.push("a.award_type = ?");
    params.push(awardType);
  }
  return queryObjects(
    `SELECT a.*, p.full_name
     FROM fact_player_awards a
     LEFT JOIN dim_player p ON p.player_id = a.player_id AND p.is_current
     WHERE ${conditions.join(" AND ")}
     ORDER BY a.award_type, p.full_name`,
    params,
  );
}
