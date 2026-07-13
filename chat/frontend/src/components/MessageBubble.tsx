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
            `max-w-[85%] rounded-2xl rounded-br-md px-3.5 py-2 text-sm/relaxed`,
            `bg-(--color-primary) text-primary-foreground`,
            "shadow-sm shadow-(color:--color-primary)/25",
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

interface AssistantViewState {
  reasoning: ChatTurnState["reasoning"];
  table: ChatTurnState["table"];
  sql: string | null;
  citations: ChatTurnState["citations"];
  queryDurationMs: number | null;
  isStreaming: boolean;
  isEmptyStream: boolean;
}

function assistantViewState(turn: ChatTurnState | undefined, content: string): AssistantViewState {
  if (!turn) {
    return {
      reasoning: null,
      table: null,
      sql: null,
      citations: [],
      queryDurationMs: null,
      isStreaming: false,
      isEmptyStream: false,
    };
  }

  const isStreaming = turn.status === "running";
  return {
    reasoning: turn.reasoning,
    table: turn.table,
    sql: turn.sql,
    citations: turn.citations,
    queryDurationMs: turn.queryDurationMs,
    isStreaming,
    isEmptyStream: isStreaming && content.length === 0,
  };
}

const AssistantBubble = memo(function AssistantBubble({
  content,
  turn,
  reduce,
}: AssistantBubbleProps) {
  const view = assistantViewState(turn, content);

  return (
    <BubbleRow reduce={reduce} align="start">
      <AssistantAvatar />

      <article
        aria-label="Assistant answered"
        className={cn(
          "flex w-full max-w-[92%] flex-col gap-2.5 rounded-2xl rounded-tl-md",
          `border border-border bg-card px-4 py-3 text-sm`,
          // Left accent border — the signature "data card" cue.
          "border-l-[3px] border-l-(--color-accent-orange)",
          "shadow-sm shadow-black/5",
        )}
      >
        <AssistantHeader isStreaming={view.isStreaming} queryDurationMs={view.queryDurationMs} />

        <AnswerBody content={content} isEmptyStream={view.isEmptyStream} />

        <AssistantPanels
          reasoning={view.reasoning}
          sql={view.sql}
          table={view.table}
          citations={view.citations}
        />
        <AnswerCopyButton content={content} isStreaming={view.isStreaming} />
      </article>
    </BubbleRow>
  );
});

function AssistantAvatar() {
  return (
    <div
      aria-hidden="true"
      className={cn(
        "mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg",
        "bg-(--color-primary)/12 text-(--color-primary)",
        "ring-1 ring-(--color-primary)/20 ring-inset",
      )}
    >
      <span className="font-display text-sm leading-none font-bold">B</span>
    </div>
  );
}

interface AssistantHeaderProps {
  isStreaming: boolean;
  queryDurationMs: number | null;
}

function AssistantHeader({ isStreaming, queryDurationMs }: AssistantHeaderProps) {
  return (
    <div className="flex items-center gap-2">
      <span className="font-display text-xs font-semibold tracking-tight text-(--color-foreground)">
        Assistant
      </span>
      {isStreaming && (
        <span className="inline-flex items-center gap-1 text-[0.7rem] text-muted-foreground">
          <span className="relative flex size-1.5">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-(--color-accent-orange) opacity-75" />
            <span className="relative inline-flex size-1.5 rounded-full bg-(--color-accent-orange)" />
          </span>
          answering…
        </span>
      )}
      {!isStreaming && queryDurationMs !== null && (
        <span className="text-[0.7rem] text-muted-foreground">
          · {(queryDurationMs / 1000).toFixed(2)}s
        </span>
      )}
    </div>
  );
}

function AnswerBody({ content, isEmptyStream }: { content: string; isEmptyStream: boolean }) {
  return isEmptyStream ? <TypingDots /> : <MarkdownContent content={content} />;
}

interface AssistantPanelsProps {
  reasoning: ChatTurnState["reasoning"];
  sql: string | null;
  table: ChatTurnState["table"];
  citations: ChatTurnState["citations"];
}

function AssistantPanels({ reasoning, sql, table, citations }: AssistantPanelsProps) {
  const hasSql = sql !== null && sql.length > 0;
  if (!reasoning && !hasSql && !table && citations.length === 0) return null;

  return (
    <div className="flex flex-col gap-2 pt-0.5">
      <ReasoningDetails reasoning={reasoning} />
      <SqlDetails sql={hasSql ? sql : null} />
      <TableDetails table={table} />
      <CitationDetails citations={citations} />
    </div>
  );
}

function ReasoningDetails({ reasoning }: Pick<AssistantPanelsProps, "reasoning">) {
  if (!reasoning) return null;
  return <ReasoningPanel summary={reasoning.summary} executionPlan={reasoning.executionPlan} />;
}

function SqlDetails({ sql }: Pick<AssistantPanelsProps, "sql">) {
  return sql ? <SqlPanel sql={sql} /> : null;
}

function TableDetails({ table }: Pick<AssistantPanelsProps, "table">) {
  if (!table) return null;
  return (
    <>
      <AnswerChart table={table} />
      <ResultTable table={table} />
    </>
  );
}

function CitationDetails({ citations }: Pick<AssistantPanelsProps, "citations">) {
  return citations.length > 0 ? <EvidenceCard citations={citations} /> : null;
}

interface AnswerCopyButtonProps {
  content: string;
  isStreaming: boolean;
}

function AnswerCopyButton({ content, isStreaming }: AnswerCopyButtonProps) {
  const [, copyToClipboard] = useCopyToClipboard();
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async (): Promise<void> => {
    const copiedSuccessfully = await copyToClipboard(content);
    if (!copiedSuccessfully) {
      toast.error("Couldn't copy — clipboard unavailable");
      return;
    }

    toast.success("Answer copied");
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }, [content, copyToClipboard]);

  if (isStreaming || content.length === 0) return null;

  return (
    <div className="flex items-center justify-end pt-0.5">
      <button
        type="button"
        onClick={() => void handleCopy()}
        className={cn(
          `inline-flex items-center gap-1 rounded-md px-2 py-1 text-[0.7rem] font-medium`,
          "text-(--color-foreground) transition-colors",
          "hover:bg-muted",
          `focus-visible:ring-2 focus-visible:ring-(--color-ring) focus-visible:ring-offset-2 focus-visible:ring-offset-card focus-visible:outline-none`,
        )}
        aria-label={copied ? "Answer copied to clipboard" : "Copy answer to clipboard"}
      >
        {copied ? (
          <Check className="size-3 text-ok-fg" aria-hidden="true" />
        ) : (
          <Copy className="size-3" aria-hidden="true" />
        )}
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

function TypingDots() {
  // Decorative only — ChatTimeline's sr-only polite region already
  // announces "Assistant is typing…", so the dots carry no role/label
  // (avoids colliding with the cancelled-note `role="status"` the e2e
  // error-path test scopes via getByRole("status")).
  return (
    <div className="flex items-center gap-1 py-1" aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <span key={i} className="typing-dot size-1.5 rounded-full bg-(--color-primary)" />
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
      className={cn("flex w-full gap-2.5", align === "end" ? "justify-end" : `justify-start`)}
    >
      {children}
    </motion.div>
  );
}
