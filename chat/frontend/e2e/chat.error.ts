/**
 * E2E error-path tests (error UX).
 *
 * Validates the network-loss / cancel affordances without breaking the
 * happy-path smoke. The two tests are independent (no shared state).
 *
 *  1. `network loss shows the Connection-lost banner with a Retry button`
 *     — intercepts `POST /api/chat/stream` with `route.abort("failed")`
 *     and asserts the assertive red banner renders + Retry button exists.
 *     No live agent or warehouse call is required for this test.
 *
 *  2. `Cancel restores the composer and shows the inline Cancelled. note`
 *     — sends a real turn, waits for the Cancel button (after the 5 s
 *     cancel-affordance threshold), clicks it, asserts the inline
 *     "Cancelled." note
 *     appears AND the composer re-enables. This makes ONE live OpenRouter
 *     call (~$0.001); the call is cancelled before it can settle.
 *
 * Boots BOTH servers via the Playwright `webServer` config in
 * `../playwright.config.ts`. The shared `resetVisibleHistory` helper
 * isolates tests from leftover session history (same pattern as
 * `chat.smoke.ts`).
 */
import { expect, test, type APIRequestContext } from "@playwright/test";

/**
 * Wipe all visible chat sessions via the backend's REST API so each
 * test starts from an empty timeline. Mirrors the helper in
 * `chat.smoke.ts`.
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

test("network loss shows the Connection-lost banner with a Retry button", async ({ page }) => {
  // Abort every chat/stream request before it reaches the backend.
  // `route.abort("failed")` triggers a network failure on the client
  // side; `useChatTurn` translates it to `state.error.code === "network"`,
  // which ChatView surfaces via the red assertive banner with Retry.
  await page.route("**/api/chat/stream", (route) => route.abort("failed"));

  await page.goto("/");

  const composer = page.getByRole("textbox", { name: /message|ask|question/i });
  await expect(composer).toBeVisible();
  await expect(composer).toBeEnabled();

  // Same benchmark question the happy-path smoke uses — keeps the
  // error UX spot-check visually adjacent to the existing coverage.
  await composer.fill("50-40-90 with at least 25 ppg");
  await composer.press("Enter");

  // Assertive alert region containing the Connection-lost copy.
  const alert = page.getByRole("alert");
  await expect(alert).toBeVisible({ timeout: 10_000 });
  await expect(alert).toContainText(/connection lost|network/i);
  await expect(alert).toContainText(/Code:\s*network/i);

  // Retry affordance scoped to the alert so we don't pick up unrelated
  // buttons (none today, but defensive).
  const retry = alert.getByRole("button", { name: /retry/i });
  await expect(retry).toBeVisible();

  // The composer must re-enable after the error settles so a retry
  // can actually be issued.
  await expect(composer).toBeEnabled({ timeout: 5_000 });
});

test("Cancel restores the composer and shows the inline Cancelled. note", async ({ page }) => {
  // The 50-40-90 template typically answers in < 5 s, which would
  // skip the Cancel affordance entirely. To make the cancel UX
  // deterministic we delay the SSE stream long enough that the §13
  // 5 s threshold is crossed reliably — then click Cancel before
  // the response is allowed to settle. The actual Cancel handling
  // is timer-driven on the client; the backend never sees a fully
  // delivered response because we abort the fetch.
  await page.route("**/api/chat/stream", async (route) => {
    // 8 s ≫ the 5 s cancel-affordance threshold, < the global
    // test timeout. The handler never calls route.fulfill or
    // route.continue; the test aborts the fetch via the UI's
    // Cancel button before this promise resolves.
    await new Promise((resolve) => setTimeout(resolve, 8_000));
    await route.abort("failed");
  });

  await page.goto("/");

  const composer = page.getByRole("textbox", { name: /message|ask|question/i });
  await expect(composer).toBeEnabled();

  await composer.fill("50-40-90 with at least 25 ppg");
  await composer.press("Enter");

  // Composer MUST disable while the turn is running.
  await expect(composer).toBeDisabled({ timeout: 5_000 });

  // Cancel button appears after 5 s.
  const cancel = page.getByRole("button", { name: /^cancel$/i });
  await expect(cancel).toBeVisible({ timeout: 9_000 });
  await cancel.click();

  // The inline muted "Cancelled." note (role=status) must appear once
  // the turn settles to the cancelled terminal state. Scope to the
  // status region so we don't collide with the sr-only chat-timeline
  // live status (which also announces "Cancelled."). The note
  // contains the Retry button too — so we assert the leading text
  // rather than an exact match.
  const cancelledNote = page.getByRole("status");
  await expect(cancelledNote).toBeVisible({ timeout: 5_000 });
  await expect(cancelledNote).toContainText(/Cancelled\./i);
  await expect(page.getByRole("alert")).toHaveCount(0);

  // Retry affordance sits inside the cancelled note.
  const retry = page.getByRole("button", { name: /retry/i });
  await expect(retry).toBeVisible();

  // Composer must re-enable so the user can type again.
  await expect(composer).toBeEnabled({ timeout: 5_000 });
});
