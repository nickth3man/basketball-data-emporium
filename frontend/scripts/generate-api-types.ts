/**
 * Codegen runner for the UI's OpenAPI types.
 *
 * Workflow:
 *   1. Start the FastAPI server:    uv run courtside-data serve
 *   2. Run this script:             npx tsx ui/scripts/generate-api-types.ts
 *
 * Run via `npx tsx` (the project doesn't pin `"type": "module"`).
 *
 * TODO(lib): wire this script into CI for drift detection.
 *
 * What: this file is a CI-friendly wrapper around `openapi-typescript`.
 *   The `npm run gen:api` script in `package.json` is the manual
 *   developer entry point (line 11: `"gen:api": "openapi-typescript ..."`).
 *   This script exists for CI pipelines that want explicit control over
 *   the invocation: fail-loud on codegen error, enforce a specific
 *   server URL, and integrate with `git diff --exit-code` to detect
 *   when committed types have drifted from the live server.
 *
 * Where:
 *   - `ui/src/lib/openapi-types.ts` — the output file (cross-reference
 *     its TODO for the full migration plan).
 *   - `ui/package.json` — `gen:api` script (manual dev entry point).
 *   - `.github/workflows/ci.yml` — where the CI step belongs.
 *   - `courtside_data/server/app.py` — the FastAPI app that serves
 *     `/openapi.json` on `http://127.0.0.1:8765`.
 *
 * How (CI integration sketch):
 *   1. In the CI job, start the server as a background step or a
 *      service container:
 *        - name: Start courtside-data server
 *          run: uv run courtside-data serve &
 *        - name: Wait for /health
 *          run: npx wait-on http://127.0.0.1:8765/openapi.json -t 30_000
 *   2. Run the script:
 *        - name: Generate API types
 *          run: npx tsx scripts/generate-api-types.ts
 *   3. Drift gate — fail the PR if the regenerated file differs from
 *      what is committed:
 *        - name: Verify no drift
 *          run: |
 *            if ! git diff --exit-code src/lib/openapi-types.ts; then
 *              echo "::error::src/lib/openapi-types.ts is out of date."
 *              echo "Run 'npm run gen:api' locally and commit the result."
 *              exit 1
 *            fi
 *   4. Tear down the background server (the `&` from step 1, or let
 *      the service container handle it).
 *
 * Decision needed:
 *   - Run codegen on every PR (drift detection — catches the case where
 *     a backend change ships without a matching UI type update) vs
 *     manual-only (less CI noise, but the UI silently drifts until
 *     somebody runs `gen:api`).
 *   - If on every PR: pin the Python version used for `uv run` in the
 *     CI step, since the generated types can differ across Pydantic
 *     versions.
 *   - If on every PR: consider also committing the regenerated file on
 *     a nightly cron instead of blocking the PR, so the diff is
 *     reviewable in a dedicated bot PR.
 *
 * Verify: run `npx tsx scripts/generate-api-types.ts` locally against a
 *   running `uv run courtside-data serve` and confirm the script exits
 *   0 and `src/lib/openapi-types.ts` is updated. Then `git diff
 *   src/lib/openapi-types.ts` should show only the expected schema
 *   additions, no unrelated noise.
 */
import { execSync } from "node:child_process";

const SERVER_URL = "http://127.0.0.1:8765";
const OUTPUT = "src/lib/openapi-types.ts";

// TODO P0-FE-01: replace this live-port dependency with an owned in-process
// spec dump or a managed backend subprocess. A stale listener on :8765 can
// serve an old `/openapi.json` and overwrite `openapi-types.ts` with the wrong
// contract while the current backend code is correct.

console.log(`Ensure courtside-data server is running on ${SERVER_URL} (uv run courtside-data serve)`);

try {
  execSync(`npx --yes openapi-typescript ${SERVER_URL}/openapi.json -o ${OUTPUT}`, { stdio: "inherit" });
  console.log(`Wrote ${OUTPUT} from ${SERVER_URL}/openapi.json`);
  process.exit(0);
} catch (error) {
  console.error(`openapi-typescript failed: ${error instanceof Error ? error.message : String(error)}`);
  process.exit(1);
}
