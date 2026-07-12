/**
 * ResultTable (PLAN §8.3, §16).
 *
 * TanStack Table v8 + TanStack Virtual v3. Columns are built from
 * `ChatTurnTable.columns`. Cell values are formatted with d3-format
 * (per-column format picked from the column name + sample values), with
 * a toLocaleString fallback for general floats. Native `<table>` keeps
 * the implicit `role="table"` the e2e smoke asserts on.
 *
 * Render cap (PLAN §16): the UI never renders more than 10,000 rows.
 *
 * Sortable column headers (TanStack `getSortedRowModel`) and a "Copy CSV"
 * affordance that copies the currently rendered rows + fires a sonner
 * toast.
 */
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { format as d3Format } from "d3-format";
import { useCallback, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, ChevronsUpDown, Check, Copy, Table2 } from "lucide-react";
import { toast } from "sonner";

import type { ChatTurnTable } from "@/hooks/useChatTurn";
import type { ColumnSpec } from "@/generated/sse-events";
import { rowsToCsv } from "@/lib/csv";
import { cn } from "@/lib/utils";

const MAX_RENDERED_ROWS = 10_000;
const MAX_TABLE_HEIGHT = 480;

export interface ResultTableProps {
  table: ChatTurnTable;
}

interface CellRow extends Record<string, unknown> {
  __rowIndex: number;
}

/**
 * Build a per-column number formatter from the column name + its sample
 * values. Returns a `(value: unknown) => string`. Non-numeric columns
 * fall back to a generic stringifier.
 */
function makeColumnFormatter(
  spec: ColumnSpec,
  rows: Record<string, unknown>[],
): (value: unknown) => string {
  const name = spec.name.toLowerCase();
  const values = rows
    .map((r) => r[spec.name])
    .filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  const raw = formatRaw;

  if (values.length === 0) return raw;

  const allInt = values.every((v) => Number.isInteger(v));
  const maxAbs = Math.max(...values.map((v) => Math.abs(v)));

  // Percentage / rate columns. NBA convention: proportions in [0,1]
  // (e.g. fg_pct = 0.482) → ".1%"; already-on-0-100 scales append "%".
  if (
    /(pct|percent|rate|eff_|efg|ts_|_fg|fg3|ft_|per36|usage|usg)/.test(name) ||
    name.includes("%")
  ) {
    if (maxAbs <= 1.5) return guard(d3Format(".1%"));
    if (maxAbs <= 150) return (v) => `${d3Format(".1f")(asNum(v))}%`;
  }

  // Currency-ish large numbers (salaries, cap hits).
  if (/(salary|cap|price|cost|usd|dollar|payroll)/.test(name) && maxAbs >= 1000) {
    return (v) => `$${d3Format(",.0f")(asNum(v))}`;
  }

  // Grouped integers (game counts, totals).
  if (allInt) return guard(d3Format(",.0f"));

  // General floats: trim trailing zeros via toLocaleString for a clean
  // read (PER, PPG, etc. all look better without fixed ".000").
  return (v) =>
    typeof v === "number" && Number.isFinite(v)
      ? v.toLocaleString(undefined, { maximumFractionDigits: 3 })
      : raw(v);
}

function asNum(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

/** Wrap a d3 formatter so non-numeric / null values don't print "NaN". */
function guard(fn: (n: number) => string): (v: unknown) => string {
  return (v) => (typeof v === "number" && Number.isFinite(v) ? fn(v) : formatRaw(v));
}

function formatRaw(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") {
    if (Number.isInteger(value)) return value.toLocaleString();
    return value.toLocaleString(undefined, { maximumFractionDigits: 6 });
  }
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "bigint") return value.toString();
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

export function ResultTable({ table }: ResultTableProps) {
  const totalRowCount = table.rowCount;
  const allRows = table.rows;
  const truncatedByCap = allRows.length > MAX_RENDERED_ROWS;
  const visibleRows = truncatedByCap ? allRows.slice(0, MAX_RENDERED_ROWS) : allRows;

  const data = useMemo<CellRow[]>(
    () => visibleRows.map((r, i) => ({ ...r, __rowIndex: i })),
    [visibleRows],
  );

  // Pre-compute a formatter per column (memoized on the column specs +
  // the row batch so it survives the live turn's table_ready refresh).
  const formatters = useMemo(() => {
    const map = new Map<string, (v: unknown) => string>();
    for (const spec of table.columns) {
      map.set(spec.name, makeColumnFormatter(spec, visibleRows));
    }
    return map;
  }, [table.columns, visibleRows]);

  const columns = useMemo<ColumnDef<CellRow>[]>(
    () =>
      table.columns.map((spec) => ({
        id: spec.name,
        accessorKey: spec.name,
        header: spec.name,
        cell: (info) => (formatters.get(spec.name) ?? formatRaw)(info.getValue()),
        sortingFn: (a, b, columnId) => {
          const av = a.getValue(columnId);
          const bv = b.getValue(columnId);
          if (typeof av === "number" && typeof bv === "number") return av - bv;
          return formatRaw(av).localeCompare(formatRaw(bv));
        },
      })),
    [table.columns, formatters],
  );

  const [sorting, setSorting] = useState<SortingState>([]);

  const tableInstance = useReactTable<CellRow>({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const sortedRows = tableInstance.getRowModel().rows;

  const [copied, setCopied] = useState<boolean>(false);

  const handleCopyCsv = useCallback(async (): Promise<void> => {
    try {
      const rowsForCsv = sortedRows.map((row) => {
        const out: Record<string, unknown> = {};
        for (const c of table.columns) out[c.name] = row.original[c.name];
        return out;
      });
      const csv = rowsToCsv(
        table.columns.map((c) => c.name),
        rowsForCsv,
      );
      await navigator.clipboard.writeText(csv);
      setCopied(true);
      toast.success(`Copied ${sortedRows.length.toLocaleString()} rows as CSV`);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
      toast.error("Couldn't copy — clipboard unavailable");
    }
  }, [sortedRows, table.columns]);

  if (table.columns.length === 0) {
    return (
      <p className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-sm text-muted-foreground">
        No rows.
      </p>
    );
  }

  return (
    <section
      aria-label={`Result table with ${table.columns.length} column${table.columns.length === 1 ? "" : "s"}`}
      className="flex flex-col gap-0 overflow-hidden rounded-lg border border-border bg-card"
    >
      <header className="flex items-center justify-between gap-2 border-b border-border bg-muted/40 px-3 py-2 text-[0.7rem] text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <Table2 className="size-3.5" aria-hidden="true" />
          <span>
            {sortedRows.length.toLocaleString()} of {totalRowCount.toLocaleString()} row
            {totalRowCount === 1 ? "" : "s"}
            {table.truncated ? " · backend-truncated" : ""}
            {truncatedByCap ? " · UI-render cap" : ""}
          </span>
        </span>
        <button
          type="button"
          onClick={() => {
            void handleCopyCsv();
          }}
          className={cn(
            `inline-flex items-center gap-1 rounded-md border border-border`,
            "bg-card px-2 py-1 text-xs font-medium",
            `transition-colors hover:bg-muted`,
            `focus-visible:ring-2 focus-visible:ring-(--color-ring) focus-visible:ring-offset-2 focus-visible:ring-offset-card focus-visible:outline-none`,
          )}
          aria-label={copied ? "CSV copied to clipboard" : "Copy result rows as CSV"}
        >
          {copied ? (
            <Check className="size-3 text-ok-fg" aria-hidden="true" />
          ) : (
            <Copy className="size-3" aria-hidden="true" />
          )}
          {copied ? "Copied" : "Copy CSV"}
        </button>
      </header>
      <div
        ref={scrollRef}
        className="relative overflow-auto focus-visible:ring-2 focus-visible:ring-(--color-ring) focus-visible:outline-none focus-visible:ring-inset"
        style={{ maxHeight: MAX_TABLE_HEIGHT }}
      >
        <table className="w-full border-collapse text-sm">
          <thead className="sticky top-0 z-10 bg-muted backdrop-blur-sm">
            {tableInstance.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((header) => {
                  const canSort = header.column.getCanSort();
                  const sortDir = header.column.getIsSorted();
                  const ariaSort: "ascending" | "descending" | "none" =
                    sortDir === "asc" ? "ascending" : sortDir === "desc" ? "descending" : "none";
                  return (
                    <th
                      key={header.id}
                      scope="col"
                      aria-sort={canSort ? ariaSort : undefined}
                      className="border-b border-border px-2.5 py-2 text-left font-display text-[0.7rem] font-semibold tracking-[0.06em] text-muted-foreground uppercase"
                    >
                      {canSort ? (
                        <button
                          type="button"
                          onClick={header.column.getToggleSortingHandler()}
                          className={cn(
                            `inline-flex items-center gap-1 rounded-sm px-1 py-0.5 transition-colors`,
                            `hover:bg-background hover:text-(--color-foreground)`,
                            `focus-visible:ring-2 focus-visible:ring-(--color-ring) focus-visible:ring-offset-1 focus-visible:ring-offset-muted focus-visible:outline-none`,
                          )}
                          aria-label={`Sort by ${header.column.columnDef.header as string} ${ariaSort === "ascending" ? "descending" : "ascending"}`}
                        >
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          <SortIcon dir={sortDir} />
                        </button>
                      ) : (
                        flexRender(header.column.columnDef.header, header.getContext())
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {sortedRows.map((row, rowIdx) => (
              <tr
                key={row.id}
                className={cn(
                  `transition-colors hover:bg-muted/50`,
                  rowIdx % 2 === 1 && "bg-muted/25",
                )}
              >
                {row.getVisibleCells().map((cell) => (
                  <td
                    key={cell.id}
                    className="border-b border-border/60 px-2.5 py-1.5 align-top tabular-nums"
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function SortIcon({ dir }: { dir: false | "asc" | "desc" }) {
  if (dir === "asc") return <ArrowUp className="size-3" aria-hidden="true" />;
  if (dir === "desc") return <ArrowDown className="size-3" aria-hidden="true" />;
  return <ChevronsUpDown className="size-3 opacity-40" aria-hidden="true" />;
}
