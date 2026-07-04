# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository. It is kept identical to [`AGENTS.md`](AGENTS.md) at the repo root, which serves the same purpose for other coding agents — update both together.

## ⚠️ Current state: the app and the warehouse are out of sync

As of 2026-07-04, `data/nba.duckdb` was rebuilt from scratch by `data/audit/build_nba.py` into a new layered schema (`src_*` / `dim_*` / `fact_*` / `map_*` / `mart_*` / `meta_*`, see below). **The web app's queries were written against the old, pre-rebuild schema and have not been updated yet** (`meta_known_gap.app_contract_not_preserved` documents this as intentional/deferred). Concretely:

- Many tables the app queries no longer exist as bare names (`agg_player_season`, `game`, `fact_game`, `fact_player_game_log`, ...) — they only exist under `src_*` prefixes now, or were replaced by differently-named/shaped `mart_*`/`fact_*` tables.
- Tables that share an old name, like `dim_player`, now have a different schema (no more `is_current` column, which `queries/players.ts` filters on).
- `web/test/data-hardening.test.ts` only skips its DB-backed suite when `data/nba.duckdb` is **absent** — since the file now exists (just with an incompatible schema), that suite will run and fail loudly instead of skipping.

Don't spend time debugging "why do all the queries return nothing/error" as if it's a regression — it's this schema swap. The fix is to rewrite `web/server/queries/*.ts` against the new schema (see the Warehouse layers section), which is planned but not yet done.

## What this repo is

An NBA data explorer: a DuckDB warehouse queried by a small Express API and rendered by a vanilla TypeScript/Vite frontend. No frameworks. The app lives entirely in `web/`; the repo root holds git-hook tooling (lefthook) plus the warehouse itself and the Python tooling that builds/ingests into it (`data/audit/`, `data/ingest/`).

**Data prerequisite:** `data/nba.duckdb` (gitignored; ~21.5 GB, 621 tables) must be present locally for the API to serve data and for the data-hardening tests to run.

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

Run a single test file: `npx vitest run test/dom.test.ts`
Filter by test name: `npx vitest run -t "fixture id or name"`

Pre-commit (lefthook, repo root `lefthook.yml`): eslint --fix, prettier --write, and typecheck run on staged `web/` files. CI (`.github/workflows/web.yml`) runs typecheck, lint, format:check, test, and build — all must pass.

Data layer (run from repo root; **stop the `web/` dev server first** — these need the DuckDB write lock):
```sh
python data/ingest/ingest.py <source>              # stage + resolve a manifest-driven external source (see data/ingest/sources/*.yaml)
python data/ingest/ingest.py <source> --resolve-only  # re-run crosswalk resolution without re-staging
python data/ingest/ingest.py --reconcile-bbr        # reconcile stale basketball_reference/json_slug player rows against bridge_player_bbr
python data/ingest/validate_bridges.py              # invariant checks on every bridge_* table (--fix dedupes)
python data/audit/build_nba.py --source-db <raw.duckdb> --replace   # full rebuild into data/nba.duckdb (needs ~60GB+ free disk)
python data/audit/build_nba.py --source-db <raw.duckdb> --replace --source-mode view --skip-source-hashes  # fast smoke-test rebuild, no copy
```

## Architecture

### Warehouse layers (`data/nba.duckdb`, built by `data/audit/build_nba.py`)

The warehouse is layered, and `meta_*` tables are the source of truth for what's trustworthy:

- `src_*` — lossless copies of every table from the raw source warehouse, with provenance columns (`_ingest_run_id`, `_source_system`, `_source_table`, `_source_record_hash`, `_normalized_game_id`).
- `map_*` — standardized id crosswalks (player/team/game ↔ Kaggle/ESPN/Basketball-Reference), e.g. `map_player_source_id`, `map_player_bbr`, `map_team_source_id`, `map_game_source_id`.
- `dim_*` / `fact_*` — canonical dimensions and facts rebuilt from the source layer.
- `mart_*` / `analytics_*` — convenience marts built from the canonical facts (e.g. `mart_player_season`, `mart_league_leaders`).
- `meta_build_run` — one row per rebuild invocation.
- `meta_table_fate` — classifies **every** raw source table as `canonical_source` / `derived_rebuild` / `lossless_source_only` / `legacy_do_not_use` / `duplicate_superseded` / `empty_endpoint_shell` — check this before trusting an unfamiliar table.
- `meta_quality_check` — pass/fail gates the builder runs every time (row parity, no player-season fan-out, resolution floors, specific known-player sanity checks like Luka Doncic's awards, etc.).
- `meta_known_gap` — plain-English register of known, accepted gaps (e.g. `app_contract_not_preserved`, `bbr_bridge_residual_unresolved_players`) with a `status` (`documented` / `expected` / `backlog` / `resolved_in_v1` / `intentional`).
- `meta_column_lineage` / `meta_metric_definition` — traces canonical columns back to source columns, and documents canonical metric formulas (e.g. `ts_pct`).

**There's no default `--source-db`** for `build_nba.py`: `data/nba.duckdb` is itself a build output now, and the raw warehouse it was built from has been archived outside this repo. You must pass `--source-db` explicitly pointing at a raw warehouse to rebuild from scratch.

### Ingestion (`data/ingest/`)

Manifest-driven loader for pulling external files into the warehouse: `ingest.py <source>` stages files as `stg_<source>_*` tables, registers the source in `dim_source_system`, and resolves ids into the generic crosswalks `bridge_player_source_id` / `bridge_team_source_id` / `bridge_game_source_id` (unmatched ids kept as `is_unresolved` rows with a populated `unresolved_reason`, never silently dropped). Adding a source = writing `data/ingest/sources/<name>.yaml`.

Two maintenance passes run alongside the per-entity resolvers:
- `reconcile_player_bbr_matches` (`--reconcile-bbr`) fixes stale `basketball_reference`/`json_slug` rows in `bridge_player_source_id` by reusing `bridge_player_bbr`'s games-played tie-break — needed because `dim_all_players` contains phantom duplicate person ids from NBA teams' preseason exhibition games abroad, which the exact-id/name matchers can't disambiguate on their own.
- `classify_kaggle_unresolved_players` (runs automatically for the `kaggle_nba` source) splits generic "unresolved" Kaggle player rows into `non_player_official_or_unassigned_staff`, `non_player_nba_staff`, `exhibition_opponent_non_nba_player`, or a residual generic fallback, by joining back to the play-by-play `teamId` and checking it against the NBA franchise id range (`1610000000`–`1611000000`).

`validate_bridges.py` checks invariants on every `bridge_*` table (uniqueness, resolved-ids-exist-in-dim, flags/reason consistency, BBR reconciliation completeness) — run it after any ingest or bridge edit.

### Server (`web/server/`)

- `index.ts` — all Express routes. Thin: validates params (`clampLimit`, integer id checks), delegates to `queries/*.ts`, wraps handlers in `asyncRoute` for consistent JSON 500s. **Route-ordering convention:** literal-segment routes (e.g. `/api/players/featured`, `/api/teams/by-conference`) must be registered before their `/:id` param siblings. Game ids are 10-char zero-padded numeric strings.
- `queries/` — every SQL query against the warehouse, split by concern (`players.ts`, `teams.ts`, `game.ts`, `standings.ts`, `draft.ts`, `awards.ts`, `leaders.ts`, `betting.ts`, `fourFactors.ts`, `matchups.ts`, `gameFlow.ts`, plus shared helpers in `shared.ts`); `queries.ts` is just a barrel re-export. This is where nearly all business logic lives — and where the schema-mismatch fixes above will need to land.
- `db.ts` — single READ_ONLY DuckDB connection (singleton promise). `queryObjects` for parameterized SELECTs; converts BigInt to Number (or string when unsafe) so results survive `JSON.stringify`. DB path: `DUCKDB_PATH` env override, else `../../data/nba.duckdb`.
- `photos.ts` — proxies NBA CDN headshots with a disk cache at `web/.cache/photos/`. The CDN returns a ~5 KB silhouette (HTTP 200) for players without photos, so a 10 KB size threshold distinguishes real photos; both outcomes are cached (`.png` / `.none`).
- `teamColorEras.ts` — generated era-accurate franchise colors keyed by team_id and year; used to color player/team UI per season.
- `/api/admin/*` — generic table browser + arbitrary read-only SQL, a developer escape hatch not used by the UI (the READ_ONLY connection rejects DDL/DML at the engine level).

### Supplemental data (`data/anchors/`)

Basketball-Reference JSONL scrapes read **at request time** via DuckDB `read_json_auto` (no server restart needed after re-scraping; paths overridable via `BBR_JERSEYS_PATH` / `BBR_COACHES_PATH`, CTEs omitted entirely if the files are missing):

- `bbr_jerseys.jsonl` — jersey numbers. Per-season jersey sources rank: `inactive_players` (1) > BBR jerseys (2) > `bridge_player_team_season` (3) — see `data/anchors/README.md` for the bridge-suppression rule (bridge rows can leak a player's *current* number into historical seasons).
- `bbr_coaches.jsonl` — the only source of historical coach-by-season data.

Both are also materialized as warehouse tables by `data/audit/build_coach_jersey_tables.sql` (re-run after re-scraping): `fact_coach_season` and `fact_player_jersey_season` (jersey rows carry a `source` tier). The script header documents known accuracy limits and warns the ESPN/cumulative-stats jersey columns are current-number backfills, never usable for history.

### Frontend (`web/src/`)

Vanilla TS, no framework. `main.ts` owns a tab-based SPA: each view in `src/views/` exports a `render(container, detailId?)` function registered in the `TABS` array. Some tabs are `hidden` (search results, game detail) — reachable only via navigation, not the tab bar. Cross-view navigation is done by dispatching a `nba:navigate` CustomEvent (`{ tab, id? }`) on `window`. `dom.ts` provides the `el()` element builder and `announceStatus` (aria-live); `api.ts` holds the shared response types; `headerSearch.ts` is the persistent global search in the header.

### Testing (`web/test/`)

The main suite is **fixture-driven data hardening** (`data-hardening.test.ts`): JSON fixtures under `test/fixtures/` are auto-discovered by `fixtures/manifest.ts` (via `import.meta.glob` — drop a JSON file in, no manifest edit needed). Each fixture asserts a known-true datapoint against the live warehouse. Fixture `status`:

- `"stable"` — must pass now.
- `"regression"` — documents a known open bug; registered as `test.fails`, so it flips RED when someone fixes the underlying query (the prompt to flip it back to `"stable"`).

The DB-backed suite skips only when `data/nba.duckdb` is **absent** (an existence check, not a schema/connectivity check — see the warning at the top of this file). `dom.test.ts` runs under jsdom regardless of the DB.
