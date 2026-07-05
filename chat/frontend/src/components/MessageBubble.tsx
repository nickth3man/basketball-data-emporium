/**
 * MessageBubble (PLAN §8.3).
 *
 * One chat message: user or assistant styled. For an assistant turn,
 * the answer text is rendered plus the rich panels — table, SQL,
 * reasoning, evidence — composed conditionally on the live `turn` state.
 *
 * The streaming answer (live `turn.answer`) is wrapped in an
 * `aria-live="polite"` region by `ChatTimeline`; the bubble itself
 * does not carry the live region (only one polite region per page is
 * the recommended pattern, and `ChatTimeline` owns it).
 *
 * The bubble also renders the inline citation chips via the EvidenceCard
 * (placed beneath the answer for v1; v2 polish can anchor chip clicks
 * to the evidence block).
 */
import { EvidenceCard } from "@/components/EvidenceCard";
import { ReasoningPanel } from "@/components/ReasoningPanel";
import { ResultTable } from "@/components/ResultTable";
import { SqlPanel } from "@/components/SqlPanel";
import type { ChatTurnState } from "@/hooks/useChatTurn";

export type TimelineRole = "user" | "assistant";

export interface MessageBubbleProps {
  /** Conversation role — not a JSX role attribute (renamed from `role` to
   *  avoid colliding with the jsx-a11y `role` ARIA-attribute check). */
  speaker: TimelineRole;
  content: string;
  /** Assistant-only: the live/final turn state to render rich panels from. */
  turn?: ChatTurnState;
}

export function MessageBubble({ speaker, content, turn }: MessageBubbleProps) {
  if (speaker === "user") {
    return (
      <div className="flex justify-end">
        <article
          aria-label="You said"
          className="max-w-[85%] rounded-2xl rounded-br-sm bg-[color:var(--color-foreground)] px-3 py-2 text-sm text-[color:var(--color-background)]"
        >
          {content}
        </article>
      </div>
    );
  }

  // Assistant turn — render answer + (optional) rich panels.
  const reasoning = turn?.reasoning ?? null;
  const table = turn?.table ?? null;
  const sql = turn?.sql ?? null;
  const citations = turn?.citations ?? [];

  return (
    <div className="flex justify-start">
      <article
        aria-label="Assistant answered"
        className="flex max-w-[90%] flex-col gap-2 rounded-2xl rounded-bl-sm border border-[color:var(--color-border)] bg-[color:var(--color-card)] px-3 py-2 text-sm"
      >
        <p className="whitespace-pre-wrap">{content}</p>
        {reasoning && (
          <ReasoningPanel summary={reasoning.summary} executionPlan={reasoning.executionPlan} />
        )}
        {sql !== null && sql.length > 0 && <SqlPanel sql={sql} />}
        {table && <ResultTable table={table} />}
        {citations.length > 0 && <EvidenceCard citations={citations} />}
      </article>
    </div>
  );
}
