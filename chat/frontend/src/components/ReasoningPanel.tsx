/**
 * Reasoning panel (PLAN §8.3, §8.4).
 *
 * Collapsible (`<details>`/`<summary>` — accessible by default). Shows
 * the `summary` and optional `execution_plan` from the backend's
 * `reasoning` event.
 *
 * **Privacy contract (critical):** the backend never sends model
 * chain-of-thought (PLAN §7.7 step 8 — "structured (non-CoT) reasoning
 * derived from the agent's plan"). This component therefore only ever
 * renders `summary` and `execution_plan` — both are *post-hoc* facts
 * about the pipeline, not private model reasoning. The component
 * carries a comment to make the contract explicit for future fixers.
 *
 * Expanded by default per §8.3 (it's a small panel and reviewers want
 * to see the chosen plan alongside the answer).
 */
import { ChevronRight, Lightbulb } from "lucide-react";

import { cn } from "@/lib/utils";

export interface ReasoningPanelProps {
  summary: string;
  executionPlan?: string | null;
}

export function ReasoningPanel({ summary, executionPlan }: ReasoningPanelProps) {
  return (
    <details
      open
      className={cn(
        "group rounded-lg border border-[color:var(--color-border)]",
        "bg-[color:var(--color-muted)]/40",
      )}
    >
      <summary
        className={cn(
          "flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm font-medium select-none",
          "transition-colors hover:bg-[color:var(--color-muted)]",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)]",
          "focus-visible:ring-offset-2 focus-visible:ring-offset-[color:var(--color-card)]",
          "[&::-webkit-details-marker]:hidden",
        )}
      >
        <ChevronRight
          className="h-4 w-4 text-[color:var(--color-muted-foreground)] transition-transform duration-200 group-open:rotate-90"
          aria-hidden="true"
        />
        <Lightbulb className="h-4 w-4 text-[color:var(--color-accent-orange)]" aria-hidden="true" />
        Reasoning
      </summary>
      <div className="flex flex-col gap-2 px-3 pb-3 pt-0.5 text-sm leading-relaxed">
        <p className="text-[color:var(--color-foreground)]/90">{summary}</p>
        {executionPlan !== null && executionPlan !== undefined && executionPlan.length > 0 && (
          <>
            <p className="font-display text-[0.65rem] font-semibold uppercase tracking-[0.12em] text-[color:var(--color-muted-foreground)]">
              Execution plan
            </p>
            <pre className="m-0 overflow-x-auto whitespace-pre-wrap rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-card)] p-2.5 font-mono text-xs leading-relaxed">
              {executionPlan}
            </pre>
          </>
        )}
      </div>
    </details>
  );
}
