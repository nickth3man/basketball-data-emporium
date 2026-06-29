"use client";

import { Trophy } from "lucide-react";
import type { ReactNode } from "react";

import { DataTable } from "@/components/data-table";
import { EmptyState } from "@/components/empty-state";
import { LoadingBlock } from "@/components/loading-block";
import { QueryBoundary } from "@/components/query-boundary";
import { StatusPill } from "@/components/status-pill";
import {
  useAvailableSeasons,
  useSeasonLeaders,
  useSeasonStandings,
} from "@/features/season-hub/api/queries";
import type { LeaderStat } from "@/features/season-hub/api/client";
import { seasonLabel } from "@/features/player-hub/utils/season";
import { useUrlParam } from "@/lib/use-url-param";

const LEADER_STATS: readonly { id: LeaderStat; label: string }[] = [
  { id: "pts", label: "PTS" },
  { id: "reb", label: "REB" },
  { id: "ast", label: "AST" },
  { id: "stl", label: "STL" },
  { id: "blk", label: "BLK" },
];

export function SeasonHub() {
  const seasonsQuery = useAvailableSeasons();
  const { get: getParam, set: setParam } = useUrlParam();

  const seasonFromUrl = Number(getParam("season"));
  const selectedSeason =
    Number.isFinite(seasonFromUrl) && seasonFromUrl > 0
      ? seasonFromUrl
      : (seasonsQuery.data?.default_season ?? null);
  const selectedStat = normalizeStat(getParam("stat"));

  const standingsQuery = useSeasonStandings(selectedSeason);
  const leadersQuery = useSeasonLeaders(selectedSeason, selectedStat);

  return (
    <main className="min-h-screen bg-court-paper">
      <header className="border-b border-court-line bg-white">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="min-w-0">
              <p className="text-xs font-semibold uppercase tracking-wide text-court-accent">
                Basketball Data Emporium
              </p>
              <h1 className="mt-1 text-xl font-semibold text-court-ink">
                Season Hub
              </h1>
            </div>
            <StatusPill />
          </div>

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <label className="flex items-center gap-2 text-sm text-court-muted">
              Season
              <select
                value={selectedSeason ?? ""}
                onChange={(event) => setParam("season", event.target.value)}
                disabled={seasonsQuery.isLoading || seasonsQuery.isError}
                className="h-9 rounded-md border border-court-line bg-white px-2 text-sm text-court-ink outline-none focus:border-court-accent focus:ring-2 focus:ring-teal-100"
              >
                {(seasonsQuery.data?.seasons ?? []).map((season) => (
                  <option key={season} value={season}>
                    {seasonLabel(season)}
                  </option>
                ))}
              </select>
            </label>

            <div className="flex gap-1 overflow-x-auto" aria-label="Leader stat">
              {LEADER_STATS.map((stat) => (
                <button
                  key={stat.id}
                  type="button"
                  data-active={stat.id === selectedStat ? "" : undefined}
                  onClick={() => setParam("stat", stat.id)}
                  className="h-9 shrink-0 rounded-md px-3 text-sm font-medium text-court-muted transition hover:bg-zinc-100 hover:text-court-ink data-active:bg-court-accent data-active:text-white"
                >
                  {stat.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </header>

      {seasonsQuery.isError ? (
        <div className="mx-auto max-w-7xl px-4 py-5 sm:px-6 lg:px-8">
          <EmptyState
            title="Seasons unavailable"
            detail={seasonsQuery.error.message}
          />
        </div>
      ) : (
        <div className="mx-auto grid max-w-7xl gap-6 px-4 py-5 sm:px-6 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)] xl:px-8">
          <section className="space-y-3">
            <SectionTitle icon={<Trophy className="size-4" aria-hidden="true" />}>
              Standings
            </SectionTitle>
            {selectedSeason === null ? (
              <LoadingBlock label="Loading seasons" />
            ) : (
              <QueryBoundary
                query={standingsQuery}
                loadingLabel="Loading standings"
                errorTitle="Standings unavailable"
                emptyTitle="No standings"
                isEmpty={(data) => data.row_count === 0}
              >
                {(standings) => (
                  <DataTable
                    key={`standings-${selectedSeason}`}
                    rows={standings.rows}
                    columns={standings.columns}
                    defaultVisibleColumns={standings.default_visible_columns}
                  />
                )}
              </QueryBoundary>
            )}
          </section>

          <section className="space-y-3">
            <SectionTitle>Leaders</SectionTitle>
            {selectedSeason === null ? (
              <EmptyState title="No season selected" />
            ) : (
              <QueryBoundary
                query={leadersQuery}
                loadingLabel="Loading leaders"
                errorTitle="Leaders unavailable"
                emptyTitle="No leaders"
                isEmpty={(data) => data.row_count === 0}
              >
                {(leaders) => (
                  <DataTable
                    key={`leaders-${selectedSeason}-${selectedStat}`}
                    rows={leaders.rows}
                    columns={leaders.columns}
                    defaultVisibleColumns={leaders.default_visible_columns}
                  />
                )}
              </QueryBoundary>
            )}
          </section>
        </div>
      )}
    </main>
  );
}

function SectionTitle({
  children,
  icon,
}: Readonly<{ children: string; icon?: ReactNode }>) {
  return (
    <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-court-muted">
      {icon}
      {children}
    </h2>
  );
}

function normalizeStat(value: string | null): LeaderStat {
  if (LEADER_STATS.some((stat) => stat.id === value)) {
    return value as LeaderStat;
  }
  return "pts";
}
