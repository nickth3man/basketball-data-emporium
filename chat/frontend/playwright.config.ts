/**
 * Playwright config for the chat app (PLAN §8.4, §15 Phase 5 exit).
 *
 * Brings up BOTH servers for an end-to-end smoke test:
 *
 *   1. FastAPI on :8787 (the chat backend, runs `uv run uvicorn
 *      chat_server.main:app --port 8787` from the `chat/` root so the
 *      `chat_server.main:app` import resolves).
 *   2. Vite dev server on :5173 (the chat frontend, runs `npm run dev`
 *      from `chat/frontend/`).
 *
 * `webServer` waits for both URLs to return 200 before running tests.
 * Locally (`!CI`) we `reuseExistingServer: true` so a manual dev session
 * doesn't get clobbered; in CI we always spawn fresh.
 *
 * Layout deviation from PLAN §6: the plan lists `chat/tests/e2e/`, but
 * `@playwright/test` lives under `chat/frontend/node_modules/` and the
 * Vite dev server expects to be run from `chat/frontend/`. Co-locating
 * the Playwright config + tests here is the practical choice; this
 * file documents the deviation and the `webServer` block lists the
 * exact working directory each subprocess is launched from.
 *
 * The test in `./e2e/chat.smoke.ts` makes ONE live OpenRouter call
 * (~$0.001), so it's a LOCAL smoke — not wired into CI yet (needs the
 * warehouse + `OPENROUTER_API_KEY`).
 */
import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Resolve from this config file so `cwd` and `__dirname` stay stable
// regardless of where `npx playwright test` is invoked from.
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const CHAT_ROOT = path.resolve(__dirname, ".."); // chat/frontend/.. -> chat/

export default defineConfig({
  testDir: "./e2e",
  testMatch: /.*\.(smoke|spec|test)\.ts$/, // match `.smoke.ts` (semantic) and the conventional `.spec.ts` / `.test.ts`
  fullyParallel: false, // shared backend + live agent — serialize
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1, // one live OpenRouter call at a time
  reporter: process.env.CI ? "github" : "list",
  timeout: 60_000, // generous: live agent call (agent + SQL < 15s typical)
  expect: { timeout: 30_000 },
  use: {
    baseURL: "http://localhost:5173",
    trace: "on-first-retry",
    actionTimeout: 15_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    // --- 1. Chat backend (FastAPI on :8787) ----------------------------
    // `cwd` is the chat/ root so `chat_server.main:app` resolves as an
    // importable module. `DUCKDB_PATH` is passed via `env` because the
    // repo has no `chat/.env` (the secret-bearing env file is gitignored
    // and not committed) and the backend's Settings() treats DUCKDB_PATH
    // as a required field. OPENROUTER_API_KEY is forwarded from the
    // parent shell by Playwright's default `env` extension behaviour.
    {
      command: "uv run uvicorn chat_server.main:app --port 8787 --host 127.0.0.1",
      url: "http://127.0.0.1:8787/api/health",
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      cwd: CHAT_ROOT,
      env: {
        DUCKDB_PATH: path.resolve(CHAT_ROOT, "..", "data", "nba.duckdb"),
        CHAT_LOG_DIR: path.resolve(CHAT_ROOT, "logs"),
        CHAT_DATA_DIR: path.resolve(CHAT_ROOT, "data"),
      },
    },
    // --- 2. Chat frontend (Vite dev on :5173) --------------------------
    // Vite proxies `/api/*` to :8787 (see vite.config.ts), so the
    // browser hits 5173 only and the SSE stream is forwarded.
    {
      command: "npm run dev",
      url: "http://localhost:5173",
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      cwd: __dirname,
    },
  ],
});
