/**
 * ChatTimeline (PLAN §8.3, §8.4).
 *
 * The scrolling message list. Auto-scrolls to the bottom on new
 * content (history append OR live `answer_delta`s). Owns:
 *
 *   - `role="log"` for the scroll region
 *   - `aria-busy` while a turn is running
 *   - A separate visually-hidden (`sr-only`) polite live region that
 *     announces coarse turn-status ("Assistant is typing…" / "Done." /
 *     "Cancelled." / "Error.") — kept distinct from the answer-stream
 *     live region so screen readers don't overlap (§8.4 / §15).
 *
 * The status prop is intentionally explicit (separate from `liveTurn`)
 * so the parent can drive announcement text from a unified terminal
 * marker (`ChatView` derives it from `state.status` +
 * `lastError`/`lastCancelled`).
 *
 * Empty state surfaces a friendly hint with three example questions
 * from the benchmark (§12) so first-time users have something to try.
 */
import { useEffect, useRef } from "react";

import { MessageBubble, type TimelineRole } from "@/components/MessageBubble";
import type { ChatTurnState } from "@/hooks/useChatTurn";

export interface TimelineMessage {
  role: TimelineRole;
  content: string;
  /** Assistant-only rich-panels payload (the final ChatTurnState snapshot). */
  turn?: ChatTurnState;
}

/**
 * Coarse turn status surfaced to assistive tech as a separate polite
 * announcement. Mirrors `useChatTurn`'s status + ChatView's lifted
 * terminal markers (`lastError` / `lastCancelled`).
 */
export type TimelineStatus = "idle" | "running" | "done" | "error" | "cancelled";

export interface ChatTimelineProps {
  messages: TimelineMessage[];
  /** The live, in-flight turn (if any). Rendered as a trailing assistant bubble. */
  liveTurn: ChatTurnState | null;
  /** Coarse status for the sr-only live region; derived by ChatView. */
  status?: TimelineStatus;
}

const EXAMPLE_QUESTIONS: string[] = [
  "Who shot 50/40/90 with at least 25 PPG?",
  "Show me the largest scoring run in a Finals game since 2010.",
  "Most career assists in games where the player scored 0.",
];

/**
 * Map the coarse status to the user-visible announcement text.
 * Empty string suppresses the announcement (idle = nothing to say).
 */
function statusAnnouncement(status: TimelineStatus | undefined): string {
  switch (status) {
    case "running":
      return "Assistant is typing…";
    case "done":
      return "Done.";
    case "cancelled":
      return "Cancelled.";
    case "error":
      return "Error.";
    default:
      return "";
  }
}

export function ChatTimeline({
  messages,
  liveTurn,
  status = "idle",
}: ChatTimelineProps) {
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const isBusy = liveTurn?.status === "running";

  // Auto-scroll on new content. We watch three signals:
  //   - messages.length changes (history appended)
  //   - liveTurn.answer changes (streaming deltas)
  //   - liveTurn.status changes (e.g. done/error banner arriving)
  const liveAnswerLen = liveTurn?.answer.length ?? 0;
  const liveStatus = liveTurn?.status;
  useEffect(() => {
    // The deps are intentionally read once at the top — they're scroll
    // triggers, but the rule requires them to be referenced. Each is a
    // cheap primitive; the cost is dwarfed by the scroll itself.
    const len = messages.length;
    const ansLen = liveAnswerLen;
    const st = liveStatus;
    void len;
    void ansLen;
    void st;
    const el = bottomRef.current;
    if (!el) return;
    // Use rAF so the DOM has applied the latest text before we measure.
    const id = window.requestAnimationFrame(() => {
      el.scrollIntoView({ block: "end", behavior: "smooth" });
    });
    return () => window.cancelAnimationFrame(id);
  }, [messages.length, liveAnswerLen, liveStatus]);

  const isEmpty = messages.length === 0 && liveTurn === null;
  const announcement = statusAnnouncement(status);

  return (
    <div
      role="log"
      aria-label="Chat timeline"
      aria-busy={isBusy}
      className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 py-4"
    >
      {/*
       * sr-only polite live region for coarse turn status. Lives
       * outside the answer region on purpose so screen readers don't
       * merge it with the streamed answer chunks. `aria-atomic`
       * ensures the whole string is re-read on each change.
       */}
      <span
        aria-live="polite"
        aria-atomic="true"
        className="sr-only"
        data-turn-status={status}
      >
        {announcement}
      </span>

      {isEmpty && <EmptyState />}
      {messages.map((msg) => (
        <MessageBubble
          key={`m-${msg.role}-${msg.content.slice(0, 16)}`}
          speaker={msg.role}
          content={msg.content}
          turn={msg.turn}
        />
      ))}
      {liveTurn !== null && (
        <MessageBubble
          key="live-turn"
          speaker="assistant"
          content={
            liveTurn.answer.length > 0
              ? liveTurn.answer
              : liveTurn.status === "error"
                ? ""
                : "…"
          }
          turn={liveTurn}
        />
      )}
      <div ref={bottomRef} aria-hidden="true" />
    </div>
  );
}

function EmptyState() {
  return (
    <div className="m-auto flex max-w-md flex-col gap-3 rounded border border-dashed border-[color:var(--color-border)] p-6 text-center">
      <h2 className="text-base font-medium">Ask anything about NBA stats</h2>
      <p className="text-sm text-[color:var(--color-muted-foreground)]">
        Every answer is grounded in the warehouse — no model memory.
      </p>
      <ul className="flex flex-col gap-1 text-left">
        {EXAMPLE_QUESTIONS.map((q) => (
          <li key={q} className="rounded bg-[color:var(--color-muted)] px-3 py-2 text-sm">
            {q}
          </li>
        ))}
      </ul>
    </div>
  );
}
