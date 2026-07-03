# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

An NBA data explorer: a DuckDB warehouse queried by a small Express API and rendered by a vanilla TypeScript/Vite frontend. No frameworks, no in-repo ETL — this is the read-only app layer on top of a warehouse built elsewhere. The app lives entirely in `web/`; the repo root only holds git-hook tooling (lefthook) and data.

**Data prerequisite:** `data/nba.duckdb` (gitignored; ~17 GB and 529 tables as of 2026-07, grows over time) must be present locally for the API to serve real data and for the data-hardening tests to run. It is built/refreshed outside this repo.

## Commands (run inside `web/`)

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

## Architecture

### Server (`web/server/`)

- `index.ts` — all Express routes. Thin: validates params (`clampLimit`, integer id checks), delegates to `queries.ts`, wraps handlers in `asyncRoute` for consistent JSON 500s. **Route-ordering convention:** literal-segment routes (e.g. `/api/players/featured`, `/api/teams/by-conference`) must be registered before their `/:id` param siblings. Game ids are 10-char zero-padded numeric strings.
- `queries.ts` (~3,500 lines) — every SQL query against the warehouse, one exported function per endpoint concern. This is where nearly all business logic lives.
- `db.ts` — single READ_ONLY DuckDB connection (singleton promise). `queryObjects` for parameterized SELECTs; converts BigInt to Number (or string when unsafe) so results survive `JSON.stringify`. DB path: `DUCKDB_PATH` env override, else `../../data/nba.duckdb`.
- `photos.ts` — proxies NBA CDN headshots with a disk cache at `web/.cache/photos/`. The CDN returns a ~5 KB silhouette (HTTP 200) for players without photos, so a 10 KB size threshold distinguishes real photos; both outcomes are cached (`.png` / `.none`).
- `teamColorEras.ts` — generated era-accurate franchise colors keyed by team_id and year; used to color player/team UI per season.
- `/api/admin/*` — generic table browser + arbitrary read-only SQL, a developer escape hatch not used by the UI (the READ_ONLY connection rejects DDL/DML at the engine level).

### Supplemental data (`data/anchors/`)

Basketball-Reference JSONL scrapes read **at request time** via DuckDB `read_json_auto` (no server restart needed after re-scraping; paths overridable via `BBR_JERSEYS_PATH` / `BBR_COACHES_PATH`, CTEs omitted entirely if the files are missing):

- `bbr_jerseys.jsonl` — jersey numbers. In `getPlayerProfile`, per-season jersey sources rank: `inactive_players` (1) > BBR jerseys (2) > `bridge_player_team_season` (3). Bridge rows can leak a player's *current* number into historical seasons, hence the priority; see `data/anchors/README.md` for the bridge-suppression rule.
- `bbr_coaches.jsonl` — the **only** source of historical coach-by-season data (`dim_coach` only covers the current season).

### Frontend (`web/src/`)

Vanilla TS, no framework. `main.ts` owns a tab-based SPA: each view in `src/views/` exports a `render(container, detailId?)` function registered in the `TABS` array. Some tabs are `hidden` (search results, game detail) — reachable only via navigation, not the tab bar. Cross-view navigation is done by dispatching a `nba:navigate` CustomEvent (`{ tab, id? }`) on `window`. `dom.ts` provides the `el()` element builder and `announceStatus` (aria-live); `api.ts` holds the shared response types; `headerSearch.ts` is the persistent global search in the header.

### Testing (`web/test/`)

The main suite is **fixture-driven data hardening** (`data-hardening.test.ts`): JSON fixtures under `test/fixtures/` are auto-discovered by `fixtures/manifest.ts` (via `import.meta.glob` — drop a JSON file in, no manifest edit needed). Each fixture asserts a known-true datapoint (e.g. Kobe's 81-point game) against the live warehouse. Fixture `status`:

- `"stable"` — must pass now.
- `"regression"` — documents a known open bug; registered as `test.fails`, so it flips RED when someone fixes the underlying query (the prompt to flip it back to `"stable"`).

The whole DB-backed suite skips when `data/nba.duckdb` is absent (so CI stays green without the warehouse). `dom.test.ts` runs under jsdom regardless.

## Data-quality gotchas (verified against the warehouse 2026-07-03)

A July 2026 audit found systematic corruption in the warehouse's aggregate/curated layer; it was **remediated in place** by the idempotent scripts in `data/audit/` (`import_source_tables.sql`, `rebuild_curated_layer.sql`, `rebuild_leaders_layer.sql` — they need the DuckDB write lock, so stop the dev server before running them). The rebuilt `agg_*`, `fact_player_awards`, `fact_standings`, `draft_history`, and leaders/ranks tables are now trustworthy; the corrupt pre-rebuild originals are preserved as `*_legacy_fanout` / `*_legacy_names` tables — **never query those**. This remediation lives only in the local `.duckdb` file; an upstream warehouse rebuild (sibling `basketball-data` repo) would wipe it unless the fixes land there.

Still-true gotchas:

- The legacy `game` table is missing whole regular seasons (1960-61, 1961-62, 1966-67, 1970-71, 1975-76, 1976-77, 2012-13, and everything after 2022-23). `fact_game` is complete (1946 → present) — prefer it. `line_score` and `officials` share the legacy `game` gaps, and `line_score`'s home/away column orientation disagrees with `fact_game` for ~47% of games — resolve sides by team id (see `renderLineScore` in `web/src/views/game.ts`).
- `fact_player_game_log` and `inactive_players` coverage starts 1996-97 (and `inactive_players` coverage varies per team — verify, don't assume).
- `dim_player` and `dim_team` are SCD tables with multiple rows per id: filter `dim_player` on `is_current = true`; use `dim_team_history WHERE is_current` as the canonical franchise-identity source. `dim_team.conference`/`division` are always NULL — get those from the latest `fact_standings` row.
- `dim_team_history` only tracks franchise eras from 1996-97 onward, so pre-1996 relocations (e.g. Minneapolis → LA Lakers) show the modern name for old seasons.
- NBA ↔ Basketball-Reference id crosswalks: `bridge_player_bbr` / `bridge_team_bbr` in the warehouse (see `PLAYER_BBR_XWALK_CTE` in `queries.ts` for the dedup pattern), plus reconciliation outputs under `data/audit/out/`.
