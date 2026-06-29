# Deployment Configuration

The application deploys as two cooperating processes:

- FastAPI sidecar: `cd backend && uv run basketball-data-emporium serve --host 0.0.0.0 --port 8765`
- Next.js app: `cd frontend && npm run build && npm run start -- --hostname 0.0.0.0 --port 3000`

Required backend environment:

- `DUCKDB_PATH`: absolute path to the read-only DuckDB file mounted in the sidecar container or VM.
- `DUCKDB_ACCESS_MODE=READ_ONLY`: documents the intended read-only access mode.
- `DUCKDB_POOL_SIZE`: sidecar connection pool size. Start with `6`; lower it for memory-constrained hosts.
- `BASKETBALL_DATA_CORS_ORIGINS`: comma-separated production frontend origins allowed to call the sidecar.
- `BASKETBALL_DATA_AUDIT_STALE_HOURS`: age threshold for `/api/status` to report `data_state="stale"`.

Required frontend environment:

- `NEXT_PUBLIC_BASKETBALL_DATA_API_URL`: public URL of the FastAPI sidecar, for example `https://api.example.com`.

Health checks:

- Backend readiness: `GET /api/status` returns `200`, `ok=true`, and a data state.
- Frontend readiness: `GET /players` returns `200`.

Operational constraints:

- The DuckDB file must be available before the sidecar starts.
- The sidecar must be deployed where it can read the DuckDB file with low-latency local disk access.
- Production CORS should name only deployed frontend origins; do not use wildcard origins.
- Use `scripts/check-openapi-drift.sh` in CI after backend or schema changes.
