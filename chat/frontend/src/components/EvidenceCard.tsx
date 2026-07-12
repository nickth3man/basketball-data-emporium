/**
 * EvidenceCard (PLAN §8.3).
 *
 * Groups `Citation` events from the SSE stream into a compact, polished
 * evidence block. Each citation becomes a labelled chip tagged with its
 * provenance:
 *
 *   - `table_name` → the warehouse table that contributed
 *   - `metric_key` → a row from `meta_metric_definition`
 *   - `gap_key`    → a row from `meta_known_gap` (caveat surfaced)
 *
 * Per §8.3 the metric_key / gap_key links could route to the
 * `/api/admin/tables/meta_*` browser; v1 keeps them as descriptive chips
 * (hovering shows the key; clicking is a no-op for now). The chips are
 * non-interactive, so they carry no aria-label (text content is the
 * accessible name); the section's `role="region"` + aria-label orients
 * screen readers, which the e2e smoke asserts on.
 */
import { Database, Flag, Gauge } from "lucide-react";

import { cn } from "@/lib/utils";
import type { Citation } from "@/generated/sse-events";

export interface EvidenceCardProps {
  citations: Citation[];
}

type ChipKind = "table" | "metric" | "gap";

interface ChipDescriptor {
  label: string;
  kind: ChipKind;
  detail?: string | null;
}

function describe(c: Citation): ChipDescriptor | null {
  if (c.table_name) return { label: c.table_name, kind: "table" };
  if (c.metric_key) return { label: c.metric_key, kind: "metric", detail: "metric definition" };
  if (c.gap_key) return { label: c.gap_key, kind: "gap", detail: "known data gap" };
  return null;
}

const KIND_ICON: Record<ChipKind, typeof Database> = {
  table: Database,
  metric: Gauge,
  gap: Flag,
};

const KIND_PILL: Record<ChipKind, string> = {
  table:
    "border-[color:var(--color-border)] bg-[color:var(--color-card)] text-[color:var(--color-foreground)]",
  metric:
    "border-[color:var(--color-accent)]/30 bg-[color:var(--color-accent)]/10 text-[color:var(--color-accent)]",
  gap: "border-[color:var(--color-warn-border)] bg-[color:var(--color-warn-bg)] text-[color:var(--color-warn-fg)]",
};

const KIND_TAG: Record<ChipKind, string> = {
  table: "table",
  metric: "metric",
  gap: "gap",
};

export function EvidenceCard({ citations }: EvidenceCardProps) {
  const chips = citations.map(describe).filter((c): c is ChipDescriptor => c !== null);

  return (
    <section
      aria-label="Evidence citations"
      className="flex flex-col gap-2 rounded-lg border border-border bg-muted/50 px-3 py-2.5"
    >
      <header className="flex items-center gap-1.5 text-[0.65rem] font-semibold tracking-[0.12em] text-muted-foreground uppercase">
        <Database className="size-3" aria-hidden="true" />
        Evidence
        <span aria-hidden="true" className="opacity-40">
          ·
        </span>
        <span>
          {chips.length} source{chips.length === 1 ? "" : "s"}
        </span>
      </header>
      {chips.length === 0 ? (
        <p className="text-xs text-muted-foreground">No citations.</p>
      ) : (
        <ul className="flex flex-wrap gap-1.5" id="evidence">
          {chips.map((chip) => {
            const Icon = KIND_ICON[chip.kind];
            return (
              <li key={`${chip.kind}:${chip.label}`}>
                <span
                  title={chip.detail ?? chip.label}
                  className={cn(
                    `inline-flex items-center gap-1.5 rounded-md border px-2 py-1`,
                    "font-mono text-[0.7rem] leading-none",
                    KIND_PILL[chip.kind],
                  )}
                >
                  <Icon className="size-3 shrink-0 opacity-80" aria-hidden="true" />
                  <span className="text-[0.6rem] tracking-wide uppercase opacity-60">
                    {KIND_TAG[chip.kind]}
                  </span>
                  <span className="font-medium">{chip.label}</span>
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
