import { expect, test, type APIRequestContext } from "@playwright/test";

async function resetVisibleHistory(request: APIRequestContext): Promise<void> {
  const listRes = await request.get("/api/sessions");
  if (!listRes.ok()) return;
  const sessions = (await listRes.json()) as Array<{ id: string }>;
  await Promise.all(sessions.map((session) => request.delete(`/api/sessions/${session.id}`)));
}

function sseFrame(event: string, data: Record<string, unknown>): string {
  return `event: ${event}\ndata: ${JSON.stringify({ event, ...data })}\n\n`;
}

test.beforeEach(async ({ request }) => {
  await resetVisibleHistory(request);
});

test("keeps a completed clarification prompt available for option and text follow-ups", async ({
  page,
  request,
}) => {
  const requests: string[] = [];
  let streamCount = 0;
  await page.route("**/api/chat/stream", async (route) => {
    const body = route.request().postDataJSON() as { message: string };
    requests.push(body.message);
    streamCount += 1;
    const bodyText =
      streamCount === 1
        ? sseFrame("clarification_needed", {
            question: "Which season?",
            options: ["2024-25"],
          })
        : streamCount === 2
          ? sseFrame("clarification_needed", { question: "Which team?", options: null })
          : sseFrame("answer_finished", { answer: "Follow-up complete." });
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: bodyText });
  });

  await request.post("/api/sessions", { data: { title: null } });
  await page.goto("/");
  const composer = page.getByRole("textbox", { name: "Message" });
  await expect(composer).toBeEnabled();
  await composer.fill("Show stats");
  await composer.press("Enter");

  const answerInput = page.getByRole("textbox", { name: "Your answer" });
  await expect(answerInput).toBeVisible();
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
