import { expect, test, type APIRequestContext } from "@playwright/test";

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

/** Build one canned SSE frame: `event: <name>\ndata: <json>\n\n`. */
function sseFrame(event: string, data: Record<string, unknown>): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

test("keeps a completed clarification prompt available for option and text follow-ups", async ({
  page,
}) => {
  const requests: string[] = [];
  let streamCount = 0;

  await page.route("**/api/chat/stream", async (route) => {
    const body = route.request().postDataJSON() as { message: string };
    requests.push(body.message);
    streamCount += 1;

    let frames: string;
    if (streamCount === 1) {
      frames =
        sseFrame("turn_started", {
          event: "turn_started",
          session_id: "synth",
          turn_id: "turn-1",
          ts: new Date().toISOString(),
        }) +
        sseFrame("clarification_needed", {
          event: "clarification_needed",
          question: "Which season?",
          options: ["2024-25"],
        });
    } else if (streamCount === 2) {
      frames =
        sseFrame("turn_started", {
          event: "turn_started",
          session_id: "synth",
          turn_id: "turn-2",
          ts: new Date().toISOString(),
        }) +
        sseFrame("clarification_needed", {
          event: "clarification_needed",
          question: "Which team?",
          options: null,
        });
    } else {
      frames =
        sseFrame("turn_started", {
          event: "turn_started",
          session_id: "synth",
          turn_id: "turn-3",
          ts: new Date().toISOString(),
        }) +
        sseFrame("answer_finished", {
          event: "answer_finished",
          answer: "Follow-up complete.",
        });
    }
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: { "Cache-Control": "no-cache", Connection: "keep-alive" },
      body: frames,
    });
  });

  // Let ChatView lazily create the session on first send — same pattern
  // as the notanswerable spec (avoids APIRequestContext race with page load).
  await page.goto("/");
  const composer = page.getByRole("textbox", { name: /message|ask|question/i });
  await expect(composer).toBeEnabled();
  await composer.fill("Show stats");
  await composer.press("Enter");

  const answerInput = page.getByRole("textbox", { name: "Your answer" });
  await expect(answerInput).toBeVisible({ timeout: 10_000 });
  await expect(answerInput).toBeEnabled();
  await expect(answerInput).toBeFocused();
  await expect(answerInput).toHaveAccessibleDescription("Which season?");
  await expect(page.getByRole("status")).toContainText("answer is needed");
  await expect(page.getByRole("alert")).toHaveCount(0);

  await page.getByRole("button", { name: "2024-25" }).click();
  await expect(answerInput).toHaveAccessibleDescription("Which team?");
  await expect(answerInput).toBeEnabled();
  await expect(answerInput).toBeFocused();

  await answerInput.fill("Lakers");
  await answerInput.press("Enter");
  await expect(page.getByRole("article", { name: /assistant answered/i })).toContainText(
    "Follow-up complete.",
  );
  expect(requests).toEqual(["Show stats", "2024-25", "Lakers"]);
});
