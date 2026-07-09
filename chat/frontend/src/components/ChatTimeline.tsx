/**
 * ChatTimeline (PLAN §8.3, §8.4).
 *
 * The scrolling message list. Auto-scrolls to the bottom on new content
 * (history append OR live `answer_delta`s). Owns:
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
 * Empty state surfaces a hero headline + the example questions as
 * clickable chips that drop straight into the composer (§12).
 */
import { useEffect, useRef } from "react";
import { motion, useReducedMotion } from "motion/react";
import { ArrowRight, Database, ShieldCheck } from "lucide-react";

import { MessageBubble, type TimelineRole } from "@/components/MessageBubble";
import type { ChatTurnState } from "@/hooks/useChatTurn";

export interface TimelineMessage {
  role: TimelineRole;
  content: string;
  /** Assistant-only rich-panels payload (the final ChatTurnState snapshot). */
  turn?: ChatTurnState;
}

export type TimelineStatus = "idle" | "running" | "done" | "error" | "cancelled";

export interface ChatTimelineProps {
  messages: TimelineMessage[];
  /** The live, in-flight turn (if any). Rendered as a trailing assistant bubble. */
  liveTurn: ChatTurnState | null;
  /** Coarse status for the sr-only live region; derived by ChatView. */
  status?: TimelineStatus;
  /** Example questions shown in the empty state as clickable chips. */
  examples?: string[];
  /** Fired when an empty-state example chip is clicked. */
  onPickExample?: (q: string) => void;
}

const DEFAULT_EXAMPLES: string[] = [
  "Who shot 50/40/90 with at least 25 PPG?",
  "Show me the largest scoring run in a Finals game since 2010.",
  "Most career assists in games where the player scored 0.",
];

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
  examples = DEFAULT_EXAMPLES,
  onPickExample,
}: ChatTimelineProps) {
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const isBusy = liveTurn?.status === "running";

  const liveAnswerLen = liveTurn?.answer.length ?? 0;
  const liveStatus = liveTurn?.status;
  useEffect(() => {
    const len = messages.length;
    const ansLen = liveAnswerLen;
    const st = liveStatus;
    void len;
    void ansLen;
    void st;
    const el = bottomRef.current;
    if (!el) return;
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
      className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 py-5 sm:px-6"
    >
      {/* sr-only polite live region for coarse turn status (kept outside
          the answer region so screen readers don't merge them). */}
      <span aria-live="polite" aria-atomic="true" className="sr-only" data-turn-status={status}>
        {announcement}
      </span>

      {isEmpty && <EmptyState examples={examples} onPickExample={onPickExample} />}
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
            liveTurn.answer.length > 0 ? liveTurn.answer : liveTurn.status === "error" ? "" : ""
          }
          turn={liveTurn}
        />
      )}
      <div ref={bottomRef} aria-hidden="true" />
    </div>
  );
}

interface EmptyStateProps {
  examples: string[];
  onPickExample?: (q: string) => void;
}

function EmptyState({ examples, onPickExample }: EmptyStateProps) {
  const reduce = useReducedMotion();
  const container = {
    hidden: {},
    show: {
      transition: { staggerChildren: reduce ? 0 : 0.06, delayChildren: reduce ? 0 : 0.05 },
    },
  };
  const item = reduce
    ? { hidden: {}, show: {} }
    : {
        hidden: { opacity: 0, y: 10 },
        show: {
          opacity: 1,
          y: 0,
          transition: { type: "spring" as const, stiffness: 320, damping: 26 },
        },
      };

  return (
    <motion.div
      variants={container}
      initial="hidden"
      animate="show"
      className="m-auto flex w-full max-w-xl flex-col items-center gap-5 rounded-2xl border border-[color:var(--color-border)] bg-[color:var(--color-card)]/70 px-6 py-10 text-center shadow-sm backdrop-blur-sm"
    >
      {/* Brand mark. */}
      <motion.div variants={item} className="flex flex-col items-center gap-3">
        <div
          aria-hidden="true"
          className="flex h-12 w-12 items-center justify-center rounded-xl bg-[color:var(--color-primary)]/12 text-[color:var(--color-primary)] ring-1 ring-inset ring-[color:var(--color-primary)]/25"
        >
          <Database className="h-6 w-6" />
        </div>
        <h2 className="font-display text-xl font-semibold tracking-tight">
          Ask anything about NBA stats
        </h2>
        <p className="max-w-sm text-sm leading-relaxed text-[color:var(--color-muted-foreground)]">
          Every answer is grounded in the warehouse — no model memory. Try one of these to start.
        </p>
      </motion.div>

      {/* Example chips. */}
      <motion.ul variants={item} className="flex w-full flex-col gap-2">
        {examples.map((q, i) => (
          <li key={q}>
            <button
              type="button"
              onClick={() => onPickExample?.(q)}
              style={reduce ? undefined : { animationDelay: `${0.1 + i * 0.05}s` }}
              className={cxButton()}
              aria-label={`Ask: ${q}`}
            >
              <span className="text-left text-sm text-[color:var(--color-foreground)]">{q}</span>
              <ArrowRight
                className="ml-auto h-3.5 w-3.5 shrink-0 text-[color:var(--color-primary)] transition-transform group-hover:translate-x-0.5"
                aria-hidden="true"
              />
            </button>
          </li>
        ))}
      </motion.ul>

      <motion.div
        variants={item}
        className="inline-flex items-center gap-1.5 text-[0.7rem] text-[color:var(--color-muted-foreground)]"
      >
        <ShieldCheck className="h-3.5 w-3.5 text-[color:var(--color-primary)]" aria-hidden="true" />
        Grounded · cited · auditable
      </motion.div>
    </motion.div>
  );
}

/** Inline helper to keep the chip's long className out of the JSX. */
function cxButton(): string {
  return [
    "group flex w-full items-center gap-3 rounded-xl border border-[color:var(--color-border)]",
    "bg-[color:var(--color-background)] px-3.5 py-2.5 text-left",
    "transition-all hover:border-[color:var(--color-primary)]/40 hover:bg-[color:var(--color-muted)]",
    "hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)]",
    "focus-visible:ring-offset-2 focus-visible:ring-offset-[color:var(--color-card)]",
  ].join(" ");
}
