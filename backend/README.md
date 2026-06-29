# courtside-data

FastAPI sidecar for the Courtside Data API. Reads from a 22 GB DuckDB file
in `../data/nba.duckdb` (read-only) and serves a JSON API on port 8765.

## Phase 1 scope

This release implements only **one** endpoint:

- `GET /api/status` — returns `{ "ok": true, "endpoint_count": 15 }` when
  the read-only DuckDB connection is live. On DB failure it returns the
  standard `internal_error` envelope with HTTP 500.

The other 14 endpoints (catalog, players, teams, search, datasets,
exports) will land in later phases. See
`frontend/src/lib/openapi-types.ts` for the planned shape.

## Install

This project uses [`uv`](https://docs.astral.sh/uv/). From this
directory:

```bash
uv sync
```

That creates a local `.venv/` and installs all runtime + dev
dependencies (FastAPI, uvicorn, duckdb, pydantic, pyarrow, …).

## Configure

Copy `.env.example` to `.env` and adjust if needed. The defaults assume
the DuckDB file lives at `../data/nba.duckdb` (relative to `backend/`).

| Variable              | Default              | Meaning                                      |
| --------------------- | -------------------- | -------------------------------------------- |
| `DUCKDB_PATH`         | `../data/nba.duckdb` | Path to the read-only DuckDB file.           |
| `DUCKDB_POOL_SIZE`    | `6`                  | Number of read-only connections in the pool. |
| `DUCKDB_ACCESS_MODE`  | `READ_ONLY`          | Honored defensively; we always open RO.      |
| `COURTSIDE_LOG_LEVEL` | `INFO`               | Uvicorn / app log level.                     |

## Run

```bash
uv run courtside-data serve
```

…is equivalent to:

```bash
uv run uvicorn courtside_data.server.app:app --port 8765
```

By default the server binds to `127.0.0.1:8765`, matching the frontend's
`NEXT_PUBLIC_COURTSIDE_API_URL` default (`frontend/src/lib/api-client.ts:10-11`).

Once running:

- `curl http://127.0.0.1:8765/api/status` → `{"ok":true,"endpoint_count":15}`
- `curl http://127.0.0.1:8765/openapi.json` → full OpenAPI 3.x schema.
- `curl http://127.0.0.1:8765/docs` → interactive Swagger UI.

## Test

```bash
uv run pytest
```

The Phase 1 test (`tests/test_status.py`) covers:

- The `/api/status` happy path and contract shape.
- The `_map_exception` envelope shape produced by every domain
  exception class and by the uncaught-`Exception` catch-all.

## Layout

```
backend/
├── pyproject.toml
├── .env.example
├── .gitignore
├── README.md
└── courtside_data/
    ├── __init__.py
    ├── server/
    │   ├── __init__.py
    │   ├── app.py            # FastAPI() + _map_exception + route registration
    │   ├── deps.py           # get_db() pool dependency
    │   ├── errors.py         # 8-code exception hierarchy
    │   └── routes/
    │       ├── __init__.py
    │       └── status.py     # GET /api/status
    ├── schemas/
    │   ├── __init__.py
    │   └── common.py         # StatusResponse, ApiError envelope
    └── db/
        ├── __init__.py
        └── pool.py           # read-only DuckDB singleton pool
```
