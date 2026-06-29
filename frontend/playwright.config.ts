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
  webServer: [
    {
      command: "cd ../backend && uv run basketball-data-emporium serve --host 127.0.0.1 --port 8765",
      url: "http://127.0.0.1:8765/api/status",
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: "npm run dev -- --hostname 127.0.0.1 --port 3000",
      url: "http://127.0.0.1:3000/players",
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
});
