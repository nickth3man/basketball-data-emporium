# Basketball Data Chatbot

A local-web chat interface over the project's existing DuckDB warehouse
(`data/nba.duckdb`). Read-only, no auth, OpenRouter-backed, and ground-truth
answers only — every numeric claim comes from a query against the warehouse,
never from model memory. Built around a registry of pre-validated SQL templates
(per PLAN §10–§11); agents never emit SQL.

See [`PLAN.md`](./PLAN.md) for the authoritative design, architecture, technology
pins, and phased implementation plan. **Project status: Phase 0 scaffold.**

---

## Prerequisites

- **Python** 3.12+ (3.13 recommended; pin `<3.14`)
- **Node** 20.19+ (Vite 6 requirement)
- **`uv`** — Python package + env manager ([docs](https://docs.astral.sh/uv/))
- **The built warehouse** at `../data/nba.duckdb` (~21.5 GB; gitignored, not
  shipped in the repo). The API is a no-op without it.

---

## Backend (chat_server/)

```sh
# from chat/
cp .env.example .env             # fill OPENROUTER_API_KEY (required)
uv sync                          # install backend deps from pyproject.toml + uv.lock
uv run uvicorn chat_server.main:app --port 8787 --reload
```

The API binds `127.0.0.1:8787`. `GET /api/health` returns
`{ "status": "ok", "db": "connected" }` when the warehouse is reachable.

> **Concurrency note:** the DuckDB connection is **read-only**. The warehouse
> file cannot be open read-only and read-write simultaneously — stop the chat
> API before running `data/audit/build_nba.py` (or the `web/` dev server's
> README-warning covers it the other way).

---

## Frontend (`frontend/`)

A separate Vite + React 19 + TypeScript + Tailwind v4 + shadcn app, fully
isolated from the repo-root `web/` package.

```sh
# from chat/frontend/
npm install
npm run dev
```

Vite serves on `127.0.0.1:5173` and proxies `/api/*` to `http://localhost:8787`
(SSE-friendly — `Connection: keep-alive`, no buffering).

---

## Tests

**Backend:**

```sh
# from chat/
uv run pytest            # full suite; DB-backed tests skip when data/nba.duckdb is absent
uv run pytest -k name    # single test
uv run pytest --cov chat_server
```

**Frontend:**

```sh
# from chat/frontend/
npm test                 # vitest run
```

End-to-end (Playwright + `@axe-core/playwright`) lands in a later phase.

---

## Quality gates

| Gate | Command |
| --- | --- |
| Lint (Python) | `uv run ruff check chat_server` |
| Format (Python) | `uv run ruff format --check chat_server` |
| Types (Python) | `uv run ty check chat_server` |
| Unused deps | `uv run deptry chat` |
| SQL lint | `uv run sqlfluff lint --dialect duckdb chat_server/templates` |
| Types (TS) | `cd chat/frontend && npx tsc --noEmit` |
| Lint (TS) | `cd chat/frontend && npx eslint .` |
| Format (TS) | `cd chat/frontend && npx prettier --check .` |
| Unit (TS) | `cd chat/frontend && npx vitest run` |

These run automatically in CI (`.github/workflows/chat.yml`) and on pre-commit
via `lefthook` (see `chat/lefthook.yml` — to be merged into the root
`lefthook.yml` or run via `cd chat && lefthook install`).

---

## Project status: Phase 0 scaffold

This is the tooling/config/CI scaffold. The Python package and React app land in
subsequent phases — see PLAN §15 for exit criteria per phase.
