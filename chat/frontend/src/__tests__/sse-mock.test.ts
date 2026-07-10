import { describe, expect, it } from "vitest";

import { streamChat } from "@/api/sse";

describe("default POST stream mock", () => {
  it("returns production-shaped SSE frames through fetch", async () => {
    const events = [];
    for await (const event of streamChat({ sessionId: "session-1", message: "hello" })) {
      events.push(event);
    }

    expect(events.map((event) => event.event)).toEqual([
      "turn_started",
      "answer_delta",
      "answer_finished",
    ]);
    expect(events[0]).toMatchObject({ session_id: "session-1", turn_id: "mock-turn" });
    expect(events[2]).toMatchObject({ answer: "Mock answer: hello" });
  });
});
