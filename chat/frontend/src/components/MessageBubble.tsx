/**
 * MessageBubble (PLAN §8.3) — re-skinned for the v2 premium UI.
 *
 * One chat message: user or assistant styled. For an assistant turn,
 * the answer text is rendered as markdown (MarkdownContent is memoized
 * so settled bubbles survive token-by-token re-renders of the live
 * bubble), plus the rich panels — reasoning, SQL, an optional inline
 * chart, the result table, and evidence — composed conditionally on the
 * live `turn` state.
 *
 * Motion: each bubble row fades/slides in on mount via `motion.div`
 * (gated by `useReducedMotion` so reduced-motion users see an instant
 * render). The streaming "answering…" pill + typing dots give live
 * feedback while the answer streams.
 *
 * The streaming answer (live `turn.answer`) is wrapped in an
 * `aria-live="polite"` region by `ChatTimeline`; the bubble itself does
 * not carry the live region.
 */
import { memo, useCallback, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";
import { useCopyToClipboard } from "usehooks-ts";
import type { ReactNode } from "react";

import { AnswerChart } from "@/components/AnswerChart";
import { EvidenceCard } from "@/components/EvidenceCard";
import { MarkdownContent } from "@/components/MarkdownContent";
import { ReasoningPanel } from "@/components/ReasoningPanel";
import { ResultTable } from "@/components/ResultTable";
import { SqlPanel } from "@/components/SqlPanel";
import { cn } from "@/lib/utils";
import type { ChatTurnState } from "@/hooks/useChatTurn";

export type TimelineRole = "user" | "assistant";

export interface MessageBubbleProps {
  speaker: TimelineRole;
  content: string;
  /** Assistant-only: the live/final turn state to render rich panels from. */
  turn?: ChatTurnState;
}

export const MessageBubble = memo(function MessageBubble({
  speaker,
  content,
  turn,
}: MessageBubbleProps) {
  const reduce = useReducedMotion();

  if (speaker === "user") {
    return (
      <BubbleRow reduce={reduce} align="end">
        <article
          aria-label="You said"
          className={cn(
            "max-w-[85%] rounded-2xl rounded-br-md px-3.5 py-2 text-sm leading-relaxed",
            "bg-[color:var(--color-primary)] text-[color:var(--color-primary-foreground)]",
            "shadow-sm shadow-[color:var(--color-primary)]/25",
          )}
        >
          <p className="whitespace-pre-wrap">{content}</p>
        </article>
      </BubbleRow>
    );
  }

  return <AssistantBubble content={content} turn={turn} reduce={reduce} />;
});

interface AssistantBubbleProps {
  content: string;
  turn?: ChatTurnState;
  reduce: boolean | null;
}

const AssistantBubble = memo(function AssistantBubble({
  content,
  turn,
  reduce,
}: AssistantBubbleProps) {
  const reasoning = turn?.reasoning ?? null;
  const table = turn?.table ?? null;
  const sql = turn?.sql ?? null;
  const citations = turn?.citations ?? [];
  const isStreaming = turn?.status === "running";
  const isEmptyStream = isStreaming && content.length === 0;
  const [, copyToClipboard] = useCopyToClipboard();
  const [copied, setCopied] = useState<boolean>(false);

  const handleCopy = useCallback(async (): Promise<void> => {
    if (content.length === 0) return;
    const ok = await copyToClipboard(content);
    if (ok) {
      toast.success("Answer copied");
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } else {
      toast.error("Couldn't copy — clipboard unavailable");
    }
  }, [content, copyToClipboard]);

  return (
    <BubbleRow reduce={reduce} align="start">
      {/* Bot avatar — a small accent square with the Baller "B" monogram. */}
      <div
        aria-hidden="true"
        className={cn(
          "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg",
          "bg-[color:var(--color-primary)]/12 text-[color:var(--color-primary)]",
          "ring-1 ring-inset ring-[color:var(--color-primary)]/20",
        )}
      >
        <span className="font-display text-sm font-bold leading-none">B</span>
      </div>

      <article
        aria-label="Assistant answered"
        className={cn(
          "flex w-full max-w-[92%] flex-col gap-2.5 rounded-2xl rounded-tl-md",
          "border border-[color:var(--color-border)] bg-[color:var(--color-card)] px-4 py-3 text-sm",
          // Left accent border — the signature "data card" cue.
          "border-l-[3px] border-l-[color:var(--color-accent-orange)]",
          "shadow-sm shadow-black/5",
        )}
      >
        {/* Author row — name + streaming pill / duration. */}
        <div className="flex items-center gap-2">
          <span className="font-display text-xs font-semibold tracking-tight text-[color:var(--color-foreground)]">
            Assistant
          </span>
          {isStreaming && (
            <span className="inline-flex items-center gap-1 text-[0.7rem] text-[color:var(--color-muted-foreground)]">
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[color:var(--color-accent-orange)] opacity-75" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-[color:var(--color-accent-orange)]" />
              </span>
              answering…
            </span>
          )}
          {turn?.queryDurationMs !== null &&
            turn?.queryDurationMs !== undefined &&
            !isStreaming && (
              <span className="text-[0.7rem] text-[color:var(--color-muted-foreground)]">
                · {(turn.queryDurationMs / 1000).toFixed(2)}s
              </span>
            )}
        </div>

        {/* Answer body. */}
        {isEmptyStream ? <TypingDots /> : <MarkdownContent content={content} />}

        {/* Rich panels — composed as they arrive from the stream. */}
        {(reasoning || (sql !== null && sql.length > 0) || table || citations.length > 0) && (
          <div className="flex flex-col gap-2 pt-0.5">
            {reasoning && (
              <ReasoningPanel summary={reasoning.summary} executionPlan={reasoning.executionPlan} />
            )}
            {sql !== null && sql.length > 0 && <SqlPanel sql={sql} />}
            {table && <AnswerChart table={table} />}
            {table && <ResultTable table={table} />}
            {citations.length > 0 && <EvidenceCard citations={citations} />}
          </div>
        )}

        {/* Footer actions — copy answer (hidden while streaming / empty). */}
        {!isStreaming && content.length > 0 && (
          <div className="flex items-center justify-end pt-0.5">
            <button
              type="button"
              onClick={() => {
                void handleCopy();
              }}
              className={cn(
                "inline-flex items-center gap-1 rounded-md px-2 py-1 text-[0.7rem] font-medium",
                "text-[color:var(--color-muted-foreground)] transition-colors",
                "hover:bg-[color:var(--color-muted)] hover:text-[color:var(--color-foreground)]",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[color:var(--color-card)]",
              )}
              aria-label={copied ? "Answer copied to clipboard" : "Copy answer to clipboard"}
            >
              {copied ? (
                <Check className="h-3 w-3 text-[color:var(--color-ok-fg)]" aria-hidden="true" />
              ) : (
                <Copy className="h-3 w-3" aria-hidden="true" />
              )}
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
        )}
      </article>
    </BubbleRow>
  );
});

function TypingDots() {
  // Decorative only — ChatTimeline's sr-only polite region already
  // announces "Assistant is typing…", so the dots carry no role/label
  // (avoids colliding with the cancelled-note `role="status"` the e2e
  // error-path test scopes via getByRole("status")).
  return (
    <div className="flex items-center gap-1 py-1" aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="typing-dot h-1.5 w-1.5 rounded-full bg-[color:var(--color-primary)]"
        />
      ))}
    </div>
  );
}

interface BubbleRowProps {
  align: "start" | "end";
  reduce: boolean | null;
  children: ReactNode;
}

function BubbleRow({ align, reduce, children }: BubbleRowProps) {
  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={
        reduce ? { duration: 0 } : { type: "spring", stiffness: 380, damping: 30, mass: 0.6 }
      }
      className={cn("flex w-full gap-2.5", align === "end" ? "justify-end" : "justify-start")}
    >
      {children}
    </motion.div>
  );
}
