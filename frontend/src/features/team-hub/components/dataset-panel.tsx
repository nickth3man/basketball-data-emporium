"use client";

import { QueryBoundary } from "@/components/query-boundary";
import { SectionHeading } from "@/components/section-heading";
import { csvExportUrl } from "@/features/team-hub/api/client";
import { useSeasonDataset, useTeamDataset } from "@/features/team-hub/api/queries";
import { DataTable } from "@/components/data-table";
import type { TeamDatasetCatalogEntry } from "@/features/team-hub/types";
import { downloadCsv } from "@/lib/api-client";

interface DatasetPanelProps {
  identifier: string;
  dataset: TeamDatasetCatalogEntry;
  seasonEndYear: number | null;
  includeInactiveGames?: boolean;
}

export function DatasetPanel({
  identifier,
  dataset,
  seasonEndYear,
  includeInactiveGames = false,
}: DatasetPanelProps) {
  const teamQuery = useTeamDataset(identifier, dataset.id, dataset.scope === "team");
  const seasonQuery = useSeasonDataset(
    identifier,
    seasonEndYear,
    dataset.id,
    dataset.scope === "team_season" && seasonEndYear !== null,
    includeInactiveGames,
  );
  const query = dataset.scope === "team" ? teamQuery : seasonQuery;

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
            onExportCsv={() =>
              downloadCsv(
                csvExportUrl(
                  identifier,
                  dataset.id,
                  dataset.scope === "team_season" && seasonEndYear !== null ? seasonEndYear : undefined,
                  includeInactiveGames,
                ),
                `${identifier}-${dataset.id}.csv`,
              )
            }
          />
        )}
      </QueryBoundary>
    </section>
  );
}
