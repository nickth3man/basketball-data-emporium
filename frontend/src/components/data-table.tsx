"use client";

import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  type SortingState,
  useReactTable,
  type VisibilityState,
} from "@tanstack/react-table";
import { ChevronDown, ChevronLeft, ChevronRight, Download, SlidersHorizontal } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/button";
import type { ColumnMeta } from "@/lib/column-meta";
import { formatValue } from "@/lib/format";

interface DataTableProps {
  rows: Record<string, unknown>[];
  columns: ColumnMeta[];
  defaultVisibleColumns: string[];
  onExportCsv?: () => Promise<void>;
}

export function DataTable({ rows, columns, defaultVisibleColumns, onExportCsv }: DataTableProps) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [globalFilter, setGlobalFilter] = useState("");
  const [showColumns, setShowColumns] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [isExporting, setIsExporting] = useState(false);
  const defaultVisible = useMemo(() => new Set(defaultVisibleColumns), [defaultVisibleColumns]);
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(() =>
    Object.fromEntries(columns.map((column) => [column.key, defaultVisible.has(column.key)])),
  );

  const columnDefs = useMemo<ColumnDef<Record<string, unknown>>[]>(
    () =>
      columns.map((column) => ({
        accessorKey: column.key,
        header: column.label,
        cell: ({ getValue }) => (
          <span className={column.numeric ? "tabular-nums" : undefined}>{formatValue(getValue())}</span>
        ),
      })),
    [columns],
  );

  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data: rows,
    columns: columnDefs,
    state: { sorting, globalFilter, columnVisibility },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: {
      pagination: { pageSize: 20 },
    },
  });

  const handleExport = async () => {
    if (!onExportCsv || isExporting) {
      return;
    }
    setExportError(null);
    setIsExporting(true);
    try {
      await onExportCsv();
    } catch (error) {
      setExportError(error instanceof Error ? error.message : "CSV export failed");
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div className="rounded-md border border-court-line bg-white">
      <div className="flex flex-col gap-2 border-b border-court-line p-3 sm:flex-row sm:items-center sm:justify-between">
        <label className="min-w-0 flex-1 sm:max-w-sm">
          <span className="sr-only">Filter rows</span>
          <input
            value={globalFilter}
            onChange={(event) => setGlobalFilter(event.target.value)}
            placeholder="Filter rows"
            className="h-9 w-full rounded-md border border-court-line px-3 text-sm outline-none focus:border-court-accent focus:ring-2 focus:ring-teal-100"
          />
        </label>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Button size="sm" onClick={() => setShowColumns((value) => !value)} aria-expanded={showColumns}>
              <SlidersHorizontal className="size-4" aria-hidden="true" />
              Columns
            </Button>
            {showColumns ? (
              <div className="absolute right-0 z-20 mt-2 max-h-80 w-64 overflow-auto rounded-md border border-court-line bg-white p-2 text-sm shadow-lg">
                {table.getAllLeafColumns().map((column) => (
                  <label key={column.id} className="flex items-center gap-2 rounded px-2 py-1 hover:bg-zinc-50">
                    <input
                      type="checkbox"
                      checked={column.getIsVisible()}
                      onChange={column.getToggleVisibilityHandler()}
                    />
                    <span>{columns.find((entry) => entry.key === column.id)?.label ?? column.id}</span>
                  </label>
                ))}
              </div>
            ) : null}
          </div>
          {onExportCsv ? (
            <Button size="sm" onClick={handleExport} disabled={isExporting}>
              <Download className="size-4" aria-hidden="true" />
              {isExporting ? "Exporting" : "CSV"}
            </Button>
          ) : null}
        </div>
      </div>
      {exportError ? (
        <p className="border-b border-court-danger-line bg-court-danger-soft px-3 py-2 text-sm text-court-danger">
          {exportError}
        </p>
      ) : null}

      <div className="overflow-x-auto" data-testid="data-table-scroll">
        <table className="min-w-full table-fixed border-collapse text-sm">
          <thead className="bg-zinc-100 text-xs uppercase text-court-muted">
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <th
                    key={header.id}
                    className="w-36 border-b border-court-line px-3 py-2 text-left font-semibold"
                  >
                    {header.isPlaceholder ? null : (
                      <button
                        type="button"
                        onClick={header.column.getToggleSortingHandler()}
                        className="flex w-full items-center justify-between gap-1 text-left"
                      >
                        <span className="truncate">
                          {flexRender(header.column.columnDef.header, header.getContext())}
                        </span>
                        <ChevronDown
                          className={[
                            "size-3 shrink-0 transition",
                            header.column.getIsSorted() === "desc" ? "rotate-180" : "",
                            header.column.getIsSorted() ? "opacity-100" : "opacity-25",
                          ].join(" ")}
                          aria-hidden="true"
                        />
                      </button>
                    )}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr key={row.id} className="odd:bg-white even:bg-zinc-50/70">
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="w-36 border-b border-zinc-100 px-3 py-2 align-top text-court-ink">
                    <div className="truncate" title={formatValue(cell.getValue())}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </div>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex flex-col gap-2 px-3 py-3 text-sm text-court-muted sm:flex-row sm:items-center sm:justify-between">
        <span>
          {table.getFilteredRowModel().rows.length.toLocaleString()} rows · page{" "}
          {table.getState().pagination.pageIndex + 1} of {table.getPageCount() || 1}
        </span>
        <div className="flex items-center gap-2">
          <Button size="icon" onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()}>
            <ChevronLeft className="size-4" aria-hidden="true" />
            <span className="sr-only">Previous page</span>
          </Button>
          <Button size="icon" onClick={() => table.nextPage()} disabled={!table.getCanNextPage()}>
            <ChevronRight className="size-4" aria-hidden="true" />
            <span className="sr-only">Next page</span>
          </Button>
        </div>
      </div>
    </div>
  );
}
