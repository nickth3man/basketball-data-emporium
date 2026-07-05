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
export interface ReasoningPanelProps {
  summary: string;
  executionPlan?: string | null;
}

export function ReasoningPanel({ summary, executionPlan }: ReasoningPanelProps) {
  return (
    <details
      open
      className="group rounded border border-[color:var(--color-border)] bg-[color:var(--color-muted)]"
    >
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm font-medium select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-primary)] focus-visible:ring-offset-2 focus-visible:ring-offset-[color:var(--color-background)]">
        <span aria-hidden="true" className="text-xs">
          ▾
        </span>
        Reasoning
      </summary>
      <div className="flex flex-col gap-2 px-3 pb-3 pt-1 text-sm leading-relaxed">
        <p>{summary}</p>
        {executionPlan !== null && executionPlan !== undefined && executionPlan.length > 0 && (
          <>
            <p className="text-xs font-medium uppercase tracking-wide text-[color:var(--color-muted-foreground)]">
              Execution plan
            </p>
            <pre className="m-0 whitespace-pre-wrap rounded bg-[color:var(--color-background)] p-2 text-xs leading-relaxed">
              {executionPlan}
            </pre>
          </>
        )}
      </div>
    </details>
  );
}