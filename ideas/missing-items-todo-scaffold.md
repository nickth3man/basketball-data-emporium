# Missing Items Completion Ledger

The initialization scaffold has been resolved into code, tests, or explicit
operational documentation. This file remains as a historical index for the
original gap IDs without leaving active task markers in the repository.

## Runtime And Ops

- P0-BE-01: API CORS is configured in `backend/basketball_data_emporium/server/cors.py`.
- P0-BE-02: `DuckDBPool` is fixed-size and blocks on saturation.
- P0-FE-01: OpenAPI type generation uses an in-process FastAPI spec dump.
- P0-FE-02: Playwright starts both FastAPI and Next.js.
- P0-OPS-01 and P3-OPS-01: `scripts/dev.ps1` owns local ports and cleanup.
- P3-OPS-02: `ideas/deployment.md` documents production deployment settings.
- P3-OPS-03: `scripts/check-openapi-drift.sh` compares the full generated type file.
- P3-OPS-04: shared query helpers log duration and row counts.

## Backend And Data Contracts

- P1-BE-01 and P1-FE-01: `/api/status` exposes audit/DQ state and the UI renders it.
- P1-BE-02, P1-BE-06, P2-DB-01, and P2-DB-02: `db/registry.py` binds shipped datasets, projection lineage, page limits, and derived formulas.
- P1-BE-03, P1-BE-04, P1-DB-01, P1-DB-02, and P2-DB-03: normalization helpers centralize season encoding, active team windows, historical availability, compatibility naming, and the v1 public identity source.
- P1-BE-05: curated featured player/team IDs are validated before the CLI serves traffic.
- P1-BE-07: shared query execution applies a configurable DuckDB interrupt timeout.
- P1-BE-08: the player season-row dataset is labeled as season totals instead of career totals.
- P1-BE-09: route and contract tests cover status, CORS, rate-limit, pool saturation, and frontend state behavior; existing golden tests continue to cover data facts.
- P1-DB-03: audit success remains a live data condition surfaced by `/api/status`; failed or missing audit rows now render as `failed`, `stale`, or `unverified`.

## Feature Surface

- P2-BE-01 and P2-BE-02: currently catalogued player/team datasets are backed; the team franchise arc is now a catalogued dataset.
- P2-BE-03: unsupported inactive-game filtering is declared in catalog metadata and hidden in the UI.
- P2-BE-04: CSV exports stream through `db/csv_export.py`.
- P2-BE-05: registry entries declare max page sizes and stable ordering for shipped datasets.
- P2-BE-06: an opt-in in-process rate-limit jail returns the stable `rate_limit_jailed` envelope.
- P2-BE-07: richer views remain unadvertised until each has a deliberate catalog entry and contract.
- P2-BE-08: catalog labels use the column manifest where available and explicit derived metadata where values are computed.
- P2-FE-01: the client references generated OpenAPI path types and regenerates them without a live port.
- P2-FE-02: search endpoints apply exact/prefix ranking and deterministic ordering.
- P2-FE-03: franchise arc rendering and empty states are aligned with the emitted summary data.
- P2-FE-04: data-correctness specs validate fallback featured lists against live featured/search endpoints.
- P3-FE-01: browser tests include CORS-backed startup, status-state unit coverage, and CSV/error handling paths.
