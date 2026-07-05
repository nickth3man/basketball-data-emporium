/**
 * SSE parser unit tests (PLAN §8.2).
 *
 * No network — `globalThis.fetch` is mocked with a `ReadableStream`
 * constructed from synthetic `text/event-stream` frames. Covers:
 *
 *   1. Single event frame → typed ChatEvent.
 *   2. Multiple frames delivered in one chunk.
 *   3. A frame split across two chunks (partial read still assembles).
 *   4. Multi-line `data:` joined with `\n` before parse (spec compliance).
 *   5. AbortSignal forwarded to `fetch`; aborting rejects with AbortError.
 *   6. Non-2xx HTTP throws.
 */

import * as sseEvents from "@/generated/sse-events";
import { afterEach, describe, expect, it, vi } from "vitest";

import { streamChat } from "@/api/sse";
import type { ChatEvent } from "@/generated/sse-events";

const originalFetch = globalThis.fetch;

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

function chunksStream(...chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    },
  });
}

function responseFromChunks(...chunks: string[]): Response {
  return new Response(chunksStream(...chunks), {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

async function drain<T>(gen: AsyncGenerator<T, void, unknown>): Promise<T[]> {
  const out: T[] = [];
  for await (const v of gen) out.push(v);
  return out;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("streamChat (SSE parser)", () => {
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("parses a single event frame into a typed ChatEvent", async () => {
    const frame =
      "event: turn_started\n" +
      'data: {"session_id":"s","turn_id":"t","ts":"2026-07-05T00:00:00Z"}\n\n';
    globalThis.fetch = vi.fn().mockResolvedValue(responseFromChunks(frame));

    const events = await drain(streamChat({ sessionId: "s", message: "hi" }));

    expect(events).toHaveLength(1);
    const e = events[0] as Extract<ChatEvent, { event: "turn_started" }>;
    expect(e.event).toBe("turn_started");
    expect(e.session_id).toBe("s");
    expect(e.turn_id).toBe("t");
    expect(e.ts).toBe("2026-07-05T00:00:00Z");
  });

  it("parses multiple frames delivered in a single chunk", async () => {
    const f1 = 'event: turn_started\ndata: {"session_id":"s","turn_id":"t1","ts":"2026"}\n\n';
    const f2 = 'event: answer_delta\ndata: {"delta":"Hello"}\n\n';
    const f3 = 'event: answer_finished\ndata: {"answer":"Hello"}\n\n';
    globalThis.fetch = vi.fn().mockResolvedValue(responseFromChunks(f1 + f2 + f3));

    const events = await drain(streamChat({ sessionId: "s", message: "hi" }));

    expect(events.map((e) => e.event)).toEqual(["turn_started", "answer_delta", "answer_finished"]);
  });

  it("assembles a frame split across two chunks (partial read)", async () => {
    const part1 = 'event: answer_delta\ndata: {"delta":"hello ';
    const part2 = 'world"}\n\n';
    globalThis.fetch = vi.fn().mockResolvedValue(responseFromChunks(part1, part2));

    const events = await drain(streamChat({ sessionId: "s", message: "hi" }));

    expect(events).toHaveLength(1);
    const e = events[0] as Extract<ChatEvent, { event: "answer_delta" }>;
    expect(e.event).toBe("answer_delta");
    expect(e.delta).toBe("hello world");
  });

  it("joins multi-line `data:` lines with \\n before parsing (SSE spec)", async () => {
    // The frame below splits a JSON object across two `data:` lines.
    // After SSE-spec join with `\n`, the result is valid JSON (LF is
    // legal whitespace between tokens) and parses to a turn_started
    // event. The spy verifies the *joined* string is what the parser
    // hands to parseChatEvent.
    const spy = vi.spyOn(sseEvents, "parseChatEvent");
    const frame =
      "event: turn_started\n" +
      'data: {"session_id":\n' +
      'data: "s","turn_id":"t","ts":"2026"}\n\n';
    globalThis.fetch = vi.fn().mockResolvedValue(responseFromChunks(frame));

    const events = await drain(streamChat({ sessionId: "s", message: "hi" }));

    expect(events).toHaveLength(1);
    expect(events[0]?.event).toBe("turn_started");
    expect(spy).toHaveBeenCalledTimes(1);
    const dataArg = spy.mock.calls[0]?.[1];
    expect(dataArg).toBe('{"session_id":\n"s","turn_id":"t","ts":"2026"}');
  });

  it("forwards the AbortSignal to fetch and rejects with AbortError on abort", async () => {
    let capturedSignal: AbortSignal | null = null;
    globalThis.fetch = vi.fn().mockImplementation((_url: unknown, init?: RequestInit) => {
      capturedSignal = (init?.signal as AbortSignal | undefined) ?? null;
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          // Pipe the upstream AbortSignal into the stream so an
          // abort surfaces as an AbortError on reader.read().
          if (capturedSignal) {
            capturedSignal.addEventListener("abort", () => {
              controller.error(new DOMException("aborted", "AbortError"));
            });
          }
        },
      });
      return Promise.resolve(
        new Response(stream, {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        }),
      );
    });

    const ac = new AbortController();
    const iter = streamChat({
      sessionId: "s",
      message: "hi",
      signal: ac.signal,
    });
    // Start the consumer; it will block in reader.read().
    const firstNext = iter.next();

    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(init?.signal).toBe(ac.signal);

    // Aborting should reject the pending reader.read() with AbortError,
    // which propagates out of the generator.
    ac.abort();
    await expect(firstNext).rejects.toMatchObject({ name: "AbortError" });

    // Close the generator to release the reader.
    await iter.return?.();
  });

  it("throws on non-2xx HTTP responses", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(null, {
        status: 500,
        statusText: "Internal Server Error",
      }),
    );

    await expect(drain(streamChat({ sessionId: "s", message: "hi" }))).rejects.toThrow(/HTTP 500/);
  });
});
