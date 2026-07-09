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
  let dataLines: string[] = [];
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // Drain all complete frames from the buffer. The `\n\n` separator
      // may straddle chunk boundaries, so we keep looping until no more
      // frames are available — `no-cond-assign` forces us to avoid the
      // `while ((idx = …) >= 0)` idiom.
      let idx = buffer.indexOf("\n\n");
      while (idx >= 0) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        idx = buffer.indexOf("\n\n");
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) {
            currentEvent = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            // SSE spec strips one optional leading space after the
            // colon; `.trim()` is broader but harmless for JSON payloads.
            dataLines.push(line.slice(5).trim());
          }
          // Other lines (comments starting with `:`, blanks, ids) are ignored.
        }
        if (dataLines.length > 0) {
          const data = dataLines.join("\n");
          dataLines = [];
          try {
            yield parseChatEvent(currentEvent, data);
          } catch {
            // Malformed frame — skip but keep the stream alive so the
            // remaining events still reach the UI.
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
