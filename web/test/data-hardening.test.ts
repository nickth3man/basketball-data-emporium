// Data-hardening fixture suite.
//
// This suite requires the local DuckDB warehouse at
// `<repo-root>/data/nba.duckdb` to actually exercise anything; when the
// file is missing (CI, fresh clone) `DB_AVAILABLE` is false and the whole
// suite is skipped, so `npm run test` stays green.
//
// Fixtures are JSON files under `web/test/fixtures/`, auto-discovered by
// the manifest. Two statuses are recognised:
//
//   - status: "stable"     — must pass right now. Registered as `test`.
//   - status: "regression" — documents a known bug that is *currently*
//                            failing. Registered as `test.fails` so the
//                            suite stays green while the bug is open, and
//                            flips RED the moment a contributor fixes the
//                            underlying resolver (which is the prompt to
//                            flip the fixture's status back to "stable").
import { describe, test } from "vitest";
import { DB_AVAILABLE } from "./data-connection";
import { applyMatch, executeAssertion } from "./helpers/assert-fixture";
import { loadAllFixtures } from "./fixtures/manifest";

const all = loadAllFixtures();
const stable = all.filter((f) => f.status === "stable");
const regression = all.filter((f) => f.status === "regression");

const suite = DB_AVAILABLE ? describe : describe.skip;

suite("data-hardening", () => {
  for (const f of stable) {
    test(`[stable] ${f.id}: ${f.entity}`, async () => {
      const actual = await executeAssertion(f);
      applyMatch(actual, f);
    });
  }

  for (const f of regression) {
    test.fails(`[regression] ${f.id}: ${f.entity}`, async () => {
      const actual = await executeAssertion(f);
      applyMatch(actual, f);
    });
  }
});
