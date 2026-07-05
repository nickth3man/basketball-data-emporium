# Basketball Data Chatbot

A local-web chat interface over the project's existing DuckDB warehouse
(`data/nba.duckdb`): read-only, no auth, OpenRouter-backed, and
ground-truth answers only — every numeric claim comes from a query against
the warehouse, never from model memory. v1 ships with **20 predefined SQL
templates** organized into **10 analytical families**, an agent that picks a
template (never writes SQL), SSE streaming, inline citations, collapsible
SQL + reasoning panels, and a transparent "not answerable with evidence"
path for questions the warehouse can't support.

See [`PLAN.md`](./PLAN.md) for the authoritative design, architecture
decisions, technology pins, and the phased implementation plan.

---

## Architecture overview

Two processes, one immutable warehouse:

```
┌────────────────────────────────┐         ┌─────────────────────────────────┐
│  Frontend  (chat/frontend/)    │  SSE    │  Backend  (chat/chat_server/)    │
│  Vite + React 19 + TS +        │ ──────▶ │  FastAPI :8787                  │
│  Tailwind v4 + shadcn-style    │         │       │                         │
│                                │         │       ▼                         │
│  ChatTimeline · MessageBubble  │         │  Pydantic AI agent (OpenRouter) │
│  SqlPanel · ReasoningPanel     │         │       │   ↓ (template + params) │
│  ResultTable · EvidenceCard    │         │       ▼                         │
│                                │         │  Template registry (20 .sql     │
│                                │         │   + .py pairs, 10 families)    │
│                                │         │       │   ↓ (render + validate) │
│                                │         │       ▼                         │
│                                │         │  SQLGlot allowlist gate         │
│                                │         │       │   ↓ (single SELECT)     │
└────────────────────────────────┘         │       ▼                         │
                                            │  DuckDBSingleton (read-only)    │
                                            │       │                         │
                                            │       ▼                         │
                                            │  data/nba.duckdb (~21.5 GB)     │
                                            │  + Answer Composer (Pydantic)   │
                                            │  + JSONL log store              │
                                            └─────────────────────────────────┘
```

**Data flow (UI → DB → UI):**

1. The user composes a message and hits **Send** in `ChatView`.
2. The frontend POSTs to `POST /api/chat/stream`. `useChatTurn`
   consumes the SSE stream via `fetch`/`ReadableStream` (`api/sse.ts`)
   — `EventSource` is the wrong fit because turns are POSTs.
3. The backend (`pipeline.py`) walks the turn:
   **agent (Pydantic AI / OpenRouter) → template lookup → SQL render
   → SQLGlot validation → DuckDB read-only execute → composer
   (Pydantic response models) → SSE event stream**.
4. The composer attaches inline citations (per table / metric / gap),
   produces a transparent `not-answerable-with-evidence` answer when no
   template fits, and emits `chat` events: `turn_started`,
   `intent_classified`, `query_started`/`query_finished`, `table_ready`,
   `reasoning`, `citation`, `answer_delta`/`answer_finished`, `error`.
5. Everything goes to `chat/logs/{sessions,queries,model}/<date>/...` as
   JSONL (7-day rolling retention; secrets redacted by
   `loggingredactor`).

The agent **never** emits SQL. `validate_template_sql()`
(`chat_server/validation.py`) plus a per-template `ALLOWED_TABLES`
allowlist make the backend **safe even if the agent tries to misbehave**.

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
#    OPENROUTER_MODEL defaults to mistralai/mistral-small-2603 — see PLAN §7.1
#    DUCKDB_PATH defaults to ../data/nba.duckdb (relative to chat/)

# 2. Backend deps
uv sync                          # installs from pyproject.toml + uv.lock

# 3. Frontend deps
cd frontend
npm install
```

The v1 default model is `mistralai/mistral-small-2603` (cheap and
reliable enough for template-routing). Switch to `anthropic/claude-sonnet-4.6`
or another before live testing — see PLAN §7.1 + the Phase 8 model-selection
note.

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

> **Concurrency note (PLAN §7.2 — important).** The DuckDB connection is
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
| Backend | SQL lint | `uv run sqlfluff lint --dialect duckdb chat_server/templates` |
| Backend | Unused deps | `uv run deptry chat` |
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

## Template authoring

Add a new query capability by dropping **one file pair** under
`chat_server/templates/<family>/`:

- `<template_id>.sql` — parameterized SQL using DuckDB `$name`
  placeholders. Reads **only** from the template's `ALLOWED_TABLES`.
- `<template_id>.py` — module-level constants:

```python
from pydantic import BaseModel, Field

class Params(BaseModel):
    min_ppg: float = Field(default=25.0, ge=0)

TEMPLATE_ID  = "season_thresholds.fifty_forty_ninety"
TITLE        = "50-40-90 seasons with minimum PPG"
DESCRIPTION  = "Players who shot >=50% FG, >=40% 3P, >=90% FT in a season."
ALLOWED_TABLES = {"mart_player_season"}
RESULT_SCHEMA  = {"player_id": int, "full_name": str, ...}
ANSWER_POLICY  = "ranked_list"
DEFAULT_LIMIT  = 50
EXAMPLES  = ["50-40-90 seasons with at least 25 PPG"]
TESTS     = [{"params": {"min_ppg": 25.0}, "expect_min_rows": 1,
              "expect_contains_player": "Stephen Curry"}]
```

The registry loader (`chat_server/templates/__init__.py` + `_loader.py`)
discovers every `.sql` / `.py` pair, validates the rendered SQL via
SQLGlot at import time (fail-fast — a template that doesn't validate is
a build error), and registers it under `TEMPLATE_ID`. Group files under
one of the family subdirectories so the dotted template id matches the
folder. See `templates/season_thresholds/fifty_forty_ninety.{sql,py}`
for the canonical pattern.

Add a pytest fixture under `chat_tests/test_templates.py` (parameterised)
to lock the answer shape against the live warehouse.

---

## Coverage (v1)

The v1 template registry covers 18 of the 20 benchmark questions in
`PLAN.md` §12 directly, with the remaining two handled as
**transparent not-answerable-with-evidence** responses:

- **18 designed-to-be-answerable** — across the Simple / Medium / Heavy
  latency tiers (PLAN §13). Each has a deterministic pytest fixture
  against `data/nba.duckdb` and a Playwright smoke covering the happy
  path. Five of the Heavy-tier templates are spike-gated and may
  decline to not-answerable-with-evidence on warehouse cost.
- **1 outright not-answerable-with-evidence:**
  `season_comparison.player_team_split` returns the attempted SQL +
  the evidence query that proves why the Harden 2022-23 PHI-vs-BKN
  trade split can't be answered (the warehouse only canonicalizes the
  post-trade team-roster rows).
- **1 conditional not-answerable-with-evidence:**
  `lineup_court.fiveman_shared_court` may fall back when the
  possession-stitched net-rating computation exceeds the 300 s Heavy
  budget — the registry emits the attempted SQL + an evidence query.

**Template families — 10:**

| Family | Templates | Covers |
| --- | --- | --- |
| `season_thresholds` | 2 | 50-40-90, rookie-vs-final slices |
| `career_demographic` | 2 | country + GP thresholds, draft value |
| `season_comparison` | 3 | per-100, era pace, team-split |
| `player_game_conditional` | 5 | margin split, streak / rare stat lines, milestone age, career aggregates |
| `team_coach` | 1 | franchise-final-season ORtg |
| `teammate_overlap` | 1 | two-player shared team-seasons |
| `shot_zones` | 1 | corner-3 / zone splits |
| `pbp_aggregate` | 2 | largest scoring run, fouls by period |
| `clutch_terminal` | 2 | clutch TS%, buzzer-beaters |
| `lineup_court` | 1 | 5-man shared-court minutes + net rating |

**Total: 20 templates / 10 families.**

**Warehouse adaptations** worth knowing when extending the registry:

- **Win shares** are not in `mart_player_season`; the
  `career_demographic.hs_draftee_career_ws` template pulls WS via
  `src_agg_player_season_advanced` (a BBR source-backed table — allowed
  per PLAN §3 decision #3).
- **Lineups** are stitched together from
  `fact_lineup_player` (canonical roster map) and
  `src_agg_lineup_efficiency` (lineup-stats source table) inside the
  `lineup_court` family.

---

## Project status

v1 (Phase 6 template breadth + Phase 7 observability/error-UX polish)
is the current target. See `PLAN.md` §15 for the full phase list and
exit criteria. Phase 8 (hardening — adversarial prompt review, load
testing, model-selection live test, final docs) is the shippable
milestone.
