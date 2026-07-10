import { http, HttpResponse } from "msw";

const encoder = new TextEncoder();

function sseResponse(frames: string[]): HttpResponse<ReadableStream<Uint8Array>> {
  return new HttpResponse(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const frame of frames) controller.enqueue(encoder.encode(frame));
        controller.close();
      },
    }),
    { headers: { "Content-Type": "text/event-stream" } },
  );
}

/**
 * MSW handlers shared by the Node test server (src/test/setup.ts) and the
 * browser worker (src/test/mocks/browser.ts). Add real `/api/*` routes here
 * as you write component tests; see the generated types in `src/generated/`
 * for the authoritative endpoint list.
 */
export const handlers = [
  // --- REST examples ------------------------------------------------------
  http.get("/api/health", () => HttpResponse.json({ status: "ok" })),

  // `streamChat` is a fetch POST, not an EventSource GET. Return the same
  // ReadableStream wire shape that the FastAPI SSE endpoint produces.
  http.post("/api/chat/stream", async ({ request }) => {
    const body = (await request.json()) as { session_id?: string; message?: string };
    const sessionId = body.session_id ?? "mock-session";
    const answer = `Mock answer: ${body.message ?? ""}`;
    return sseResponse([
      `event: turn_started\ndata: ${JSON.stringify({ session_id: sessionId, turn_id: "mock-turn", ts: "2026-01-01T00:00:00Z" })}\n\n`,
      `event: answer_delta\ndata: ${JSON.stringify({ delta: answer })}\n\n`,
      `event: answer_finished\ndata: ${JSON.stringify({ answer })}\n\n`,
    ]);
  }),
];
