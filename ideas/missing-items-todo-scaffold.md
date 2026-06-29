# Missing Items TODO Scaffold

This file indexes the known gaps left after the initialization work. The IDs
below are referenced by inline TODO comments in backend/frontend scaffolding.
Priority levels are ordered by how likely the item is to block real usage or
mask incorrect data.

## P0 - Runtime Blockers

- TODO P0-BE-01: Configure API CORS for the Next.js origin.
  Browser calls from `http://127.0.0.1:3000` to `http://127.0.0.1:8765` need
  explicit FastAPI CORS middleware. Keep origins environment-driven so local,
  preview, and production deployments can differ without code changes.

- TODO P0-BE-02: Make the DuckDB pool truly fixed-size and blocking.
  `DuckDBPool.acquire()` must wait when all connections are checked out instead
  of trying to initialize/open more connections. Use a `threading.Condition`,
  track total opened connections separately from `_available`, and add a
  concurrency regression test that saturates the pool.

- TODO P0-FE-01: Make OpenAPI generation independent of stale port 8765.
  `npm run gen:api` currently reads from a live URL. A stale listener can
  generate types from the wrong API. Prefer an in-process FastAPI spec dump or a
  script that starts and owns the backend process, waits for readiness, and
  tears it down.

- TODO P0-FE-02: Orchestrate API + web for Playwright.
  Playwright starts only the Next server today. E2E should start or require the
  FastAPI sidecar too, then verify browser-level CORS, network, and real DuckDB
  behavior.

- TODO P0-OPS-01: Add dev server port ownership/cleanup.
  Local dev needs a reliable guard for `127.0.0.1:8765` and `:3000` so stale
  processes do not poison codegen, E2E, or manual testing.

## P1 - Correctness And Contract Safety

- TODO P1-BE-01: Wire `audit.*` into `/api/status`.
  Status should expose latest pipeline run, latest DQ result, failed/stale
  states, and "data present but unverified" so the UI can show more than
  Live/Offline.

- TODO P1-BE-02: Replace hardcoded dataset branches with a registry.
  Each dataset should bind a public dataset ID to a SQL source object,
  projection columns, filters, limits, season support, export support, and
  schema-drift expectations. Routes should dispatch through the registry.

- TODO P1-BE-03: Centralize season validation and encoding.
  The DB mixes integer ending years and string labels like `2023-24`. Add one
  parser/normalizer and validate against a configured or DB-derived current
  season range before route handlers query.

- TODO P1-BE-04: Normalize team identity with season-active windows.
  Queries must filter by `team_id` plus the team row's active season range, not
  by abbreviation alone. This prevents historical duplicate joins and ambiguous
  abbreviations.

- TODO P1-BE-05: Add startup validation for curated featured IDs.
  Featured players/teams should be checked once at startup. A stale curated
  identifier should fail fast in startup diagnostics rather than fail per
  request.

- TODO P1-BE-06: Expand schema-drift checks beyond visible columns.
  Validate every registered column, type, nullable expectation, format rule,
  and derived/UI-consumed key. Missing hidden fields should fail before malformed
  rows reach the frontend.

- TODO P1-BE-07: Add server-side query timeout/cancellation policy.
  Frontend request timeouts do not cancel DuckDB work. Long or accidental scans
  should be guarded server-side with a timeout strategy and dataset-specific
  limits.

- TODO P1-BE-08: Clean up player career semantics.
  The `career` dataset currently returns per-season rows although the catalog
  says "Career Totals". Split into a real career-total dataset and a
  `career-arc`/`season-totals` dataset, or update the catalog language.

- TODO P1-BE-09: Endpoint-level golden tests for all new routes.
  Existing golden SQL checks prove DB facts, but route responses should also pin
  representative values for search, summaries, datasets, and CSV exports.

- TODO P1-DB-01: Resolve schema naming divergence with compatibility views.
  Add stable API-facing views or migrations for player `display_name`, team
  `full_name`, and any other UI contract names so Python does not need ad hoc
  aliases everywhere.

- TODO P1-DB-02: Fix or explicitly model pre-1973 null semantics.
  Some historical counters are stored as `0` where source truth is missing.
  Either repair the ETL values to `NULL` or expose availability metadata so the
  API/UI can suppress misleading aggregates.

- TODO P1-DB-03: Produce a successful audit pipeline run.
  The audit tests document failed latest runs. The API can serve data, but it
  cannot honestly claim the latest ETL passed until `audit.pipeline_run_log`
  contains a successful run and DQ status.

- TODO P1-FE-01: Render audit/DQ states in `StatusPill`.
  Once `/api/status` exposes freshness/DQ fields, replace the simple Live pill
  with states for passed, failed, stale, unverified, offline, and rate-limited.

- TODO P1-FE-02: Add runtime schema validation for open-ended fields.
  `hero_stats` is intentionally loose in OpenAPI. Validate consumed keys at the
  feature boundary so the UI fails gracefully if backend shape drifts.

- TODO P1-FE-03: Handle CSV export API errors in-app.
  Anchor downloads bypass `apiFetch`, so typed API errors become raw browser
  downloads or navigation failures. Add a download handler that fetches,
  validates headers, and surfaces errors through the query/error UI.

## P2 - Feature Completeness

- TODO P2-BE-01: Implement all catalogued and future player datasets.
  Current player coverage is only `career` and `adjusted-shooting`. Add backed
  datasets for per-game logs, season totals, per-game stats, advanced, shooting,
  playoffs, and any view the catalog advertises.

- TODO P2-BE-02: Implement all catalogued and future team datasets.
  Current team coverage is only `roster`. Add franchise history, standings,
  team season, team game logs, opponent stats, four factors, lineups, and any
  catalog tabs exposed in the UI.

- TODO P2-BE-03: Implement `include_inactive_games` or remove it from UI.
  The flag is accepted by current endpoints but ignored. Either back it with
  inactive-player/game-log filters or hide it where unsupported.

- TODO P2-BE-04: Stream CSV exports.
  Current CSV export materializes the whole response in memory. Larger datasets
  should stream Arrow/CSV chunks and keep formula-injection protection.

- TODO P2-BE-05: Add route pagination and sorting.
  Hardcoded `LIMIT 500` is not enough for game logs or future large tables.
  Add cursor/offset, max page size, truthful total counts, and stable ordering.

- TODO P2-BE-06: Implement rate-limit jail if it becomes product-relevant.
  The error code exists, but no shared cross-request rate-limit state exists.
  Add only if the service needs real throttling semantics.

- TODO P2-BE-07: Expose richer API views deliberately.
  PBP, shot charts, betting lines, game logs, franchise leaders, and source
  reconciliation views are not wired. Each needs bespoke limits and UI contracts
  before exposure.

- TODO P2-BE-08: Use `meta.canonical_metric` for labels/formatting.
  Catalog labels are hardcoded from the manifest. Hydrate labels, units, and
  formatting rules from `meta` once the metric-discovery contract is stable.

- TODO P2-DB-01: Bind the 21 `api.v_*` views to registry entries.
  Do not rely on one-off SQL per route. Registry startup checks should verify
  each bound view exists and supplies the expected columns.

- TODO P2-DB-02: Add derived-field lineage.
  UI-consumed fields such as per-game rates and win percentage should have
  declared formulas/lineage, not just appear in route SQL.

- TODO P2-DB-03: Decide when to wire `xref.*` identity resolution.
  Cross-source matching is deferred, but any NBA.com/BBR blended feature will
  need an explicit identity service.

- TODO P2-FE-01: Replace hand-written API shims with generated operation types.
  The current clients return generated schema types but still build paths by
  hand. Move to an OpenAPI-operation-aware client or generated typed helpers.

- TODO P2-FE-02: Improve search ranking and result semantics.
  Search is basic ILIKE ranking. Add exact-name boosts, slug boosts, active
  player/team boosts, league filters, and deterministic tie-breaking.

- TODO P2-FE-03: Align tests with newly emitted franchise arc.
  Some tests still document franchise arc as absent/coming soon. Update them to
  test both real series rendering and empty fallback only where appropriate.

- TODO P2-FE-04: Validate sample featured lists against live API in CI.
  Static fallback lists are useful, but CI should prove each slug/abbrev still
  resolves through the backend.

## P3 - Operations And Product Hardening

- TODO P3-OPS-01: Add root-level dev orchestration.
  A Makefile, script, or process manager should start backend and frontend with
  predictable ports, env vars, readiness checks, and cleanup.

- TODO P3-OPS-02: Add production deployment configuration.
  Define how the FastAPI sidecar and Next app are deployed together, including
  environment variables, static origins, health checks, and DuckDB file access.

- TODO P3-OPS-03: Promote full OpenAPI drift to CI.
  The drift script still focuses on a subset historically. Now that all 15 paths
  exist, compare the full generated type file or a stable structural subset.

- TODO P3-OPS-04: Add performance telemetry.
  Capture query duration, row counts, timeout/cancellation events, and p95/p99
  latency per dataset before exposing larger DB views.

- TODO P3-FE-01: Add browser E2E assertions for CSV and status states.
  Current checks are unit/API-heavy. Browser tests should cover download
  behavior, CORS, status states, empty states, and failure envelopes.

