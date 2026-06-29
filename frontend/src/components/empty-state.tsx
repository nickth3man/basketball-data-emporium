interface EmptyStateProps {
  title: string;
  /** Optional supporting copy under the title (e.g. an error message or "no rows" hint). */
  detail?: string;
}

/**
 * Shared empty/no-data affordance. Dashed-border card with a required
 * `title` and optional secondary `detail` line. Used for empty datasets,
 * "no search results", and query-error fallbacks.
 */
export function EmptyState({ title, detail }: EmptyStateProps) {
  return (
    <div className="rounded-md border border-dashed border-court-line bg-white px-5 py-8 text-sm">
      <p className="font-medium text-court-ink">{title}</p>
      {detail ? <p className="mt-1 text-court-muted">{detail}</p> : null}
    </div>
  );
}
