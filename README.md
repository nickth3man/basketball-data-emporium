# Basketball Data Emporium

Basketball Data Emporium is a two-process basketball research app:

- `backend/`: FastAPI sidecar over a read-only DuckDB snapshot.
- `frontend/`: Next.js app for Player, Team, and Season hubs.

The backend owns the OpenAPI contract consumed by the frontend. The local DuckDB
file is intentionally ignored by git because it is large.

## Current Product Surface

- Player Hub: search, featured players, summaries, season totals, shooting, adjusted shooting, CSV export.
- Team Hub: search, featured teams, summaries, roster, franchise arc, CSV export.
- Season Hub: available seasons, canonical team-season standings, and ranked season leaders.

The app deliberately does not expose raw player game logs, play-by-play, or shot
charts through generic datasets. Those views are high-cardinality and need
dedicated query contracts before they become public UI.

## First-Time Setup

Backend:

```powershell
cd backend
uv sync --extra dev
Copy-Item .env.example .env
uv run basketball-data-emporium serve
```

Frontend:

```powershell
cd frontend
npm install
npm run dev
```

The default local URLs are:

- Backend: `http://127.0.0.1:8765`
- Frontend: `http://127.0.0.1:3000`

You can also start both processes from the repository root:

```powershell
.\scripts\dev.ps1
```

## Data Status Gate

`GET /api/status` reports API liveness separately from data verification. A
reachable API can still report failed, stale, or unverified data.

Run this before release checks:

```powershell
.\scripts\check-data-status.ps1
```

The gate exits non-zero unless `data_verified=true`. For local exploratory work
against a known failed snapshot, use:

```powershell
.\scripts\check-data-status.ps1 -AllowUnverified
```

Do not change `/api/status` to mark failed audit data as verified. Fix or
refresh the ETL/audit snapshot, then re-run the gate.

## Validation

Backend:

```powershell
cd backend
uv run pytest
```

Frontend:

```powershell
cd frontend
npm run typecheck
npm run lint
npm run test
npm run build
npm run test:e2e
```

OpenAPI type drift:

```powershell
cd frontend
npm run gen:api
git diff -- src/lib/openapi-types.ts
```

CI or release checks should include `scripts/check-openapi-drift.sh` and the
data-status gate when the DuckDB snapshot is available.

## Deployment Shape

Run the backend where it can read the DuckDB file from low-latency local disk:

```powershell
cd backend
uv run basketball-data-emporium serve --host 0.0.0.0 --port 8765
```

Run the frontend with the public API URL configured:

```powershell
cd frontend
$env:NEXT_PUBLIC_BASKETBALL_DATA_API_URL = "https://api.example.com"
npm run build
npm run start -- --hostname 0.0.0.0 --port 3000
```

Required backend environment:

- `DUCKDB_PATH`: absolute path to the read-only DuckDB file.
- `DUCKDB_ACCESS_MODE=READ_ONLY`
- `DUCKDB_POOL_SIZE`: start with `6`; lower it for memory-constrained hosts.
- `BASKETBALL_DATA_CORS_ORIGINS`: comma-separated deployed frontend origins.
- `BASKETBALL_DATA_AUDIT_STALE_HOURS`: audit freshness threshold.

Required frontend environment:

- `NEXT_PUBLIC_BASKETBALL_DATA_API_URL`: public FastAPI sidecar URL.
