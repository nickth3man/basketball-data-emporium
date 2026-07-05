/**
 * ResultTable (PLAN §8.3, §16).
 *
 * TanStack Table v8 + TanStack Virtual v3. Columns are built from
 * `ChatTurnTable.columns` (one `ColumnSpec` per column from the
 * `table_ready` event); cell values are stringified before render
 * because the backend already produces JSON-safe values.
 *
 * Render cap (PLAN §16):
 *   - The backend may return up to DEFAULT_LIMIT rows in the SSE preview.
 *   - The UI never renders more than 10,000 rows; if `table.rows.length`
 *     exceeds that, we slice + show a "Showing N of M rows" notice.
 *
 * Sortable column headers (TanStack `getSortedRowModel`) and a
 * "Copy CSV" affordance that copies the currently rendered rows.
 *
 * Accessibility:
 *   - A native `<table>` with `<thead>`/`<tbody>` and `scope="col"`.
 *   - Sort buttons in headers carry `aria-sort` semantics via TanStack's
 *     `flexRender` output (the lib writes `aria-sort` for us).
 *   - The outer scroll container carries a focusable `tabIndex` so
 *     keyboard users can scroll the table without a mouse.
 *   - `role="region"` + `aria-label` for screen-reader orientation.
 */
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { useCallback, useMemo, useRef, useState } from "react";

import type { ChatTurnTable } from "@/hooks/useChatTurn";
import { rowsToCsv } from "@/lib/csv";

/**
 * Hard cap on rendered rows (PLAN §16). The SSE preview already caps at ~200 rows
 * (pipeline table_ready), so the table is always small in v1 and renders directly
 * without virtualization. TanStack Virtual is deferred until result sets actually
 * exceed ~1k rows; the absolute-positioned-<tr> approach it requires is fragile
 * across browsers and broke alignment for small tables.
 */
const MAX_RENDERED_ROWS = 10_000;
/** Max scroll-area height in px. */
const MAX_TABLE_HEIGHT = 480;

export interface ResultTableProps {
  table: ChatTurnTable;
}

interface CellRow extends Record<string, unknown> {
  __rowIndex: number;
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") {
    // Avoid scientific notation for tiny ints; trim trailing zeros only for floats.
    if (Number.isInteger(value)) return value.toLocaleString();
    return value.toLocaleString(undefined, { maximumFractionDigits: 6 });
  }
  if (typeof value === "string") return value;
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "bigint") return value.toString();
  // Objects/arrays — fall back to JSON. The backend's json_safe.py is
  // responsible for keeping these out of result tables; this is a defensive
  // fallback only.
  return JSON.stringify(value);
}

export function ResultTable({ table }: ResultTableProps) {
  const totalRowCount = table.rowCount;
  const allRows = table.rows;
  const truncatedByCap = allRows.length > MAX_RENDERED_ROWS;
  const visibleRows = truncatedByCap ? allRows.slice(0, MAX_RENDERED_ROWS) : allRows;

  // Tag each row with its stable 1-based index for React keys + copy-CSV
  // parity with the backend's row numbering.
  const data = useMemo<CellRow[]>(
    () => visibleRows.map((r, i) => ({ ...r, __rowIndex: i })),
    [visibleRows],
  );

  const columns = useMemo<ColumnDef<CellRow>[]>(
    () =>
      table.columns.map((spec) => ({
        id: spec.name,
        accessorKey: spec.name,
        header: spec.name,
        cell: (info) => formatCell(info.getValue()),
        // Numbers sort numerically; everything else as strings.
        sortingFn: (a, b, columnId) => {
          const av = a.getValue(columnId);
          const bv = b.getValue(columnId);
          if (typeof av === "number" && typeof bv === "number") return av - bv;
          return formatCell(av).localeCompare(formatCell(bv));
        },
      })),
    [table.columns],
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

  // Virtualization is deferred (see module note). The scroll container still
  // bounds the height so large result sets scroll within the panel.
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const sortedRows = tableInstance.getRowModel().rows;

  const [copied, setCopied] = useState<boolean>(false);

  const handleCopyCsv = useCallback(async (): Promise<void> => {
    try {
      // Copy the post-sort visible rows in the order the user sees them.
      const rowsForCsv = sortedRows.map((row) => {
        const out: Record<string, unknown> = {};
        for (const c of table.columns) {
          out[c.name] = row.original[c.name];
        }
        return out;
      });
      const csv = rowsToCsv(
        table.columns.map((c) => c.name),
        rowsForCsv,
      );
      await navigator.clipboard.writeText(csv);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }, [sortedRows, table.columns]);

  if (table.columns.length === 0) {
    return (
      <p className="rounded border border-[color:var(--color-border)] bg-[color:var(--color-muted)] px-3 py-2 text-sm text-[color:var(--color-muted-foreground)]">
        No rows.
      </p>
    );
  }

  return (
    <section
      aria-label={`Result table with ${table.columns.length} column${table.columns.length === 1 ? "" : "s"}`}
      className="flex flex-col gap-2 rounded border border-[color:var(--color-border)] bg-[color:var(--color-background)]"
    >
      <header className="flex items-center justify-between gap-2 px-3 py-2 text-xs text-[color:var(--color-muted-foreground)]">
        <span>
          Showing {sortedRows.length.toLocaleString()} of {totalRowCount.toLocaleString()} row
          {totalRowCount === 1 ? "" : "s"}
          {table.truncated ? " · backend-truncated" : ""}
          {truncatedByCap ? " · UI-render cap" : ""}
        </span>
        <button
          type="button"
          onClick={() => {
            void handleCopyCsv();
          }}
          className="rounded border border-[color:var(--color-border)] bg-[color:var(--color-background)] px-2 py-0.5 text-xs font-medium hover:bg-[color:var(--color-muted)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-primary)] focus-visible:ring-offset-2 focus-visible:ring-offset-[color:var(--color-background)]"
          aria-label={copied ? "CSV copied to clipboard" : "Copy result rows as CSV"}
        >
          {copied ? "Copied" : "Copy CSV"}
        </button>
      </header>
      <div
        ref={scrollRef}
        className="relative overflow-auto focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-primary)] focus-visible:ring-offset-2 focus-visible:ring-offset-[color:var(--color-background)]"
        style={{ maxHeight: MAX_TABLE_HEIGHT }}
      >
        <table className="w-full border-collapse text-sm">
          <thead className="sticky top-0 z-10 bg-[color:var(--color-muted)]">
            {tableInstance.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((header) => {
                  const canSort = header.column.getCanSort();
                  const sortDir = header.column.getIsSorted();
                  const ariaSort: "ascending" | "descending" | "none" =
                    sortDir === "asc"
                      ? "ascending"
                      : sortDir === "desc"
                        ? "descending"
                        : "none";
                  return (
                    <th
                      key={header.id}
                      scope="col"
                      aria-sort={canSort ? ariaSort : undefined}
                      className="border-b border-[color:var(--color-border)] px-2 py-1.5 text-left font-medium"
                    >
                      {canSort ? (
                        <button
                          type="button"
                          onClick={header.column.getToggleSortingHandler()}
                          className="inline-flex items-center gap-1 rounded px-1 py-0.5 hover:bg-[color:var(--color-background)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-primary)] focus-visible:ring-offset-2 focus-visible:ring-offset-[color:var(--color-muted)]"
                          aria-label={`Sort by ${header.column.columnDef.header as string} ${ariaSort === "ascending" ? "descending" : "ascending"}`}
                        >
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          <span aria-hidden="true" className="text-xs">
                            {sortDir === "asc" ? "▲" : sortDir === "desc" ? "▼" : "↕"}
                          </span>
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
            {sortedRows.map((row) => (
              <tr key={row.id}>
                {row.getVisibleCells().map((cell) => (
                  <td
                    key={cell.id}
                    className="border-b border-[color:var(--color-border)] px-2 py-1 align-top"
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