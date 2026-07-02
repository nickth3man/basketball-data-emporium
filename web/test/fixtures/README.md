# Data-hardening fixtures

This directory holds golden-value fixtures that pin specific datapoints
in the NBA warehouse (`data/nba.duckdb`) to known-good values sourced
from Basketball-Reference. The point is to catch silent regressions in
the curated queries under `web/server/queries.ts` — when a resolver
breaks and a value drifts, the matching fixture turns red.

The suite is auto-discovered: drop a JSON file anywhere under this
directory and it will be picked up at the next `npm run test` run. You
do **not** need to edit `manifest.ts` (or any other file) to add a
fixture. The only required additions to the test suite are this README
and the JSON fixture itself.

## Layout

```
fixtures/
  manifest.ts            # DataFixture type, isDataFixture guard, loadAllFixtures
  README.md              # this file
  jerseys/               # one folder per datapoint_class
    _seed_curry.json     # seed fixture — owned by the foundation, do not delete
    _seed_griffin.json   # seed fixture — owned by the foundation, do not delete
    <your-jersey-fixture>.json
  career_totals/         # future
  ...
```

Conventions:

- One folder per `datapoint_class`. Name it after the snake_case class
  in `manifest.ts`.
- Seed/proof fixtures are prefixed `_seed_` and are owned by the
  foundation. They prove each of the `assertion_mode` paths work end
  to end. Do not delete or rename them.
- Everything else is fair game for any contributor to add.

## The `DataFixture` schema (pinned)

`manifest.ts` exports this interface. It is the contract — copy it
verbatim when adding a fixture:

```ts
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
  | "hall_of_fame";

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

export interface QueryTarget {
  fn?: string; // export name in queries.ts (query_fn mode)
  params?: unknown[]; // positional args
  sql?: string; // raw SQL (raw_sql mode)
  extract?: string; // dot/bracket path into the result, e.g. "career.gp" or "jerseyHistory[0].jersey_num"
  composite?: Array<{
    mode: AssertionMode;
    fn?: string;
    params?: unknown[];
    sql?: string;
    extract?: string;
  }>;
}

export interface DataFixture {
  id: string; // unique, e.g. "jersey.griffin_lac_32"
  datapoint_class: DatapointClass;
  entity: string; // human description
  expected: unknown; // golden value (scalar | object | array)
  bbr_source_url: string; // required citation
  assertion_mode: AssertionMode;
  query_target: QueryTarget;
  match: MatchMode; // explicit — no default-by-type
  tolerance?: number; // decimal places for closeTo (toBeCloseTo precision)
  status: FixtureStatus; // "stable" = must pass; "regression" = currently fails (test.fails)
  confidence: "verified" | "spot-checked";
  notes?: string;
  skip_if_no_db?: boolean; // default true
}
```

### Required fields

- `id` — must be unique. Convention: `<class>.<slug>`. The seed uses
  `jersey.seed_curry_gsw_30`, `jersey.seed_griffin_lac_32`.
- `bbr_source_url` — **required**. Every `expected` value must trace back
  to a BBR page. The whole point of this suite is the citation chain;
  don't add a fixture without one.
- `match` — explicit. There is no default-by-type. Pick the right one
  for the shape of your expected value.
- `status` — see "Stable vs regression" below.
- `confidence` — `verified` means you checked the BBR page yourself;
  `spot-checked` means you trust the value but haven't re-checked.

## `match` modes

| Mode                | What it asserts                                                                           |
| ------------------- | ----------------------------------------------------------------------------------------- |
| `equals`            | Deep equality. Use for scalars and small objects.                                         |
| `closeTo`           | Numeric proximity (vitest `toBeCloseTo`); use `tolerance` for precision.                  |
| `containsObject`    | Actual array contains an object matching all of `expected`'s fields (`objectContaining`). |
| `notContainsObject` | Actual array contains no object matching `expected`.                                      |
| `arrayContains`     | Actual array is a superset of `expected` (full element equality, not partial).            |
| `objectMatching`    | Actual object has at least the fields in `expected` (`toMatchObject`).                    |
| `length`            | Actual array's length equals `Number(expected)`.                                          |
| `gte`               | Actual number is `>= Number(expected)`.                                                   |

## `assertion_mode` modes

- `query_fn` — call a named export from `web/server/queries.ts`. Set
  `query_target.fn` (the export name) and `query_target.params`
  (positional args). The result is then walked with `extract` to
  produce the actual value.
- `raw_sql` — run a raw SELECT via `queryObjects`. Set
  `query_target.sql`. The result is a row array; use `extract` to
  pull a specific row/column.
- `composite` — run several sub-targets in order; the actual value
  becomes an array of sub-results. Useful when a single assertion
  needs evidence from more than one place.

## `extract` paths

`extract` is a dot/bracket walk into the query result:

- `career.gp` → `result.career.gp`
- `jerseyHistory[0].jersey_num` → `result.jerseyHistory[0].jersey_num`
- `[0].career_gp` → `result[0].career_gp` (typical for `raw_sql`, which returns a row array)
- empty/missing → the value is returned unchanged

Missing segments return `undefined` (no throw). Fixtures that index
into unpopulated data should still be runnable.

## Stable vs regression

| `status`     | Test registration | When to use                                                                                                                                                                                                                             |
| ------------ | ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `stable`     | `test(...)`       | The data is correct now and must stay correct. The test fails the moment the value drifts.                                                                                                                                              |
| `regression` | `test.fails(...)` | There is a known bug in the underlying query — the data is currently wrong. The test is GREEN while the bug is open, and turns RED the moment the bug is fixed. That RED is the prompt to flip the fixture's `status` back to `stable`. |

This is the most important rule in the suite: a `regression` fixture
documents a known bug, not an aspirational test. Don't promote a
regression to stable without verifying the underlying fix.

## Copy-paste template — jersey fixture

```json
{
  "id": "jersey.<player>_<team>_<num>",
  "datapoint_class": "jersey",
  "entity": "Human-readable description of the datapoint",
  "expected": { "abbreviation": "LAC", "start_year": 2010, "jersey_num": "32" },
  "bbr_source_url": "https://www.basketball-reference.com/teams/LAC/numbers.html",
  "assertion_mode": "query_fn",
  "query_target": { "fn": "getPlayerProfile", "params": [201933], "extract": "jerseyHistory" },
  "match": "containsObject",
  "status": "stable",
  "confidence": "verified",
  "notes": "Why this datapoint matters.",
  "skip_if_no_db": true
}
```

`jerseyHistory` items look like:
`{ team_id, abbreviation, team_name, jersey_num, start_year, end_year, primary, trim }`.

## Copy-paste template — raw_sql career-total fixture

```json
{
  "id": "career_total.<player>_<stat>",
  "datapoint_class": "career_total",
  "entity": "Career total of <stat> for <player>",
  "expected": 1234,
  "bbr_source_url": "https://www.basketball-reference.com/players/<id>/<slug>.html",
  "assertion_mode": "raw_sql",
  "query_target": {
    "sql": "SELECT SUM(gp) AS career_gp FROM agg_player_season WHERE player_id = ? AND season_type = 'Regular'",
    "params": [201939],
    "extract": "[0].career_gp"
  },
  "match": "closeTo",
  "tolerance": 0,
  "status": "stable",
  "confidence": "verified",
  "notes": "Why this datapoint matters.",
  "skip_if_no_db": true
}
```

Note: for `raw_sql` with parameterised queries, prefer calling a
curated export in `queries.ts` instead — it centralises the parameter
binding. Reach for `raw_sql` only when there is no curated export that
returns the value you need.

## Running

From `web/`:

```sh
npm run test                  # runs the full suite
npm run test -- data-hardening  # just the fixture suite
```

The suite is skipped entirely if `data/nba.duckdb` is missing from
the repo root (e.g. in CI), so missing data never breaks the build.
