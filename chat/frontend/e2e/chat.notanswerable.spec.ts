/**
 * E2E test for the not-answerable UX ("not-answerable UX review").
 *
 * What we're proving
 * ------------------
 * When the warehouse can't support an exact answer, the chat backend
 * emits a **transparent not-answerable-with-evidence response**: a
 * regular `answer_finished` event carrying prose like "this is not
 * answerable because…" — *not* an SSE `error` event. The UI must
 * surface that prose as a normal assistant bubble (not the red
 * `role="alert"` error banner reserved for transport / pipeline
 * failures).
 *
 * The canonical not-answerable question is the Harden 2022-23 PHI-vs-BKN
 * trade split. A prompt like "James Harden 2022-23 per-game stats split
 * between Philadelphia and Brooklyn after the trade" routes the agent to
 * a not-answerable path (the warehouse only carries single-team season
 * rows per player-year).
 *
 * Why this test mocks the SSE stream
 * ----------------------------------
 * The live agent's routing is non-deterministic and the not-answerable
 * path depends on the agent's classification. To make the test
 * deterministic (and free of live OpenRouter cost), we intercept
 * `POST /api/chat/stream` with Playwright's `page.route()` and inject a
 * canned SSE stream that emits the four-frame not-answerable sequence:
 *
 *   1. `turn_started`     — first event of every turn
 *   2. `reasoning`        — collapsible-reasoning summary ("the
 *                            warehouse can't support this split because…")
 *   3. `answer_delta`     — a single chunk of the prose
 *   4. `answer_finished`  — the full composed answer string
 *
 * The frame format mirrors what `src/api/sse.ts` parses:
 *
 *   event: <name>\n
 *   data: <json>\n
 *   \n
 *
 * And the payload `event` field equals the SSE `event:` line value (the
 * Pydantic backend's discriminator invariant).
 *
 * Assertions
 * ----------
 *   - Composer accepts text and Enter submits (keyboard contract).
 *   - The streamed answer text appears in the timeline as a regular
 *     assistant bubble (`aria-label="Assistant answered"`). The
 *     "not answerable" copy is asserted explicitly so a future change
 *     that swaps the canned string is caught in code review.
 *   - There is **no** `role="alert"` on the page — the red assertive
 *     banner is reserved for actual transport / pipeline errors
 *     (`chat.error.ts` exercises that path separately).
 *   - The composer re-enables so the user can ask a follow-up.
 *   - axe-core finds zero `critical` / `serious` violations on the
 *     post-turn state.
 *
 * Test infrastructure
 * -------------------
 * This is a **mocked-SSE** spec: it intercepts `POST /api/chat/stream`
 * with a canned response and never reaches the real agent or warehouse.
 * No live OpenRouter call or DuckDB connection is required.
 *
 * Boots BOTH servers via Playwright's `webServer` config in
 * `../playwright.config.ts`. This spec is auto-discovered by CI (run on
 * every push/PR) via the `ls e2e/*.ts | grep -v smoke` glob — any new
 * `*.spec.ts`, `*.error.ts`, or `*.test.ts` in `e2e/` that mocks the SSE
 * stream is picked up automatically. The live smoke test (`chat.smoke.ts`)
 * is excluded from CI; it needs a real `OPENROUTER_API_KEY` and the
 * DuckDB warehouse.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type APIRequestContext } from "@playwright/test";

/**
 * Wipe all visible chat sessions via the backend's REST API so each
 * test starts from an empty timeline. Mirrors the helper in
 * `chat.smoke.ts` / `chat.error.ts`.
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

/**
 * Build the canned SSE frame payload for a deterministic not-answerable
 * turn. Frames follow the SSE spec (`event:` / `data:` / blank line)
 * that `src/api/sse.ts` parses.
 */
function notAnswerableSseFrames(args: {
  sessionId: string;
  turnId: string;
  reasoning: string;
  answer: string;
}): string {
  const frames: Array<{ event: string; data: Record<string, unknown> }> = [
    // 1. First event of every turn; carries the ids + timestamp.
    {
      event: "turn_started",
      data: {
        event: "turn_started",
        session_id: args.sessionId,
        turn_id: args.turnId,
        ts: new Date().toISOString(),
      },
    },
    // 2. Collapsible-reasoning summary (the agent's explanation of why
    //    no template fits / the warehouse can't support this slice).
    {
      event: "reasoning",
      data: {
        event: "reasoning",
        summary: args.reasoning,
        execution_plan: null,
      },
    },
    // 3. Streamed answer delta. Mirrors the composer's normal path —
    //    `answer_delta` chunks then a final `answer_finished`.
    {
      event: "answer_delta",
      data: {
        event: "answer_delta",
        delta: args.answer,
      },
    },
    // 4. Terminal answer — the full composed string. The reducer
    //    overwrites `answer` with this value when it arrives.
    {
      event: "answer_finished",
      data: {
        event: "answer_finished",
        answer: args.answer,
      },
    },
  ];
  return frames.map((f) => `event: ${f.event}\ndata: ${JSON.stringify(f.data)}\n\n`).join("");
}

test("not-answerable answer renders as a normal assistant bubble, not an error banner", async ({
  page,
}) => {
  // The canned NA copy. Plain prose that an end-user could read and
  // understand: "this exact question isn't answerable, here's why, and
  // here's what we did try." The leading "Not answerable" substring is
  // asserted on explicitly so a future template/composer change that
  // drops the canonical phrasing is caught here.
  const reasoningText =
    "No template fits — the warehouse only carries single-team season rows for a player in a given year, so a within-season team split (e.g. Harden PHI→BKN) isn't derivable.";
  const answerText =
    "Not answerable from the warehouse. The James Harden 2022-23 season rows in `mart_player_season` cover Philadelphia only; the trade event isn't canonicalised in a way that lets us slice the same season across two teams, so a per-game PHI-vs-BKN split isn't supported. Try a single-team (PHI-only) or single-season (2018-19 HOU) question instead.";

  // Intercept the chat stream and inject the canned SSE frames. We
  // don't know the session id up front (ChatView creates one lazily
  // on first send), so we fulfill with a fixed session id and let the
  // body pass through — the SSE parser on the client doesn't read the
  // body's session id; it trusts the `turn_started` frame's
  // `session_id` field.
  await page.route("**/api/chat/stream", async (route) => {
    const cannedBody = notAnswerableSseFrames({
      // Synthetic ids — the client doesn't validate them; it just
      // stores them on the turn state for debug visibility.
      sessionId: "synthetic-not-answerable-session",
      turnId: "turn-not-answerable-001",
      reasoning: reasoningText,
      answer: answerText,
    });
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: {
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      },
      body: cannedBody,
    });
  });

  await page.goto("/");

  // Composer is the primary interaction surface; once it's enabled we
  // know the health badge has resolved and the timeline has mounted.
  const composer = page.getByRole("textbox", { name: /message|ask|question/i });
  await expect(composer).toBeVisible();
  await expect(composer).toBeEnabled();

  // The canonical not-answerable question. The canned SSE response
  // decouples us from the live agent's routing — the test asserts
  // the *rendering*, not the agent's classification.
  await composer.fill(
    "James Harden 2022-23 per-game stats split between Philadelphia and Brooklyn after the trade",
  );
  await composer.press("Enter");

  // We intentionally don't assert `composer.toBeDisabled()` here — the
  // canned SSE stream is four small frames, so a fast consumer drains it
  // inside a single React tick and the composer's `running → done`
  // transition can complete before Playwright polls for the disabled
  // state. The error-UX test (`chat.error.ts`) deliberately slows the
  // SSE to exercise that race; this test asserts the **rendered NA
  // output**, which is the contract that matters.

  // The streamed answer must surface as a regular assistant bubble.
  // `MessageBubble` wraps the assistant content with
  // `aria-label="Assistant answered"` — that's our hook.
  const assistantBubble = page.getByRole("article", { name: /assistant answered/i });
  await expect(assistantBubble).toBeVisible({ timeout: 10_000 });
  await expect(assistantBubble).toContainText(/not answerable/i);
  await expect(assistantBubble).toContainText(/Harden/);

  // The reasoning summary should also render in its collapsible
  // panel. The composer emits it as a separate `reasoning` event, and
  // the `ReasoningPanel` always renders when `turn.reasoning` is set.
  await expect(assistantBubble).toContainText(/warehouse only carries single-team/i);

  // **Critical:** no error banner. The red assertive `role="alert"`
  // is reserved for transport / pipeline failures; a not-answerable
  // response is a valid `answer_finished`, not an error.
  expect(await page.getByRole("alert").count()).toBe(0);

  // The composer must be enabled (turn has settled) so a follow-up
  // question can be asked. Whether it disabled-and-re-enabled inside
  // a single tick is not asserted here — see the comment above.
  await expect(composer).toBeEnabled({ timeout: 5_000 });

  // axe-core smoke on the post-turn state. The rendered answer is
  // short prose — there shouldn't be any serious a11y issues.
  const axeResults = await new AxeBuilder({ page }).analyze();
  const seriousViolations = axeResults.violations.filter(
    (v) => v.impact === "critical" || v.impact === "serious",
  );
  expect(
    seriousViolations,
    `axe (post-turn) — ${JSON.stringify(seriousViolations, null, 2)}`,
  ).toEqual([]);

  // Visual artifact: prove the NA bubble + reasoning render side by
  // side and that no red banner is present.
  await page.screenshot({
    path: "test-results/chat.notanswerable.populated.png",
    fullPage: true,
  });
});
