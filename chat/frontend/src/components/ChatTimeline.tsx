/**
 * ChatTimeline (PLAN §8.3, §8.4).
 *
 * The scrolling message list. Auto-scrolls to the bottom on new
 * content (history append OR live `answer_delta`s). Owns the
 * `aria-live="polite"` region for the streaming answer and the
 * `aria-busy` flag for the timeline while a turn is running.
 *
 * Empty state surfaces a friendly hint with three example questions
 * from the benchmark (§12) so first-time users have something to try.
 *
 * The auto-scroll is intentionally simple (scrollTop = scrollHeight):
 *   - We do not pause scroll on user-up-scroll because the chat is
 *     mostly short in Phase 5; Phase 7 polish can add "scroll to
 *     bottom" affordance + "user is reading" detection.
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

export interface ChatTimelineProps {
  messages: TimelineMessage[];
  /** The live, in-flight turn (if any). Rendered as a trailing assistant bubble. */
  liveTurn: ChatTurnState | null;
}

const EXAMPLE_QUESTIONS: string[] = [
  "Who shot 50/40/90 with at least 25 PPG?",
  "Show me the largest scoring run in a Finals game since 2010.",
  "Most career assists in games where the player scored 0.",
];

export function ChatTimeline({ messages, liveTurn }: ChatTimelineProps) {
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
    const status = liveStatus;
    void len;
    void ansLen;
    void status;
    const el = bottomRef.current;
    if (!el) return;
    // Use rAF so the DOM has applied the latest text before we measure.
    const id = window.requestAnimationFrame(() => {
      el.scrollIntoView({ block: "end", behavior: "smooth" });
    });
    return () => window.cancelAnimationFrame(id);
  }, [messages.length, liveAnswerLen, liveStatus]);

  const isEmpty = messages.length === 0 && liveTurn === null;

  return (
    <div
      role="log"
      aria-label="Chat timeline"
      aria-busy={isBusy}
      className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 py-4"
    >
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