# AGENTS.md

This file provides guidance to coding agents when working with code in this repository. It is kept identical to [`CLAUDE.md`](CLAUDE.md) at the repo root, which serves the same purpose for Claude Code — update both together.

## Schema migration status (2026-07-04 warehouse rebuild)

`data/nba.duckdb` was rebuilt from scratch by `data/audit/build_nba.py` into a layered schema (`src_*` / `dim_*` / `fact_*` / `map_*` / `mart_*` / `meta_*`, see below). `web/server/queries/*.ts` has since been rewritten against the new schema — every query file (`players.ts`, `teams.ts`, `game.ts`, `standings.ts`, `draft.ts`, `awards.ts`, `leaders.ts`, `betting.ts`, `fourFactors.ts`, `matchups.ts`, `gameFlow.ts`, plus `shared.ts`) now reads canonical `dim_*`/`fact_*`/`mart_*` tables where one exists, or the matching `src_*`-prefixed lossless copy (joined back to canonical ids via `map_player_bbr`/`map_team_bbr` where needed) where no canonical replacement was built. Three new hidden analytics tabs (`officials`, `coaching`, `franchise-leaders`) surface `fact_official_assignment`/`dim_official`, `fact_coach_season`, and `mart_franchise_leaders` respectively, which previously had no UI.

Known, permanent (non-bug) coverage gaps carried through the migration — don't try to "fix" these:
- `fact_official_assignment` only covers the 2025-26 season (a live/current-season feed).
- `fact_draft` stops at the 2023 draft class (2024/2025 not yet ingested).
- A handful of `fact_draft` picks retain a `draft_slot_status='duplicate_source_slot'` numbering bug (e.g. 2002 pick 35/36) — flagged but not renumbered.
- 33 `dim_game` All-Star rows have a corrupted `season_year` (parsed as 20YY instead of 19YY) — any season-range query built on `dim_game`/`fact_pbp_event`/etc. should apply `shared.ts`'s `DIM_GAME_SEASON_GUARD_SQL` to exclude them.
- `fact_award` is missing at least one pre-1951 no-id award winner present in `src_stg_bref_player_award_shares` (e.g. the 1950 ROY).

If you find a query still referencing an old bare table name (`agg_player_season`, `game`, `fact_game_log`, ...), that's a leftover to fix, not expected behavior — the migration should be complete.

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
- `queries/` — every SQL query against the warehouse, split by concern (`players.ts`, `teams.ts`, `game.ts`, `standings.ts`, `draft.ts`, `awards.ts`, `leaders.ts`, `betting.ts`, `fourFactors.ts`, `matchups.ts`, `gameFlow.ts`, `leaderboards.ts`, plus shared helpers in `shared.ts`); `queries.ts` is just a barrel re-export. This is where nearly all business logic lives.
- `db.ts` — single READ_ONLY DuckDB connection (singleton promise). `queryObjects` for parameterized SELECTs; converts BigInt to Number (or string when unsafe) so results survive `JSON.stringify`. DB path: `DUCKDB_PATH` env override, else `../../data/nba.duckdb`.
- `photos.ts` — proxies NBA CDN headshots with a disk cache at `web/.cache/photos/`. The CDN returns a ~5 KB silhouette (HTTP 200) for players without photos, so a 10 KB size threshold distinguishes real photos; both outcomes are cached (`.png` / `.none`).
- `teamColorEras.ts` — generated era-accurate franchise colors keyed by team_id and year; used to color player/team UI per season.
- `/api/admin/*` — generic table browser + arbitrary read-only SQL, a developer escape hatch not used by the UI (the READ_ONLY connection rejects DDL/DML at the engine level).

### Supplemental data (`data/anchors/`)

Basketball-Reference JSONL scrapes (`bbr_jerseys.jsonl`, `bbr_coaches.jsonl`) are materialized into warehouse tables by `data/audit/build_coach_jersey_tables.sql` (re-run after re-scraping): `fact_coach_season` (team-by-season coach records) and `fact_player_jersey_season` (jersey numbers, each row carrying a `source` tier — `game_inactive_list` > `bbr_roster` > `inferred`, in that priority order). The app queries these warehouse tables directly (`teams.ts`'s `getTeamCoachHistory`, `players.ts`'s jersey-history logic in `getPlayerProfile`) rather than reading the JSONL files at request time, so a server restart is no longer avoidable after re-scraping — re-run `build_coach_jersey_tables.sql` to pick up new scrapes. `shared.ts` still resolves `BBR_COACHES_PATH` and logs whether the file is present, as a secondary sanity signal only (not read at query time). The script header on `build_coach_jersey_tables.sql` documents known accuracy limits and warns the ESPN/cumulative-stats jersey columns are current-number backfills, never usable for history.

### Frontend (`web/src/`)

Vanilla TS, no framework. `main.ts` owns a tab-based SPA: each view in `src/views/` exports a `render(container, detailId?)` function registered in the `TABS` array. Some tabs are `hidden` (search results, game detail, and the analytics-hub tools: betting, four-factors, matchups, clutch, officials, coaching, franchise-leaders) — reachable only via navigation, not the tab bar; hidden analytics tools are also linked from the `analytics` tab's hub page (`views/analytics.ts`'s `ANALYTICS_TOOLS`). Cross-view navigation is done by dispatching a `nba:navigate` CustomEvent (`{ tab, id? }`) on `window`. `dom.ts` provides the `el()` element builder and `announceStatus` (aria-live); `api.ts` holds the shared response types; `headerSearch.ts` is the persistent global search in the header.

### Testing (`web/test/`)

The main suite is **fixture-driven data hardening** (`data-hardening.test.ts`): JSON fixtures under `test/fixtures/` are auto-discovered by `fixtures/manifest.ts` (via `import.meta.glob` — drop a JSON file in, no manifest edit needed). Each fixture asserts a known-true datapoint against the live warehouse. Fixture `status`:

- `"stable"` — must pass now.
- `"regression"` — documents a known open bug; registered as `test.fails`, so it flips RED when someone fixes the underlying query (the prompt to flip it back to `"stable"`).

The DB-backed suite skips only when `data/nba.duckdb` is **absent** (an existence check, not a schema/connectivity check). `dom.test.ts` runs under jsdom regardless of the DB.
