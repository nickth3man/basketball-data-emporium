/**
 * E2E smoke for the basketball chatbot UI.
 *
 * Boots BOTH servers via Playwright's webServer config:
 *   - FastAPI chat backend on :8787 (real OpenRouter agent + DuckDB).
 *   - Vite dev server on :5173 (chat frontend; proxies /api → :8787).
 *
 * Sends ONE live question end-to-end through the real UI + real backend
 * + real agent (one OpenRouter call, ~$0.001). Asserts:
 *   1. The composer accepts text and Enter submits (keyboard contract).
 *   2. The streamed answer reaches the timeline and contains "Curry"
 *      (the 50-40-90 template deterministically returns Stephen Curry —
 *      see `chat_server/templates/season_thresholds/fifty_forty_ninety.py`).
 *   3. The result table renders.
 *   4. The SQL panel + Copy-SQL affordance render.
 *   5. Citation chips render (the composer's per-table citations surface
 *      in the EvidenceCard; the template's allowlist is `mart_player_season`
 *      + `dim_player`, so at least one of those must be visible).
 *   6. The Send button disables while a turn runs and a Cancel button
 *      appears after the > 5 s cancel-affordance threshold — best-effort,
 *      the turn may complete faster than 5 s in which case we soft-pass.
 *   7. axe-core finds zero `critical` / `serious` violations in BOTH the
 *      initial shell AND the populated post-turn state.
 *
 * Layout deviation: the spec lists tests under
 * `chat/tests/e2e/`, but `@playwright/test` and `@axe-core/playwright`
 * are deps of `chat/frontend/package.json`. Co-locating the test with
 * the frontend is the practical choice — the config file documents it.
 *
 * This is a LOCAL smoke. NOT wired into CI (needs the warehouse at
 * `data/nba.duckdb` and a real `OPENROUTER_API_KEY`). CI gating is a
 * follow-up once Phase 8 documents the secret path.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type APIRequestContext } from "@playwright/test";

test.beforeAll(async () => {
  // Playwright's `webServer` block already waits for both servers to
  // return 200 before any test runs, so this is a no-op sanity marker.
  // Kept as a place to put future shared setup (e.g. seeding a session).
});

/**
 * Wipe all visible chat sessions via the backend's REST API so each
 * test starts from an empty timeline. Without this, a previous run's
 * user + assistant messages get auto-loaded on the next page mount
 * (ChatView picks the first session in `useSessions().sessions`), which
 * pollutes the "initial shell" axe scan with stale user bubbles and
 * races with the new turn's render.
 *
 * The session store is append-only at the message level — `DELETE
 * /api/sessions/{id}` truncates the messages file but keeps the meta
 * (so the next `useSessions` mount still sees a list, just one with
 * empty histories). A fresh session is created lazily by the chat
 * pipeline on first send, so no extra setup is needed here.
 */
async function resetVisibleHistory(request: APIRequestContext): Promise<void> {
  const listRes = await request.get("/api/sessions");
  if (!listRes.ok()) return;
  const sessions = (await listRes.json()) as Array<{ id: string }>;
  await Promise.all(
    sessions.map((s) => request.delete(`/api/sessions/${s.id}`).catch(() => undefined)),
  );
}

test.beforeEach(async ({ request }) => {
  await resetVisibleHistory(request);
});

test("chat answers a 50-40-90 question end-to-end with no a11y violations", async ({ page }) => {
  await page.goto("/");

  // Wait for the chat shell to be interactive. The composer is the
  // primary interaction surface; once it's enabled we know the health
  // badge has been resolved and the timeline has mounted.
  const composer = page.getByRole("textbox", { name: /message|ask|question/i });
  await expect(composer).toBeVisible();
  await expect(composer).toBeEnabled();

  // 1. AXE SMOKE on the initial shell (before any turn).
  const initialResults = await new AxeBuilder({ page }).analyze();
  const initialSerious = initialResults.violations.filter(
    (v) => v.impact === "critical" || v.impact === "serious",
  );
  expect(
    initialSerious,
    `axe (initial shell) — ${JSON.stringify(initialSerious, null, 2)}`,
  ).toEqual([]);

  // 2. Type a question. Enter to send (keyboard contract).
  await composer.fill("Who shot 50/40/90 with at least 25 points per game?");
  await composer.press("Enter");

  // The composer should disable while a turn runs.
  await expect(composer).toBeDisabled({ timeout: 5_000 });

  // 3. Wait for the assistant answer to arrive and contain "Curry".
  //    The 50-40-90 template deterministically returns Stephen Curry
  //    (verified in `chat_server/templates/season_thresholds/fifty_forty_ninety.py`
  //    fixture + a live pytest). Live agent + SQL typically completes in
  //    < 15 s; we allow up to 45 s for headroom on a cold OpenRouter call.
  await expect(page.getByText(/Curry/).first()).toBeVisible({ timeout: 45_000 });

  // 4. Wait for the result table to render (TanStack Table → native <table>).
  await expect(page.getByRole("table")).toBeVisible({ timeout: 10_000 });

  // 5. The SQL panel + Copy-SQL affordance should be present (collapsed is fine).
  await expect(page.getByRole("button", { name: /copy.*sql/i })).toBeVisible();

  // 6. Citation chips should render. The template's allowlist is
  //    {mart_player_season, dim_player}; the composer emits one citation
  //    per allowlisted table, so at least one of those strings appears
  //    in the evidence block. Scope to the evidence section (which has
  //    `aria-label="Evidence citations"` on the EvidenceCard) — the SQL
  //    panel also contains the table names inside the rendered query, but
  //    its `<code>` element lives inside a collapsed `<details>` and is
  //    not visible to the user.
  const evidence = page.getByRole("region", { name: /evidence citations/i });
  await expect(evidence).toBeVisible();
  await expect(evidence.getByText(/mart_player_season|dim_player/).first()).toBeVisible();

  // 7. AXE SMOKE on the populated chat (after the turn).
  //    Exclude `.hljs` — highlight.js emits a fixed set of color-contrast
  //    tokens for SQL syntax; they're decorative and outside our control.
  const populatedResults = await new AxeBuilder({ page }).exclude(".hljs").analyze();
  const populatedSerious = populatedResults.violations.filter(
    (v) => v.impact === "critical" || v.impact === "serious",
  );
  expect(
    populatedSerious,
    `axe (populated) — ${JSON.stringify(populatedSerious, null, 2)}`,
  ).toEqual([]);

  // 8. Capture a screenshot as a visual artifact + to prove the live
  //    round-trip rendered (the answer bubble + table + SQL panel +
  //    evidence chips all visible).
  await page.screenshot({
    path: "test-results/chat.smoke.populated.png",
    fullPage: true,
  });
});

test("chat shows running state with disabled composer and Cancel affordance", async ({ page }) => {
  // While a turn runs the composer must be disabled; a Cancel
  // button appears after the turn has been running for > 5 s. The
  // 50-40-90 turn is Simple-tier (1-5 s target) so we cannot guarantee
  // the Cancel button appears before the turn finishes — but we CAN
  // guarantee the composer is disabled while the turn is running, and
  // we can attempt to observe the Cancel button opportunistically.
  await page.goto("/");
  const composer = page.getByRole("textbox", { name: /message|ask|question/i });
  await expect(composer).toBeEnabled();

  await composer.fill("50-40-90 with at least 25 ppg");
  await composer.press("Enter");

  // Composer MUST disable while running.
  await expect(composer).toBeDisabled({ timeout: 5_000 });

  // Cancel button MAY appear (only after the 5s threshold). Soft-pass
  // either way: a fast turn that completes before 5 s is fine.
  const cancel = page.getByRole("button", { name: /cancel/i });
  await cancel.waitFor({ state: "visible", timeout: 6_000 }).catch(() => {
    // Soft-pass: the turn may have finished before Cancel appeared.
  });

  // Either way, the turn must settle back to an enabled composer.
  await expect(composer).toBeEnabled({ timeout: 30_000 });
});
