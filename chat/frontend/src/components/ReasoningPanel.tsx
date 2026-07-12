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
    <details open className={cn("group rounded-lg border border-border", "bg-muted/40")}>
      <summary
        className={cn(
          `flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm font-medium select-none`,
          `transition-colors hover:bg-muted`,
          `focus-visible:ring-2 focus-visible:ring-(--color-ring) focus-visible:outline-none`,
          `focus-visible:ring-offset-2 focus-visible:ring-offset-card`,
          "[&::-webkit-details-marker]:hidden",
        )}
      >
        <ChevronRight
          className="size-4 text-muted-foreground transition-transform duration-200 group-open:rotate-90"
          aria-hidden="true"
        />
        <Lightbulb className="size-4 text-(--color-accent-orange)" aria-hidden="true" />
        Reasoning
      </summary>
      <div className="flex flex-col gap-2 px-3 pt-0.5 pb-3 text-sm/relaxed">
        <p className="text-(--color-foreground)/90">{summary}</p>
        {executionPlan !== null && executionPlan !== undefined && executionPlan.length > 0 && (
          <>
            <p className="font-display text-[0.65rem] font-semibold tracking-[0.12em] text-muted-foreground uppercase">
              Execution plan
            </p>
            <pre className="m-0 overflow-x-auto rounded-md border border-border bg-card p-2.5 font-mono text-xs/relaxed whitespace-pre-wrap">
              {executionPlan}
            </pre>
          </>
        )}
      </div>
    </details>
  );
}
