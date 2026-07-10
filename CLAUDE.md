# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## How to use this file (read first)

This file is **orientation only**: how to run things, where code lives, how the pieces connect. It is deliberately **not** a record of current data state — no coverage claims, row counts, bug lists, or migration status. Such prose goes stale fast and gets trusted as fact.

**The live warehouse and the source code are the only source of truth.** Before asserting or acting on a data assumption, verify it directly:

- **Warehouse state** → query the `meta_*` tables in `data/nba.duckdb` (use `-readonly` to avoid contending with the dev servers' READ_ONLY connections):

  ```sh
  duckdb data/nba.duckdb -readonly -list -c "SELECT gap_key, status FROM meta_known_gap ORDER BY gap_key"
  duckdb data/nba.duckdb -readonly -list -c "SELECT check_name, status FROM meta_quality_check"
  duckdb data/nba.duckdb -readonly -list -c "SELECT original_table, fate FROM meta_table_fate"
  ```

  - `meta_known_gap` — register of known gaps with a `status`. **Authoritative.** Check here before "fixing" something; never trust a gap list written elsewhere (this file included).
  - `meta_quality_check` — pass/fail gates from the last build.
  - `meta_table_fate` — whether a table is `canonical_source` / `derived_rebuild` / `lossless_source_only` / `legacy_do_not_use` / `duplicate_superseded` / `empty_endpoint_shell`. Check before trusting an unfamiliar table.
  - `meta_column_lineage` / `meta_metric_definition` — where a canonical column comes from and how a metric is computed.
- **App behavior** → read the code, not the docs. In particular, `chat/README.md` predates the retirement of the SQL-template system (commit `abc5177`) and describes an architecture that no longer exists — trust `chat/chat_server/` source over it.

## What this repo is

A DuckDB NBA warehouse plus two independent read-only apps on top of it:

- `web/` — data explorer: Express API + vanilla TypeScript/Vite frontend (no frameworks).
- `chat/` — chatbot: FastAPI + Pydantic AI agent (OpenRouter) backend, React 19 + Vite + Tailwind v4 frontend. The agent writes SQL that is validated by a governed gate before execution; answers stream over SSE.
- `data/` — the warehouse (`nba.duckdb`, gitignored, ~20+ GB, must be present locally for either app or DB-backed tests) plus the Python build/ingest tooling that produces it.

**Port collision:** the web Express API and the chat FastAPI both default to **:8787**, and both frontends default to **:5173**. Run one stack at a time, or override (`API_PORT` for web, `CHAT_PORT` + a Vite `--port` for chat).

**DuckDB concurrency:** multiple *read-only* connections coexist fine (both apps + CLI at once). But `data/audit/build_nba.py` and `data/ingest/ingest.py` write the warehouse — **stop both dev servers before running them**.

## Commands

Root (repo-level tooling only): `npm install` — installs lefthook git hooks via the `prepare` script.

### web/ (run inside `web/`)

```sh
npm run dev            # Express API (:8787) + Vite dev server (:5173); Vite proxies /api/* to the API
npm run typecheck      # tsc --noEmit
npm run lint           # eslint .  (lint:fix to autofix)
npm run format         # prettier --write .  (format:check in CI)
npm run test           # vitest run
npm run build          # production build
```

Single test file: `npx vitest run test/dom.test.ts` · filter by name: `npx vitest run -t "fixture id or name"`

### chat/ backend (run inside `chat/`; needs `uv` and a `.env` from `.env.example` with `OPENROUTER_API_KEY`)

```sh
uv sync                                          # install deps (pyproject.toml + uv.lock)
uv run uvicorn chat_server.main:app --host 127.0.0.1 --port 8787 --reload
uv run pytest                                    # full suite (DB-backed tests skip if warehouse absent)
uv run pytest -k <name>                          # single test
uv run pytest -m "not live_llm"                  # what CI runs (live_llm marks OpenRouter-hitting tests)
uv run ruff check chat_server                    # lint (ruff format for formatting)
uv run ty check chat_server                      # type check
uv run deptry chat                               # unused/missing deps
```

Config is pydantic-settings via env / `.env`: `OPENROUTER_API_KEY` (required), `OPENROUTER_MODEL`, `DUCKDB_PATH` (required; relative paths resolve from `chat/`), `CHAT_LOG_DIR`, `CHAT_DATA_DIR`, `CHAT_PORT`, `CHAT_QUERY_TIMEOUT`, `CHAT_MEMORY_LIMIT`. CI sets `CHAT_SKIP_DB_TESTS=1`.

### chat/frontend (run inside `chat/frontend/`)

```sh
npm run dev            # BOTH servers: Vite (:5173) + uvicorn backend (:8787) via concurrently
npm run typecheck / lint / format / test         # tsc, eslint, prettier, vitest run
npx vitest run src/hooks/useChatTurn.test.tsx    # single test file
npm run test:e2e       # Playwright (+ axe a11y); boots both servers, needs warehouse + OPENROUTER_API_KEY
npm run build          # tsc -b && vite build; size-limit budgets enforced via `npm run size`
npm run knip           # dead-code/unused-export scan
```

### Contract drift guards (CI-enforced; run from `chat/` after touching `chat_server/events.py`, routes, or response models)

```sh
uv run python scripts/export_openapi.py          # rewrites frontend/openapi.json
uv run python scripts/export_sse_schema.py       # rewrites frontend/src/generated/sse-events.schema.json
git diff --exit-code frontend/openapi.json frontend/src/generated/sse-events.schema.json
```

The committed JSON snapshots are the frontend's typed contract (`npm run gen:types` regenerates `src/generated/api.d.ts`). A non-empty diff means the contract drifted — regenerate and commit.

### Data layer (run from repo root; stop dev servers first — needs the DuckDB write lock)

```sh
python data/ingest/ingest.py <source>                 # stage + resolve a manifest-driven source (data/ingest/sources/*.yaml)
python data/ingest/ingest.py <source> --resolve-only  # re-run crosswalk resolution without re-staging
python data/ingest/ingest.py --reconcile-bbr          # reconcile stale BBR player rows vs bridge_player_bbr
python data/ingest/validate_bridges.py                # invariant checks on every bridge_* table (--fix dedupes)
python data/audit/build_nba.py --source-db <raw.duckdb> --replace   # full rebuild (needs ~60GB+ free disk)
```

`build_nba.py` has **no default `--source-db`**: `data/nba.duckdb` is itself a build output; the raw warehouse it was built from is archived outside the repo.

### Pre-commit / CI

- Repo-root `lefthook.yml` covers **web/ only** (eslint --fix, prettier --write, typecheck on staged files). `chat/lefthook.yml` holds the chat hook set (ruff, ty, sqlfluff, eslint, tsc, prettier) but is **not** auto-installed by the root install — see the note at the top of that file.
- CI: `.github/workflows/web.yml` (typecheck, lint, format:check, test, build) and `.github/workflows/chat.yml` (backend ruff/ty/deptry/pytest, frontend tsc/eslint/prettier/vitest, plus the drift-guard job). All must pass.

## Architecture

### Warehouse layers (built by `data/audit/build_nba.py`)

- `src_*` — lossless copies of every raw source table (provenance cols: `_ingest_run_id`, `_source_system`, `_source_table`, `_source_record_hash`, `_normalized_game_id`).
- `map_*` — standardized id crosswalks (player/team/game ↔ Kaggle/ESPN/Basketball-Reference).
- `dim_*` / `fact_*` — canonical dimensions and facts, rebuilt from the source layer.
- `mart_*` / `analytics_*` — convenience marts built from canonical facts.
- `meta_*` — build provenance and trust gates (see "How to use this file").

### Ingestion (`data/ingest/`)

Manifest-driven loader: `ingest.py <source>` stages files as `stg_<source>_*` tables, registers the source in `dim_source_system`, and resolves ids into `bridge_player_source_id` / `bridge_team_source_id` / `bridge_game_source_id` (unmatched ids kept as `is_unresolved` rows with `unresolved_reason`, never silently dropped). Adding a source = writing `data/ingest/sources/<name>.yaml`. Run `validate_bridges.py` after any ingest or bridge edit.

### Supplemental data (`data/anchors/`)

Basketball-Reference JSONL scrapes (jerseys, coaches) and the Wikidata death-date anchor, plus the scrape scripts that regenerate them. `data/audit/build_coach_jersey_tables.sql` materializes the scrapes into `fact_coach_season` / `fact_player_jersey_season` — re-run it after re-scraping; the apps read warehouse tables, never the JSONL.

### web/ server (`web/server/`)

- `index.ts` — all Express routes. Thin: validates params, delegates to `queries/*.ts`, wraps handlers in `asyncRoute`. **Route-ordering convention:** literal-segment routes (e.g. `/api/players/featured`) must be registered before their `/:id` param siblings. Game ids are 10-char zero-padded numeric strings.
- `queries/*.ts` — every SQL query and nearly all business logic, split by domain; `queries.ts` is a barrel re-export.
- `db.ts` — single READ_ONLY DuckDB connection (singleton promise); converts BigInt so results survive `JSON.stringify`. DB path: `DUCKDB_PATH` env override, else `../../data/nba.duckdb`.
- `photos.ts` — NBA CDN headshot proxy + disk cache at `web/.cache/photos/` (a ~5 KB CDN silhouette means "no photo"; a 10 KB size threshold distinguishes real photos; both outcomes cached).
- `/api/admin/*` — generic table browser + arbitrary read-only SQL, a developer escape hatch not used by the UI.

### web/ frontend (`web/src/`)

Vanilla TS, no framework. `main.ts` owns a tab-based SPA: each view in `src/views/` exports a `render(container, detailId?)` registered in the `TABS` array; some tabs are `hidden` (reachable only via navigation). Cross-view navigation: dispatch a `nba:navigate` CustomEvent (`{ tab, id? }`) on `window`. `dom.ts` provides the `el()` builder + `announceStatus` (aria-live).

### web/ testing (`web/test/`)

Fixture-driven data hardening (`data-hardening.test.ts`): JSON fixtures under `test/fixtures/` are auto-discovered (via `import.meta.glob` — drop a file in, no manifest edit) and each asserts a known-true datapoint against the live warehouse. Fixture `status: "stable"` must pass; `status: "regression"` documents a known open bug and is registered as `test.fails`, so it flips RED when someone fixes the underlying query (the prompt to flip it to `"stable"`). The DB-backed suite skips only when `data/nba.duckdb` is absent; `dom.test.ts` runs under jsdom regardless.

### chat/ backend (`chat/chat_server/`)

Governed-SQL pipeline — the agent **writes SQL**, but nothing executes without passing the gate:

```
user message → agent (Pydantic AI / OpenRouter) → Plan
  Plan = ClarifyPlan | NotAnswerablePlan | SqlPlan   (discriminated union on answer_mode)
SqlPlan → validate_governed_sql (sqlgate.py) → dry-run → read-only DuckDB execute
        → composer → SSE ChatEvents
```

- `pipeline.py` — owns the end-to-end turn (`run_turn(session_id, message)` async generator). Errors after `turn_started` become `ChatError` events so the UI never hangs; query timeouts come from the DB watchdog (`CHAT_QUERY_TIMEOUT`, default 300 s).
- `agent.py` — singleton agent + the `Plan` union. Tools let the agent explore before planning: `list_models` / `get_model_detail` (semantic catalog), `list_warehouse_tables`, `describe_table`, `preview`, `lookup_player`, `lookup_team`, `lookup_season`. System prompt is built at run time from `schema_context.py` (decorator form, so tests can inject their own deps). Prompts live in `prompts/`.
- `sqlgate.py` — three-layer validation: (1) single read-only SELECT, forbidden ops/table-valued functions rejected, tables restricted to approved prefixes `dim_` / `fact_` / `mart_` / `analytics_`; (2) live-schema optimizer pass catching unknown/ambiguous references; (3) catalog-scoped fan/chasm detection for one-to-many joins with additive measures (deliberately conservative).
- `semantic_catalog/` — YAML models (`models/*.yml`: player_season, games, shots, …) declaring tables, joins, and metrics; feeds schema context and the fan-out detector.
- `repair.py` / `clarify.py` — a failed validation gets one model-driven repair pass; ambiguous questions produce a `ClarificationNeeded` event and the frontend's `ClarifyPrompt` round-trips the answer.
- `composer.py` — turns query results into the streamed answer with citations; also the transparent "not answerable with evidence" path.
- `events.py` — the SSE event vocabulary. **Changing it (or routes/response models) requires regenerating the drift-guard snapshots** (see Commands).
- Sessions persist as JSONL under `chat/data/sessions/`; observability logs under `chat/logs/{sessions,queries,model}/` (secrets redacted, 7-day retention via `log_retention.py`). OTel spans are optional (`otel.py`, off by default).

### chat/ frontend (`chat/frontend/src/`)

React 19 + TanStack Query/Table/Virtual + zustand + Tailwind v4 (shadcn-style `components/ui/`). `useChatTurn` consumes the SSE stream via `fetch`/`ReadableStream` (`api/sse.ts`) — `EventSource` is the wrong fit because turns are POSTs. `api/client.ts` is typed by the generated `openapi.json` types. Key components: `ChatTimeline`, `MessageBubble`, `SqlPanel`, `ReasoningPanel`, `ResultTable`, `EvidenceCard`, `ClarifyPrompt`. Playwright e2e specs live in `e2e/`.
