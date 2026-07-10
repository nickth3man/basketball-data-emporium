# Basketball Data Chatbot

A local-web chat interface over the project's existing DuckDB warehouse
(`data/nba.duckdb`): **read-only, no auth, OpenRouter-backed, and
ground-truth answers only** — every numeric claim comes from a query against
the warehouse, never from model memory.

The agent **writes SQL** (a governed-SQL pipeline), but nothing executes
without passing a three-layer validation gate. v2 ships with a
**semantic catalog** of 8 business models, a **Plan discriminated union**
(ClarifyPlan | NotAnswerablePlan | SqlPlan), inline citations, collapsible
SQL + reasoning panels, and a transparent "not answerable with evidence"
path for questions the warehouse can't support.

---

## Architecture overview

Two processes, one immutable warehouse:

```
┌────────────────────────────────┐        ┌───────────────────────────────────────┐
│  Frontend  (chat/frontend/)    │  SSE   │  Backend  (chat/chat_server/)          │
│  Vite + React 19 + TS +        │ ──────▶│  FastAPI :8787                         │
│  Tailwind v4 + shadcn-style    │        │        │                               │
│                                │        │        ▼                               │
│  ChatTimeline · MessageBubble  │        │  Pydantic AI agent (OpenRouter)         │
│  SqlPanel · ReasoningPanel     │        │        │                               │
│  ResultTable · EvidenceCard    │        │        ▼  ┌─────────────────────┐      │
│  ClarifyPrompt                 │        │  Plan ──▶ │ ClarifyPlan         │      │
│                                │        │  union    │ NotAnswerablePlan   │      │
│                                │        │           │ SqlPlan ◀── repair  │      │
│                                │        │           └─────────┬───────────┘      │
│                                │        │                     │                   │
│                                │        │                     ▼                   │
│                                │        │  ═══ 3-layer gate (sqlgate.py) ═══     │
│                                │        │  Layer1: validate_select_sql()         │
│                                │        │    - single read-only SELECT           │
│                                │        │    - reject INSERT/UPDATE/DELETE/etc   │
│                                │        │    - reject dangerous TVFs             │
│                                │        │    - restrict to dim_/fact_/mart_      │
│                                │        │      /analytics_ prefixes              │
│                                │        │  Layer2: sqlglot optimizer pass        │
│                                │        │    - live information_schema resolve   │
│                                │        │    - catch unknown/ambiguous columns   │
│                                │        │  Layer3: catalog fan/chasm detection   │
│                                │        │    - one_to_many joins + additive      │
│                                │        │      SUM → requires GROUP BY           │
│                                │        └───────────┬───────────────────────────┘
│                                │                    │  (dry-run + execute)
│                                │                    ▼
│                                │           DuckDBSingleton (read-only)
│                                │                    │
│                                │                    ▼
│                                │            data/nba.duckdb (~21.5 GB)
│                                │            + Answer Composer (composer.py)
│                                │            + Semantic Catalog (8 YAML models)
│                                │            + JSONL log store
│                                └──────────────────────────────────────────────────┘
```

**Data flow (UI → DB → UI):**

1. The user composes a message and hits **Send** in `ChatView`.
2. The frontend POSTs to `POST /api/chat/stream`. `useChatTurn`
   consumes the SSE stream via `fetch`/`ReadableStream` (`api/sse.ts`)
   — `EventSource` is the wrong fit because turns are POSTs.
3. The backend (`pipeline.py`) kicks off a turn: agent receives the
   message plus session history and any pending clarification state.
4. **Agent** (Pydantic AI / OpenRouter) explores the semantic catalog
   via tools (`list_models`, `get_model_detail`, `list_warehouse_tables`,
   `describe_table`, `preview`, `lookup_player`, `lookup_team`,
   `lookup_season`) and produces a **Plan** — a discriminated union on
   `answer_mode`:
   - **`ClarifyPlan`**: question is ambiguous; emits
     `ClarificationNeeded` event and the frontend `ClarifyPrompt`
     round-trips the answer back via a clarification prefix.
   - **`NotAnswerablePlan`**: question genuinely can't be answered;
     emits a transparent "not answerable with evidence" response.
   - **`SqlPlan`**: the governed path — includes generated SQL +
     `ResultContract` (grain, columns, row_limit, answer_style).
5. **`SqlPlan`** enters `validate_governed_sql()` (`sqlgate.py`) —
   a three-layer gate:
   - **Layer 1**: `validate_select_sql()` — parse one read-only SELECT,
     reject INSERT/UPDATE/DELETE/CREATE/DROP/PRAGMA/ATTACH and
     dangerous TVFs (`read_csv`, `read_parquet`, etc.), restrict tables
     to approved prefixes (`dim_`, `fact_`, `mart_`, `analytics_`).
   - **Layer 2**: sqlglot optimizer pass against the live warehouse
     `information_schema` — catches unknown or ambiguous column/table
     references.
   - **Layer 3**: catalog-scoped fan/chasm detection — one-to-many
     joins with additive SUM measures must carry a GROUP BY that
     collapses on the join key (deliberately conservative; ambiguous
     cases are skipped rather than false-positive).
6. **Repair loop** (`repair.py`): if validation fails, the agent gets
   one model-driven repair pass (bounded to 2 rounds). If repair
   succeeds, the repaired SQL goes back through the gate; if not, a
   transparent not-answerable response is returned with the broken SQL
   as evidence.
7. **Dry-run + execute**: validated SQL gets a dry-run (syntax check
   against the live schema), then executes read-only against the
   warehouse. `DuckDBSingleton` enforces a single `SELECT` with no
   side effects. A DB watchdog enforces `CHAT_QUERY_TIMEOUT` (default
   300 s).
8. **Composer** (`composer.py`) turns the `QueryResult` into a streamed
   answer with citations, reasoning summary, and optionally a table
   preview. Everything flows as SSE `ChatEvent` events:
   `TurnStarted` → `IntentClassified` → `QueryStarted` →
   `QueryFinished` → `TableReady` → `Reasoning` → `Citation` →
   `AnswerDelta`* → `AnswerFinished` (or `ClarificationNeeded` /
   `ChatError`).

All queries, model usage, and assistant messages go to
`chat/logs/{sessions,queries,model}/<date>/...` as JSONL
(7-day rolling retention; secrets redacted by `loggingredactor`).

---

## Prerequisites

- **Python** 3.12+ (3.13 recommended; the project pins `<3.14` in
  `pyproject.toml`).
- **Node** 20.19+ (Vite 6 requirement).
- **`uv`** — Python package + env manager
  ([docs](https://docs.astral.sh/uv/)).
- **The built warehouse** at `../data/nba.duckdb` (~21.5 GB; gitignored,
  not shipped in the repo). The API is a no-op without it. The
  `web/` dev server also opens this file — see the concurrency note
  below.
- **An `OPENROUTER_API_KEY`** (the LLM is called via OpenRouter — no
  Anthropic / OpenAI native SDK).

---

## Setup

```sh
# 1. Backend env (from chat/)
cp .env.example .env             # fill OPENROUTER_API_KEY
#    OPENROUTER_MODEL defaults to mistralai/mistral-small-2603
#    DUCKDB_PATH defaults to ../data/nba.duckdb (relative to chat/)

# 2. Backend deps
uv sync                          # installs from pyproject.toml + uv.lock

# 3. Frontend deps
cd frontend
npm install
```

The default model is `mistralai/mistral-small-2603` (cheap and
reliable enough for structured-output routing). Switch to
`anthropic/claude-sonnet-4.6` or another before live testing — see
the `OPENROUTER_MODEL` env var in `.env.example`.

---

## Run

**Backend** (from `chat/`):

```sh
uv run uvicorn chat_server.main:app --host 127.0.0.1 --port 8787 --reload
```

- Listens on `127.0.0.1:8787`. `GET /api/health` returns
  `{ "status": "ok", "db": "connected" }` against the live warehouse.

**Frontend** (from `chat/frontend/`):

```sh
npm run dev
```

- Vite serves on `http://localhost:5173` and proxies `/api/*` to
  `http://localhost:8787` (SSE-friendly: `Connection: keep-alive`, no
  buffering — see `vite.config.ts`).

Open **http://localhost:5173** in a browser.

> **Concurrency note (important).** The DuckDB connection is
> **read-only** in this project, but the underlying file format forbids
> mixing read-only and read-write handles in the same process family.
> Multiple *read-only* connections coexist happily — the chatbot, the
> `web/` Express dev server, and CLI inspect-tools can all be open at once.
> But `data/audit/build_nba.py` (which writes the warehouse) **must** be
> the only process touching the file at write time.
> **Stop both the chat API and the `web/` dev server before running
> `build_nba.py`.** A startup log warning surfaces on the chat API if it
> detects a likely read-write lock holder.

---

## Tests

> The tests assume the built warehouse is at `data/nba.duckdb`. DB-backed
> suites skip cleanly when the file is absent — no network calls required.

**Backend** (from `chat/`):

```sh
uv run pytest                     # full suite
uv run pytest -k <name>           # single test
uv run pytest --cov chat_server   # with coverage
```

**Frontend** (from `chat/frontend/`):

```sh
npm test                          # vitest run (SSE parser, drift guards, smoke)
npm run test:e2e                  # Playwright (boots both servers; needs
                                  #   warehouse + OPENROUTER_API_KEY for
                                  #   the live-turn smoke + cancel UX)
```

**Drift guards** (OpenAPI + SSE contract — CI runs these; rerun manually
when you change `chat_server/events.py` or the REST surface):

```sh
uv run python scripts/export_openapi.py        # writes frontend/openapi.json
uv run python scripts/export_sse_schema.py     # writes frontend/src/generated/sse-events.schema.json
git diff --exit-code frontend/openapi.json frontend/src/generated/sse-events.schema.json
```

If either diff is non-empty the contract drifted — update the generated
files (or the source) until they match. The committed JSON snapshots are
the frontend's typed contract (`npm run gen:types` regenerates
`src/generated/api.d.ts`).

---

## Quality gates

| Layer | Gate | Command |
| --- | --- | --- |
| Backend | Lint + format | `uv run ruff check chat_server` / `uv run ruff format --check chat_server` |
| Backend | Type check | `uv run ty check chat_server` |
| Backend | Unit + integration | `uv run pytest` (DB-backed skips cleanly) |
| Backend | Unused deps | `uv run deptry chat` |
| Backend | Drift guard — OpenAPI | `uv run python scripts/export_openapi.py` + `git diff --exit-code` |
| Backend | Drift guard — SSE schema | `uv run python scripts/export_sse_schema.py` + `git diff --exit-code` |
| Backend | JSONL log shape | `check-jsonschema` against exported schemas (CI) |
| Frontend | Type check | `npx tsc --noEmit` |
| Frontend | Lint | `npx eslint .` |
| Frontend | Format | `npx prettier --check .` |
| Frontend | Unit | `npx vitest run` |
| Frontend | E2E + a11y | `npx playwright test` (Playwright + `@axe-core/playwright`) |
| Frontend | Build | `npm run build` |
| Both | Pre-commit | `chat/lefthook.yml` (extends or runs alongside repo-root `lefthook.yml`) |

All of these run in CI (`.github/workflows/chat.yml`) and on pre-commit via
`lefthook`. See [`chat/lefthook.yml`](./lefthook.yml) for the chat-specific
hook set.

---

## Semantic catalog

The **semantic catalog** is the governed-SQL pipeline's business-knowledge
layer: a set of YAML files under `chat_server/semantic_catalog/models/`
that declare the tables, dimensions, measures, joins, and caveats the
agent may use when writing SQL. The catalog feeds both the system prompt
(via `schema_context.py`) and the third layer of `sqlgate.py`
(fan/chasm detection).

Each YAML model defines:

- **`base_table`** — the primary DuckDB table.
- **`dimensions`** — attributes for filtering / grouping / labelling.
- **`measures`** — numeric metrics with `expr` (how to compute),
  `additivity` (sum / non_additive / count_distinct), and optional
  `default_aggregation`.
- **`joins`** — declared relationships to other models (type, keys,
  cardinality).
- **`caveats`** — human-readable warnings the agent should consider.

### Catalog models (8)

| Model | Base table | Purpose |
| --- | --- | --- |
| `player_season` | `mart_player_season` | Per-season totals, per-game averages, and advanced metrics per (player, team, year, season_type) |
| `player_career` | `mart_player_career` | Career totals, per-game averages for every player |
| `games` | `mart_games` | Game-level results: scores, teams, dates, margins, locations |
| `team_season` | `mart_team_season` | Per-season team totals, averages, and advanced metrics |
| `standings` | `mart_standings` | Win/loss records, standings positions, playoff indicators by season |
| `shots` | `mart_shot_detail` | Shot-level data: zone, distance, outcome, defender context |
| `head_to_head` | `mart_head_to_head` | Head-to-head matchups: per-game or per-season aggregates between two teams |
| `awards` | `mart_award_winners` | Award winners by season: MVP, ROY, DPOY, All-NBA, All-Star selections |

### How to add a new capability

1. **Write a YAML model** under
   `chat_server/semantic_catalog/models/<name>.yml` following the
   existing patterns (e.g. `player_season.yml`).
2. **Define** `base_table`, `dimensions`, `measures`, `joins`, and
   `caveats`. Join targets must resolve to existing catalog models.
3. The model is auto-discovered by the `load_catalog()` function
   (`semantic_catalog/loader.py`) — no manifest registration needed.
4. **Preview** the context the agent will see: run the agent locally
   and inspect the system prompt's `{catalog_summary}` block, or call
   `get_model_detail` through the agent's tools.
5. **Test** against the live warehouse: ensure dimensions and measure
   expressions reference real columns in the base table. The
   `schema_context.py` module caches the catalog and regenerates
   the prompt context at run time.

---

## Coverage

Coverage is **catalog-based**, not template-count-based. The semantic
catalog's 8 models cover the major analytical domains of the NBA
warehouse: player stats (season and career), team stats, game results,
standings, shot tracking, head-to-head matchups, and awards.

The agent can combine models via declared joins and compose questions
that span any supported domain. Warehouse-level trust gates are
published in `meta_quality_check` (query the live warehouse for the
current pass/fail state):

```sh
duckdb data/nba.duckdb -readonly -list -c \
  "SELECT check_name, status FROM meta_quality_check"
```

Known data gaps are registered in `meta_known_gap` with a `status`
field — always check this table before "fixing" something:

```sh
duckdb data/nba.duckdb -readonly -list -c \
  "SELECT gap_key, status FROM meta_known_gap ORDER BY gap_key"
```

The agent's `clarify` path handles ambiguous questions, and
`not_answerable` provides transparent explanations when a question
can't be answered from the available data.

---

## SSE event vocabulary

The chat turn streams an 11-event discriminated union (defined in
`events.py`, serialised to JSON Schema by `export_sse_schema.py`):

| Event | Direction | Payload | Purpose |
| --- | --- | --- | --- |
| `turn_started` | server → client | `session_id`, `turn_id`, `ts` | First event on every turn |
| `intent_classified` | server → client | `query_ref`, `confidence` | Agent committed to a governed query |
| `clarification_needed` | server → client | `question`, `options` | Question is ambiguous; frontend shows `ClarifyPrompt` |
| `query_started` | server → client | `query_id`, `query_ref`, `sql` | Validated SQL about to execute |
| `query_finished` | server → client | `query_id`, `duration_ms`, `row_count`, `columns`, `truncated` | Query returned |
| `table_ready` | server → client | `columns`, `rows`, `row_count`, `truncated` | Result rows for the evidence table (preview window) |
| `reasoning` | server → client | `summary`, `execution_plan` | Structured reasoning summary (not model CoT) |
| `citation` | server → client | `table_name`, `metric_key`, `gap_key` | One provenance citation |
| `answer_delta` | server → client | `delta` | One chunk of the streaming answer |
| `answer_finished` | server → client | `answer` | Full composed answer after all deltas |
| `error` | server → client | `code`, `message` | Non-recoverable turn-level error |

The canonical schema snapshot lives at
`frontend/src/generated/sse-events.schema.json` — regenerate after
changing `events.py` (see Drift guards under Tests).

---

## Project status

**v2 (governed-SQL pipeline)** is shipping. The agent writes SQL via a
Plan discriminated union; every SQL statement passes a three-layer
validation gate before read-only execution against the warehouse.
The semantic catalog covers 8 business models with declared dimensions,
measures, and joins. The SSE event vocabulary and REST API are frozen
behind drift guards in CI.

Key components:

- `pipeline.py` — end-to-end turn orchestration (agent → gate → DB → composer → SSE).
- `agent.py` — Pydantic AI agent with Plan union and exploration tools.
- `sqlgate.py` — three-layer validation gate (syntax, schema, fan-out).
- `repair.py` / `clarify.py` — automatic SQL repair and clarification round-trips.
- `composer.py` — answer composition from query results.
- `events.py` — 11-event SSE discriminated union.
- `semantic_catalog/` — 8 YAML business models + schema context.
- `schema_context.py` — runtime system-prompt builder from the catalog.
- `sessions.py` — JSONL session persistence.
- `log_retention.py` — 7-day rolling log retention.
