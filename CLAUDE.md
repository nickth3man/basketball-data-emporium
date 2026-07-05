# CLAUDE.md

Orientation for coding agents. Kept identical to [`AGENTS.md`](AGENTS.md) (same content, for other coding agents) — update both together.

## How to use this file (read first)

This file is **orientation only**: how to run things, where code lives, how the pieces connect. It is deliberately **not** a record of current data state — no coverage claims, row counts, bug lists, or migration status. Such prose goes stale fast and gets trusted as fact.

**The live warehouse and the server source are the only source of truth.** Before asserting or acting on a data assumption, verify it directly:

- **Warehouse state** → query the `meta_*` tables in `data/nba.duckdb` (use `-readonly` to avoid contending with the dev server's READ_ONLY connection):

  ```sh
  duckdb data/nba.duckdb -readonly -list -c "SELECT gap_key, status FROM meta_known_gap ORDER BY gap_key"
  duckdb data/nba.duckdb -readonly -list -c "SELECT check_name, status FROM meta_quality_check"
  duckdb data/nba.duckdb -readonly -list -c "SELECT original_table, fate FROM meta_table_fate"
  duckdb data/nba.duckdb -readonly -list -c "SELECT target_column, source_table, source_column FROM meta_column_lineage"
  ```

  - `meta_known_gap` — register of known gaps with a `status` (`resolved` / `documented` / `backlog` / `expected` / `intentional` / …). **Authoritative.** Check here before "fixing" something, and never trust a gap list written elsewhere (this file included).
  - `meta_quality_check` — pass/fail gates from the last build.
  - `meta_table_fate` — whether a table is `canonical_source` / `derived_rebuild` / `lossless_source_only` / `legacy_do_not_use` / `duplicate_superseded` / `empty_endpoint_shell`. Check before trusting an unfamiliar table.
  - `meta_column_lineage` / `meta_metric_definition` — where a canonical column comes from and how a metric is computed.
- **App behavior** → read the code: `web/server/queries/*.ts` (all SQL + business logic) and `web/server/index.ts` (routes). Don't infer behavior from documentation.

## What this repo is

An NBA data explorer: a DuckDB warehouse queried by a small Express API and rendered by a vanilla TypeScript/Vite frontend (no frameworks). The app lives in `web/`; the repo root holds git-hook tooling (lefthook) plus the warehouse and the Python build/ingest tooling (`data/audit/`, `data/ingest/`).

**Data prerequisite:** `data/nba.duckdb` (gitignored; ~21.5 GB) must be present locally for the API to serve data and for the data-hardening tests to run.

## File tree

```
.
├── AGENTS.md / CLAUDE.md       # this file (kept identical)
├── lefthook.yml                 # pre-commit: eslint --fix + prettier --write + typecheck on staged web/ files
├── data/
│   ├── nba.duckdb               # gitignored warehouse (build output)
│   ├── anchors/                 # BBR JSONL scrapes + wikidata_player_deaths.jsonl + scrape scripts
│   ├── audit/                   # warehouse build: build_nba.py (builder) + *.sql stages + player_crosswalk_overrides.json
│   ├── external/
│   └── ingest/                  # manifest loader: ingest.py, validate_bridges.py, sources/*.yaml
└── web/
    ├── index.html  vite.config.ts
    ├── server/                  # Express API
    │   ├── index.ts             # all routes (thin: param validation → queries/*.ts)
    │   ├── db.ts                # single READ_ONLY DuckDB connection (singleton)
    │   ├── photos.ts            # NBA CDN headshot proxy + disk cache
    │   ├── teamColorEras.ts     # generated era-accurate franchise colors
    │   ├── queries.ts           # barrel re-export
    │   └── queries/             # ALL SQL/business logic, split by domain
    ├── src/                     # vanilla-TS frontend
    │   ├── main.ts              # tab-based SPA owner (TABS array)
    │   ├── api.ts dom.ts headerSearch.ts style.css
    │   └── views/               # one render(container, detailId?) per tab/view
    └── test/                    # vitest: data-hardening.test.ts (fixture-driven), dom.test.ts (jsdom), fixtures/
```

## Commands

Root (repo-level tooling only):

```sh
npm install   # installs lefthook via the "prepare" script
```

App (run inside `web/`):

```sh
npm run dev            # Express API (:8787) + Vite dev server (:5173) together; Vite proxies /api/* to the API
npm run typecheck      # tsc --noEmit
npm run lint           # eslint .  (lint:fix to autofix)
npm run format         # prettier --write .  (format:check in CI)
npm run test           # vitest run
npm run build          # production build
```

Single test file: `npx vitest run test/dom.test.ts` · filter by name: `npx vitest run -t "fixture id or name"`

Pre-commit (lefthook): eslint --fix, prettier --write, typecheck on staged `web/` files. CI (`.github/workflows/web.yml`): typecheck, lint, format:check, test, build — all must pass.

Data layer (run from repo root; **stop the `web/` dev server first** — these need the DuckDB write lock):

```sh
python data/ingest/ingest.py <source>                 # stage + resolve a manifest-driven source (data/ingest/sources/*.yaml)
python data/ingest/ingest.py <source> --resolve-only  # re-run crosswalk resolution without re-staging
python data/ingest/ingest.py --reconcile-bbr          # reconcile stale basketball_reference/json_slug player rows vs bridge_player_bbr
python data/ingest/validate_bridges.py                # invariant checks on every bridge_* table (--fix dedupes)
python data/audit/build_nba.py --source-db <raw.duckdb> --replace                # full rebuild (needs ~60GB+ free disk)
python data/audit/build_nba.py --source-db <raw.duckdb> --replace --source-mode view --skip-source-hashes  # fast smoke-test rebuild
```

`build_nba.py` has **no default `--source-db`**: `data/nba.duckdb` is itself a build output, and the raw warehouse it was built from is archived outside the repo. Pass `--source-db` explicitly to rebuild from scratch.

## Architecture

### Warehouse layers (built by `data/audit/build_nba.py`)

Layered schema; the `meta_*` tables are the source of truth (see "How to use this file" above):

- `src_*` — lossless copies of every raw source table (provenance cols: `_ingest_run_id`, `_source_system`, `_source_table`, `_source_record_hash`, `_normalized_game_id`).
- `map_*` — standardized id crosswalks (player/team/game ↔ Kaggle/ESPN/Basketball-Reference): `map_player_source_id`, `map_player_bbr`, `map_team_source_id`, `map_game_source_id`.
- `dim_*` / `fact_*` — canonical dimensions and facts, rebuilt from the source layer.
- `mart_*` / `analytics_*` — convenience marts built from canonical facts.
- `meta_*` — build provenance and trust gates: `meta_build_run`, `meta_table_fate`, `meta_quality_check`, `meta_known_gap`, `meta_column_lineage`, `meta_metric_definition`.

### Ingestion (`data/ingest/`)

Manifest-driven loader: `ingest.py <source>` stages files as `stg_<source>_*` tables, registers the source in `dim_source_system`, and resolves ids into `bridge_player_source_id` / `bridge_team_source_id` / `bridge_game_source_id` (unmatched ids kept as `is_unresolved` rows with `unresolved_reason`, never silently dropped). Adding a source = writing `data/ingest/sources/<name>.yaml`.

Two maintenance passes run alongside the per-entity resolvers:

- `reconcile_player_bbr_matches` (`--reconcile-bbr`) fixes stale `basketball_reference`/`json_slug` rows in `bridge_player_source_id` using `bridge_player_bbr`'s games-played tie-break — needed because `dim_all_players` contains phantom duplicate person ids from NBA teams' preseason exhibition games abroad.
- `classify_kaggle_unresolved_players` (auto-runs for `kaggle_nba`) splits generic "unresolved" Kaggle player rows into `non_player_official_or_unassigned_staff` / `non_player_nba_staff` / `exhibition_opponent_non_nba_player` / residual fallback, by checking play-by-play `teamId` against the NBA franchise id range (`1610000000`–`1611000000`).

`validate_bridges.py` checks invariants on every `bridge_*` table — run it after any ingest or bridge edit.

### Supplemental data (`data/anchors/`)

Basketball-Reference JSONL scrapes (`bbr_jerseys.jsonl`, `bbr_coaches.jsonl`) and the Wikidata death-date anchor (`wikidata_player_deaths.jsonl`, consumed by `build_nba.py`'s `dim_player` builder) live here alongside the scrape scripts that regenerate them. The BBR jersey/coach scrapes are materialized into warehouse tables by `data/audit/build_coach_jersey_tables.sql` (`fact_coach_season`, `fact_player_jersey_season`) — re-run that script after re-scraping; the app reads the warehouse tables, not the JSONL, at request time.

### Server (`web/server/`)

- `index.ts` — all Express routes. Thin: validates params (`clampLimit`, integer id checks), delegates to `queries/*.ts`, wraps handlers in `asyncRoute` for consistent JSON 500s. **Route-ordering convention:** literal-segment routes (e.g. `/api/players/featured`, `/api/teams/by-conference`) must be registered before their `/:id` param siblings. Game ids are 10-char zero-padded numeric strings.
- `queries/*.ts` — every SQL query and nearly all business logic, split by domain (see file tree). `queries.ts` is a barrel re-export.
- `db.ts` — single READ_ONLY DuckDB connection (singleton promise). `queryObjects` for parameterized SELECTs; converts BigInt to Number (or string when unsafe) so results survive `JSON.stringify`. DB path: `DUCKDB_PATH` env override, else `../../data/nba.duckdb`.
- `photos.ts` — proxies NBA CDN headshots with a disk cache at `web/.cache/photos/`. The CDN returns a ~5 KB silhouette (HTTP 200) for players without photos; a 10 KB size threshold distinguishes real photos. Both outcomes are cached (`.png` / `.none`).
- `teamColorEras.ts` — generated era-accurate franchise colors keyed by team_id and year.
- `/api/admin/*` — generic table browser + arbitrary read-only SQL, a developer escape hatch not used by the UI (the READ_ONLY connection rejects DDL/DML at the engine level).

### Frontend (`web/src/`)

Vanilla TS, no framework. `main.ts` owns a tab-based SPA: each view in `src/views/` exports a `render(container, detailId?)` registered in the `TABS` array. Some tabs are `hidden` (search results, game detail, analytics-hub tools: betting, four-factors, matchups, clutch, officials, coaching, franchise-leaders) — reachable only via navigation, not the tab bar; the analytics tools are also linked from the `analytics` tab's hub (`views/analytics.ts`'s `ANALYTICS_TOOLS`). Cross-view navigation: dispatch a `nba:navigate` CustomEvent (`{ tab, id? }`) on `window`. `dom.ts` provides the `el()` builder + `announceStatus` (aria-live); `api.ts` holds shared response types; `headerSearch.ts` is the persistent global search.

### Testing (`web/test/`)

The main suite is **fixture-driven data hardening** (`data-hardening.test.ts`): JSON fixtures under `test/fixtures/` are auto-discovered by `fixtures/manifest.ts` (via `import.meta.glob` — drop a JSON file in, no manifest edit). Each fixture asserts a known-true datapoint against the live warehouse. Fixture `status`:

- `"stable"` — must pass now.
- `"regression"` — documents a known open bug; registered as `test.fails`, so it flips RED when someone fixes the underlying query (the prompt to flip it back to `"stable"`).

The DB-backed suite skips only when `data/nba.duckdb` is **absent** (existence check, not schema/connectivity). `dom.test.ts` runs under jsdom regardless of the DB.
