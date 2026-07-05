/**
 * EvidenceCard (PLAN §8.3).
 *
 * Groups `Citation` events from the SSE stream into a compact evidence
 * block. Each citation becomes a chip tagged with its provenance:
 *
 *   - `table_name` → the warehouse table that contributed
 *   - `metric_key` → a row from `meta_metric_definition`
 *   - `gap_key`    → a row from `meta_known_gap` (caveat surfaced)
 *
 * Per §8.3 the metric_key / gap_key links could route to the
 * `/api/admin/tables/meta_*` browser; v1 keeps them as descriptive
 * chips (hovering shows the key; clicking is a no-op for now — Phase 7
 * polish can wire the link target).
 */
import type { Citation } from "@/generated/sse-events";

export interface EvidenceCardProps {
  citations: Citation[];
}

interface ChipDescriptor {
  label: string;
  kind: "table" | "metric" | "gap";
  detail?: string | null;
}

function describe(c: Citation): ChipDescriptor | null {
  if (c.table_name) {
    return { label: c.table_name, kind: "table" };
  }
  if (c.metric_key) {
    return {
      label: c.metric_key,
      kind: "metric",
      detail: "metric definition",
    };
  }
  if (c.gap_key) {
    return {
      label: c.gap_key,
      kind: "gap",
      detail: "known data gap",
    };
  }
  return null;
}

const KIND_STYLES: Record<ChipDescriptor["kind"], string> = {
  table:
    "bg-[color:var(--color-background)] border-[color:var(--color-border)] text-[color:var(--color-foreground)]",
  metric:
    "bg-[color:var(--color-muted)] border-[color:var(--color-border)] text-[color:var(--color-foreground)]",
  gap: "bg-amber-50 border-amber-200 text-amber-900",
};

const KIND_LABEL: Record<ChipDescriptor["kind"], string> = {
  table: "table",
  metric: "metric",
  gap: "gap",
};

const KIND_ARIA_PREFIX: Record<ChipDescriptor["kind"], string> = {
  table: "Cites table",
  metric: "Cites metric definition",
  gap: "Known data gap",
};

/**
 * Accessible name for a citation chip (PLAN §15 — minor a11y). The
 * visible inner spans are kept as-is; the accessible name annotates
 * what each chip represents without polluting the visual layout.
 */
function chipAriaLabel(chip: ChipDescriptor): string {
  return `${KIND_ARIA_PREFIX[chip.kind]} ${chip.label}`;
}

export function EvidenceCard({ citations }: EvidenceCardProps) {
  const chips = citations.map(describe).filter((c): c is ChipDescriptor => c !== null);

  if (chips.length === 0) {
    return (
      <p className="text-xs text-[color:var(--color-muted-foreground)]">
        No citations.
      </p>
    );
  }

  return (
    <section
      aria-label="Evidence citations"
      className="flex flex-col gap-2 rounded border border-[color:var(--color-border)] bg-[color:var(--color-muted)] px-3 py-2"
    >
      <header className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-[color:var(--color-muted-foreground)]">
        Evidence
        <span aria-hidden="true">·</span>
        <span>{chips.length} source{chips.length === 1 ? "" : "s"}</span>
      </header>
      <ul className="flex flex-wrap gap-1.5" id="evidence">
        {chips.map((chip) => (
          <li key={`${chip.kind}:${chip.label}`}>
            <span
              className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 font-mono text-xs ${KIND_STYLES[chip.kind]}`}
              title={chip.detail ?? chip.label}
              aria-label={chipAriaLabel(chip)}
            >
              <span aria-hidden="true" className="text-[10px] uppercase tracking-wide opacity-70">
                {KIND_LABEL[chip.kind]}
              </span>
              <span>{chip.label}</span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}