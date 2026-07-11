# Basketball Data Chatbot

A local-web chat interface over the project's existing DuckDB warehouse
(`data/nba.duckdb`): read-only, no auth, OpenRouter-backed, and
ground-truth answers only — every numeric claim comes from a query against
the warehouse, never from model memory.

The agent **generates SQL** (it is not limited to a fixed template set).
Generated SQL passes through a **governed validation gate** before
execution, ensuring safety and correctness. Answers stream over SSE.

---

## Architecture overview

Two processes, one immutable warehouse:

```
┌────────────────────────────────┐         ┌──────────────────────────────────┐
│  Frontend  (chat/frontend/)    │  SSE    │  Backend  (chat/chat_server/)     │
│  Vite + React 19 + TS +        │ ──────▶ │  FastAPI :8787                   │
│  Tailwind v4 + shadcn-style    │         │        │                         │
│                                │         │        ▼                         │
│  ChatTimeline · MessageBubble  │         │  Pydantic AI agent (OpenRouter)  │
│  SqlPanel · ReasoningPanel     │         │        │  ↓ (Plan union)         │
│  ResultTable · EvidenceCard    │         │        ▼                         │
│                                │         │  Plan: ClarifyPlan |             │
│                                │         │  NotAnswerablePlan | SqlPlan     │
│                                │         │        │  ↓ (SqlPlan only)       │
│                                │         │        ▼                         │
│                                │         │  governed validation gate         │
│                                │         │  (sqlgate.py: 3-layer check)     │
│                                │         │        │  ↓                      │
│                                │         │        ▼                         │
│                                │         │  read-only DuckDB dry-run/exec   │
│                                │         │        │  ↓                      │
│                                │         │        ▼                         │
│                                │         │  Answer Composer + SSE stream    │
│                                │         │  (composer.py → events.py)       │
│                                │         │        │                         │
│                                │         │        ▼                         │
│                                │         │  data/nba.duckdb (~21.5 GB)      │
│                                │         │  + JSONL log store               │
│                                │         └──────────────────────────────────┘
```

**Data flow (UI → DB → UI):**

1. The user composes a message and hits **Send** in `ChatView`.
2. The frontend POSTs to `POST /api/chat/stream`. `useChatTurn`
   consumes the SSE stream via `fetch`/`ReadableStream` (`api/sse.ts`)
   — `EventSource` is the wrong fit because turns are POSTs.
3. The backend (`pipeline.py`) walks the turn:
   **agent (Pydantic AI / OpenRouter) → Plan union → governed SQL validation
   (sqlgate.py) → read-only DuckDB execute → composer → SSE event stream**.
4. Plans are a discriminated union:
   - **SqlPlan** — valid SQL is run through a three-layer governance gate
     (see `sqlgate.py`): single read-only SELECT, forbidden ops blocked,
     approved table prefixes only (`dim_`/`fact_`/`mart_`/`analytics_`),
     live-schema check, and fan-out / chasm detection.
   - **ClarifyPlan** — the agent asks for more information (round-tripped
     via `ClarifyPrompt`).
   - **NotAnswerablePlan** — transparent response when the warehouse can't
     answer the question.
5. The composer attaches inline citations (per table / metric / gap),
   produces a transparent `not-answerable-with-evidence` answer when no
   plan can satisfy the question, and emits SSE `ChatEvent` types:
   `turn_started`, `intent_classified`, `query_started`/`query_finished`,
   `table_ready`, `reasoning`, `citation`, `answer_delta`/`answer_finished`,
   `clarification_needed`, `error`.
6. Everything goes to `chat/logs/{sessions,queries,model}/<date>/...` as
   JSONL (7-day rolling retention; secrets redacted by
   `loggingredactor`).

The backend is **safe even if the agent tries to misbehave**: SQL is
always validated by `sqlgate.py` before execution, with a model-driven
repair pass on first failure (`repair.py`).

Key source files:
- `chat_server/pipeline.py` — end-to-end turn orchestration (async generator)
- `chat_server/agent.py` — Pydantic AI agent + Plan union + tool definitions
- `chat_server/sqlgate.py` — three-layer governed SQL validation
- `chat_server/composer.py` — answer composition from query results
- `chat_server/repair.py` — model-driven repair of failed SQL validation
- `chat_server/clarify.py` — clarification round-trip handling
- `chat_server/events.py` — SSE event vocabulary (contract with frontend)
- `chat_server/semantic_catalog/` — YAML models (tables, joins, metrics)

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

# 2. Backend deps
uv sync                          # installs from pyproject.toml + uv.lock

# 3. Frontend deps
cd frontend
npm install
```

See `.env.example` for all available environment variables. Required
values beyond `OPENROUTER_API_KEY`:
- `DUCKDB_PATH` — defaults to `../data/nba.duckdb` (relative to `chat/`)

The runtime default model is `anthropic/claude-sonnet-4.6` (set in
`chat_server/config.py`). Override via `OPENROUTER_MODEL` in `.env`.

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
files (or the source) until they match.

---

## Quality gates

| Layer | Gate | Command |
| --- | --- | --- |
| Backend | Lint + format | `uv run ruff check chat_server` / `uv run ruff format --check chat_server` |
| Backend | Type check | `uv run ty check chat_server` |
| Backend | Unit + integration | `uv run pytest` |
| Backend | Unused deps | `uv run deptry chat` |
| Backend | JSONL log shape | `check-jsonschema` against exported schemas (CI) |
| Frontend | Type check | `npx tsc --noEmit` |
| Frontend | Lint | `npx eslint .` |
| Frontend | Format | `npx prettier --check .` |
| Frontend | Unit | `npx vitest run` |
| Frontend | E2E + a11y | `npx playwright test` (Playwright + `@axe-core/playwright`) |
| Frontend | Build | `npm run build` |
| Both | Pre-commit | `lefthook.yml` (repo-root); installed by `npm install` in the root. Covers `web/` and `chat/` checks. Pre-push covers `chat/frontend` checks only. |

All of these run in CI (`.github/workflows/chat.yml`) and via `lefthook`
hooks. The hooks live in the repo-root [`lefthook.yml`](../lefthook.yml) and
are installed automatically by the root `npm install` (via the `prepare`
script). Pre-commit covers both `web/` and `chat/` checks; pre-push covers
`chat/frontend` checks only. The standalone `chat/lefthook.yml` is now an
empty stub — all chat hooks have been merged into the root config.
