import type { ReactNode } from "react";

interface SectionHeadingProps {
  title: string;
  description?: string;
  /** Optional trailing node rendered on the end side at `sm+` widths (e.g. a row-count chip). */
  trailing?: ReactNode;
}

/** Shared section heading: title + optional muted description, with an optional trailing element. */
export function SectionHeading({ title, description, trailing }: SectionHeadingProps) {
  return (
    <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h2 className="text-base font-semibold text-court-ink">{title}</h2>
        {description ? <p className="text-sm text-court-muted">{description}</p> : null}
      </div>
      {trailing ?? null}
    </div>
  );
}
