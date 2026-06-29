import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  // Discover specs in BOTH `tests/e2e/` (the user-facing browser specs)
  // and `tests/data-correctness/` (the MVCS identifier-subset spec that
  // runs against the live sidecar, no browser). The default `testMatch`
  // of `**/*.@(spec|test).?(c|m)[jt]s?(x)` already covers both
  // directories and their `*.spec.ts` files. We do NOT match
  // `*.test.ts(x)` files here — those are picked up by `vitest` and
  // live under `src/`, not under `tests/`.
  testDir: "./tests",
  testMatch: ["e2e/**/*.spec.ts", "data-correctness/**/*.spec.ts"],
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "retain-on-failure",
  },
  webServer: {
    // TODO P0-FE-02: start or verify the FastAPI sidecar here as well as the
    // Next app. Current E2E orchestration proves only that the browser can load
    // Next; it does not own `127.0.0.1:8765`, detect stale API processes, or
    // prove CORS works end-to-end.
    //
    // TODO P3-FE-01: add browser assertions for CSV downloads, audit/status
    // states, empty/error envelopes, and a real data-table load through the
    // sidecar. Those checks should fail if the backend is missing, stale, or
    // blocked by CORS.
    command: "npm run dev -- --hostname 127.0.0.1 --port 3000",
    url: "http://127.0.0.1:3000/players",
    reuseExistingServer: true,
    timeout: 120_000,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
});
