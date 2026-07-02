/// <reference types="vite/client" />
// Fixture manifest. Discovers every JSON fixture under this directory via
// Vite's import.meta.glob (Vitest reuses Vite's module graph), filters to
// entries that satisfy the `DataFixture` shape, and exposes them through
// `loadAllFixtures` / `loadFixture`.
//
// To add a new fixture: drop a JSON file anywhere under this directory
// using the `DataFixture` shape below. No manifest edit needed.

export type DatapointClass =
  | "jersey"
  | "career_total"
  | "season_line"
  | "mvp"
  | "roy"
  | "dpoy"
  | "sixth_man"
  | "mip"
  | "all_nba_count"
  | "all_star_count"
  | "draft_first_pick"
  | "standings_record"
  | "current_roster"
  | "playoff_series"
  | "finals_result"
  | "player_bio"
  | "team_identity"
  | "famous_game_line"
  | "greatest75"
  | "hall_of_fame"
  | "all_defense_count"
  | "coach_season"
  | "team_season_stat"
  | "franchise_history"
  | "player_high"
  | "player_draft_combine"
  | "team_opponent_stat"
  | "team_rank"
  | "team_recent_game"
  | "list_metadata"
  | "player_on_off_split"
  | "player_shot_split"
  | "player_advanced_stat"
  | "player_similar"
  | "retired_number"
  | "playoff_series_stat"
  | "league_leader_season"
  | "league_leader_all_time"
  | "franchise_leader"
  | "player_season_rank"
  | "draft_value";

export type AssertionMode = "query_fn" | "raw_sql" | "composite";
export type MatchMode =
  | "equals"
  | "closeTo"
  | "containsObject"
  | "notContainsObject"
  | "arrayContains"
  | "objectMatching"
  | "length"
  | "gte";
export type FixtureStatus = "stable" | "regression";

export interface CompositeSubTarget {
  mode: AssertionMode;
  fn?: string;
  params?: unknown[];
  sql?: string;
  extract?: string;
}

export interface QueryTarget {
  /** Export name in `web/server/queries.ts` (query_fn mode). */
  fn?: string;
  /** Positional args to the export. */
  params?: unknown[];
  /** Raw SQL (raw_sql mode). */
  sql?: string;
  /** Dot/bracket path into the result, e.g. "career.gp" or "jerseyHistory[0].jersey_num". */
  extract?: string;
  /** Sub-targets run in order, results returned as an array (composite mode). */
  composite?: CompositeSubTarget[];
}

export interface DataFixture {
  id: string;
  datapoint_class: DatapointClass;
  entity: string;
  expected: unknown;
  bbr_source_url: string;
  assertion_mode: AssertionMode;
  query_target: QueryTarget;
  match: MatchMode;
  tolerance?: number;
  status: FixtureStatus;
  confidence: "verified" | "spot-checked";
  notes?: string;
  skip_if_no_db?: boolean;
}

const DATAPOINT_CLASSES: ReadonlySet<string> = new Set<DatapointClass>([
  "jersey",
  "career_total",
  "season_line",
  "mvp",
  "roy",
  "dpoy",
  "sixth_man",
  "mip",
  "all_nba_count",
  "all_star_count",
  "draft_first_pick",
  "standings_record",
  "current_roster",
  "playoff_series",
  "finals_result",
  "player_bio",
  "team_identity",
  "famous_game_line",
  "greatest75",
  "hall_of_fame",
  "all_defense_count",
  "coach_season",
  "team_season_stat",
  "franchise_history",
  "player_high",
  "player_draft_combine",
  "team_opponent_stat",
  "team_rank",
  "team_recent_game",
  "list_metadata",
  "player_on_off_split",
  "player_shot_split",
  "player_advanced_stat",
  "player_similar",
  "retired_number",
  "playoff_series_stat",
  "league_leader_season",
  "league_leader_all_time",
  "franchise_leader",
  "player_season_rank",
  "draft_value",
]);

const ASSERTION_MODES: ReadonlySet<string> = new Set<AssertionMode>([
  "query_fn",
  "raw_sql",
  "composite",
]);

const MATCH_MODES: ReadonlySet<string> = new Set<MatchMode>([
  "equals",
  "closeTo",
  "containsObject",
  "notContainsObject",
  "arrayContains",
  "objectMatching",
  "length",
  "gte",
]);

const FIXTURE_STATUSES: ReadonlySet<string> = new Set<FixtureStatus>(["stable", "regression"]);

const CONFIDENCE_LEVELS: ReadonlySet<string> = new Set<"verified" | "spot-checked">([
  "verified",
  "spot-checked",
]);

export function isDataFixture(x: unknown): x is DataFixture {
  if (typeof x !== "object" || x === null) return false;
  const f = x as Record<string, unknown>;
  if (typeof f.id !== "string" || f.id.length === 0) return false;
  if (typeof f.datapoint_class !== "string" || !DATAPOINT_CLASSES.has(f.datapoint_class))
    return false;
  if (typeof f.entity !== "string") return false;
  if (typeof f.bbr_source_url !== "string" || f.bbr_source_url.length === 0) return false;
  if (typeof f.assertion_mode !== "string" || !ASSERTION_MODES.has(f.assertion_mode)) return false;
  if (typeof f.match !== "string" || !MATCH_MODES.has(f.match)) return false;
  if (typeof f.status !== "string" || !FIXTURE_STATUSES.has(f.status)) return false;
  if (typeof f.confidence !== "string" || !CONFIDENCE_LEVELS.has(f.confidence)) return false;
  if (typeof f.query_target !== "object" || f.query_target === null) return false;
  return true;
}

// Eagerly load every JSON file under this directory. The keys are
// resolved module paths; the values are the imported module's namespace.
// JSON files imported this way expose their parsed body as `.default`.
const rawFixtures = import.meta.glob<{ default: unknown }>("./**/*.json", { eager: true });

const allFixtures: DataFixture[] = [];
for (const mod of Object.values(rawFixtures)) {
  if (isDataFixture(mod.default)) {
    allFixtures.push(mod.default);
  }
}

export function loadAllFixtures(): DataFixture[] {
  return allFixtures.slice();
}

export function loadFixture(id: string): DataFixture | undefined {
  return allFixtures.find((f) => f.id === id);
}
