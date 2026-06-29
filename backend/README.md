# basketball-data-emporium

FastAPI sidecar for the Basketball Data Emporium API. It reads from a
read-only DuckDB file and serves the Player Hub and Team Hub JSON API used by
the Next.js frontend.

The backend is intentionally small: FastAPI route handlers, a fixed-size
read-only DuckDB pool, declarative dataset bindings, and contract tests that
keep the OpenAPI-generated frontend types aligned with runtime behavior.

## Current API Surface

The service listens on port `8765` by default and exposes:

| Route | Purpose |
| --- | --- |
| `GET /api/status` | API liveness plus audit/DQ state for the current data snapshot. |
| `GET /api/endpoints/player-hub` | Static Player Hub tab and dataset catalog. |
| `GET /api/endpoints/team-hub` | Static Team Hub tab and dataset catalog. |
| `GET /api/players/featured` | Curated featured player cards. |
| `GET /api/players/search?term=...` | Player search by Basketball Reference slug/name. |
| `GET /api/players/{identifier}/summary` | Player overview plus default career dataset. |
| `GET /api/players/{identifier}/{dataset}` | Player-level dataset rows. |
| `GET /api/players/{identifier}/seasons/{season_end_year}/{dataset}` | Player season-scoped dataset rows. |
| `GET /api/players/{identifier}/export?dataset=...` | Player dataset CSV export. |
| `GET /api/teams/featured` | Curated featured team cards. |
| `GET /api/teams/search?term=...` | Team search by abbreviation/name. |
| `GET /api/teams/{identifier}/summary` | Team overview plus roster and franchise arc. |
| `GET /api/teams/{identifier}/{dataset}` | Team-level dataset rows. |
| `GET /api/teams/{identifier}/seasons/{season_end_year}/{dataset}` | Team season-scoped dataset rows. |
| `GET /api/teams/{identifier}/export?dataset=...` | Team dataset CSV export. |
| `GET /api/seasons` | Available season-ending years for the Season Hub. |
| `GET /api/seasons/{season_end_year}/standings` | Canonical team-season standings. |
| `GET /api/seasons/{season_end_year}/leaders?stat=pts` | Ranked season leaders for a bounded stat. |

`/openapi.json` is the source for
`frontend/src/lib/openapi-types.ts`; regenerate that file after schema or route
changes with `npm run gen:api` from `frontend/`.

## Status And Audit Semantics

`GET /api/status` returns API liveness and data verification state:

```json
{
  "ok": true,
  "endpoint_count": 18,
  "data_state": "failed",
  "data_state_reason": "latest_pipeline_failed",
  "data_verified": false,
  "data_stale": true,
  "latest_pipeline_run_id": "...",
  "latest_pipeline_stage": "...",
  "latest_pipeline_status": "failed",
  "latest_pipeline_started_at": "...",
  "latest_dq_status": "passed"
}
```

`ok=true` means the API and DuckDB connection are live. It does not mean the
data snapshot is verified. The current DuckDB snapshot has populated data and
audit tables, but the audit log records failed ETL stages. Until a successful
ETL run and passing DQ result are present and fresh, `data_verified` remains
`false` and the UI renders the snapshot as failed, stale, or unverified.

`data_state_reason` is deliberately constrained to:

- `verified`
- `latest_pipeline_failed`
- `latest_dq_failed`
- `audit_stale`
- `dq_missing`
- `audit_missing`
- `unverified`

## Current Datasets

The public catalog intentionally exposes a small set of backed datasets:

| Hub | Dataset ID | Scope | Backing source |
| --- | --- | --- | --- |
| Player | `career` | player | `api.v_canonical_player_season_totals` |
| Player | `shooting` | player | `api.v_canonical_player_season_totals` |
| Player | `adjusted-shooting` | season | `unified_star.fact_player_season_stats` |
| Team | `roster` | team | `unified_star.fact_player_season_stats` |
| Team | `franchise-arc` | team | `unified_star.fact_team_season_summary` |

Add new datasets by updating the registry binding, catalog entry, query branch,
and contract tests together. Do not advertise raw PBP, shot chart, or betting
line views until each has a dedicated UI and bounded query plan.

The Season Hub is not a generic dataset registry entry. It deliberately exposes
three bounded routes over low-cardinality projections (`api.v_canonical_team_season`
and `api.v_season_leaders`) while keeping high-cardinality game-log and shot
chart views deferred.

## Install

This project uses `uv`. From this directory:

```bash
uv sync --extra dev
```

## Configure

Copy `.env.example` to `.env` and adjust if needed.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DUCKDB_PATH` | `../data/nba.duckdb` | Path to the read-only DuckDB file. |
| `DUCKDB_POOL_SIZE` | `6` | Number of read-only connections in the pool. |
| `DUCKDB_ACCESS_MODE` | `READ_ONLY` | Documents the intended access mode. |
| `BASKETBALL_DATA_LOG_LEVEL` | `INFO` | Uvicorn/app log level. |
| `BASKETBALL_DATA_CORS_ORIGINS` | local frontend origins | Comma-separated allowed frontend origins. |
| `BASKETBALL_DATA_AUDIT_STALE_HOURS` | `72` | Age threshold for stale audit status. |

## Run

```bash
uv run basketball-data-emporium serve
```

Equivalent uvicorn command:

```bash
uv run uvicorn basketball_data_emporium.server.app:app --port 8765
```

Useful checks:

```bash
curl http://127.0.0.1:8765/api/status
curl http://127.0.0.1:8765/api/seasons
curl http://127.0.0.1:8765/openapi.json
curl http://127.0.0.1:8765/docs
```

## Test

```bash
uv run pytest
```

From the repo root, the frontend validation suite is:

```bash
cd frontend
npm run typecheck
npm run lint
npm run test
npm run build
npm run test:e2e
```

The Playwright suite starts both the FastAPI sidecar and the Next.js app.

## Layout

```text
backend/
├── pyproject.toml
├── basketball_data_emporium/
│   ├── catalog/          # Column manifest used by catalogs/tests
│   ├── db/               # DuckDB pool, registry, CSV export helpers
│   ├── queries/          # Player/team query functions
│   ├── schemas/          # Shared Pydantic response models
│   └── server/           # FastAPI app, routes, models, status audit
└── tests/
    ├── audit/            # Audit table and DQ checks
    ├── contract/         # HTTP contract tests
    ├── golden/           # Golden fact harness
    ├── openapi/          # OpenAPI drift gate
    └── schema/           # Information-schema and lineage tests
```
