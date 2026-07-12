/**
 * SSE transport — POST + fetch/ReadableStream.
 *
 * Why not EventSource: it is GET-only, and chat turns are POSTs with a
 * message body. Putting prompts in URLs would leak them into server
 * access logs and would not match the canonical REST contract.
 *
 * Yields typed `ChatEvent`s parsed from `event:` / `data:` SSE frames.
 * The buffer state survives across chunk reads — the `\n\n` frame
 * separator may straddle chunk boundaries, so we accumulate until we
 * see one. Multi-line `data:` blocks are joined with `\n` per the SSE
 * spec before being handed to `parseChatEvent`.
 *
 * Malformed JSON in a single frame is skipped silently (the rest of
 * the turn is still useful), but a transport failure (non-2xx, abort,
 * network drop) propagates and is translated by the caller (`useChatTurn`)
 * into a terminal status.
 */
import type { ChatEvent } from "@/generated/sse-events";
import { parseChatEvent } from "@/generated/sse-events";

export interface StreamChatOptions {
  sessionId: string;
  message: string;
  /** Forwarded to `fetch` so the caller can cancel via `AbortController`. */
  signal?: AbortSignal;
}

interface ParsedFrame {
  eventName: string;
  event: ChatEvent | null;
}

function splitCompleteFrames(buffer: string): [frames: string[], remainder: string] {
  const parts = buffer.split("\n\n");
  return [parts.slice(0, -1), parts.at(-1) ?? ""];
}

function parseFrame(frame: string, previousEventName: string): ParsedFrame {
  let eventName = previousEventName;
  const dataLines: string[] = [];

  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) {
      eventName = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      // SSE strips one optional leading space after the colon. `.trim()`
      // is broader but harmless for the JSON payloads used here.
      dataLines.push(line.slice(5).trim());
    }
    // Comments, blank lines, ids, and other SSE fields are ignored.
  }

  if (dataLines.length === 0) return { eventName, event: null };

  try {
    return {
      eventName,
      event: parseChatEvent(eventName, dataLines.join("\n")),
    };
  } catch {
    // A malformed frame must not prevent later events from reaching the UI.
    return { eventName, event: null };
  }
}

export async function* streamChat(
  opts: StreamChatOptions,
): AsyncGenerator<ChatEvent, void, unknown> {
  const res = await fetch("/api/chat/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({ session_id: opts.sessionId, message: opts.message }),
    signal: opts.signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(`chat stream failed: HTTP ${res.status}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "message";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const [frames, remainder] = splitCompleteFrames(buffer);
      buffer = remainder;
      for (const frame of frames) {
        const parsed = parseFrame(frame, currentEvent);
        currentEvent = parsed.eventName;
        if (parsed.event) yield parsed.event;
      }
    }
  } finally {
    reader.releaseLock();
  }
}
