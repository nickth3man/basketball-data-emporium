"use client";

import { QueryBoundary } from "@/components/query-boundary";
import { SectionHeading } from "@/components/section-heading";
import { csvExportUrl } from "@/features/player-hub/api/client";
import { usePlayerDataset, useSeasonDataset } from "@/features/player-hub/api/queries";
import { DataTable } from "@/components/data-table";
import type { DatasetCatalogEntry } from "@/features/player-hub/types";

interface DatasetPanelProps {
  identifier: string;
  dataset: DatasetCatalogEntry;
  seasonEndYear: number | null;
  includeInactiveGames?: boolean;
}

export function DatasetPanel({
  identifier,
  dataset,
  seasonEndYear,
  includeInactiveGames = false,
}: DatasetPanelProps) {
  const playerQuery = usePlayerDataset(identifier, dataset.id, dataset.scope === "player");
  const seasonQuery = useSeasonDataset(
    identifier,
    seasonEndYear,
    dataset.id,
    dataset.scope === "season" && seasonEndYear !== null,
    includeInactiveGames,
  );
  const query = dataset.scope === "player" ? playerQuery : seasonQuery;

  return (
    <section className="space-y-3">
      <SectionHeading
        title={dataset.label}
        description={dataset.description}
        trailing={
          query.data?.row_count !== undefined ? (
            <span className="text-sm text-court-muted">{query.data.row_count.toLocaleString()} rows</span>
          ) : undefined
        }
      />
      <QueryBoundary
        query={query}
        loadingLabel="Loading"
        errorTitle="Dataset unavailable"
        emptyTitle="No rows returned"
        isEmpty={(data) => data.rows.length === 0}
      >
        {(data) => (
          <DataTable
            rows={data.rows}
            columns={data.columns}
            defaultVisibleColumns={data.default_visible_columns}
            exportUrl={csvExportUrl(
              identifier,
              dataset.id,
              dataset.scope === "season" && seasonEndYear !== null ? seasonEndYear : undefined,
              includeInactiveGames,
            )}
          />
        )}
      </QueryBoundary>
    </section>
  );
}
