"use client";

import { ArrowLeft, RefreshCcw } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";
import { useMemo, useState } from "react";

import { Button } from "@/components/button";
import { EmptyState } from "@/components/empty-state";
import { LoadingBlock } from "@/components/loading-block";
import { QueryBoundary } from "@/components/query-boundary";
import { StatusPill } from "@/components/status-pill";
import { useCatalog, useTeamSummary } from "@/features/team-hub/api/queries";
import { DatasetPanel } from "@/features/team-hub/components/dataset-panel";
import { Overview } from "@/features/team-hub/components/overview";
import { TeamSearch } from "@/features/team-hub/components/team-search";
import type { TeamHubTab } from "@/features/team-hub/types";
import { seasonLabel } from "@/features/player-hub/utils/season";
import { useUrlParam } from "@/lib/use-url-param";

interface TeamHubProps {
  identifier: string;
}

export function TeamHub({ identifier }: TeamHubProps) {
  const summaryQuery = useTeamSummary(identifier);
  const catalogQuery = useCatalog();
  const { get: getParam, set: setParam } = useUrlParam();
  const [includeInactiveGames, setIncludeInactiveGames] = useState(false);

  const tabs = catalogQuery.data?.tabs ?? [];
  const datasetById = useMemo(() => {
    const entries = catalogQuery.data?.datasets ?? [];
    return new Map(entries.map((entry) => [entry.id, entry]));
  }, [catalogQuery.data?.datasets]);

  const activeTab = normalizeTab(getParam("tab"), tabs);
  const seasonFromUrl = Number(getParam("season"));
  const selectedSeason =
    Number.isFinite(seasonFromUrl) && seasonFromUrl > 0
      ? seasonFromUrl
      : (summaryQuery.data?.default_season ?? null);
  const currentTab = tabs.find((tab) => tab.id === activeTab) ?? tabs[0];

  return (
    <Shell>
      <QueryBoundary
        query={summaryQuery}
        loadingLabel="Loading team"
        errorTitle="Team unavailable"
      >
        {(summary) => (
          <>
            <header className="space-y-4 border-b border-court-line bg-white px-4 py-4 sm:px-6 lg:px-8">
              <div className="mx-auto flex max-w-7xl flex-col gap-4">
                <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                  <div className="min-w-0">
                    <Link
                      href="/teams"
                      className="inline-flex items-center gap-2 text-sm text-court-muted hover:text-court-ink"
                    >
                      <ArrowLeft className="size-4" aria-hidden="true" />
                      Teams
                    </Link>
                    <h1 className="mt-2 truncate text-2xl font-semibold text-court-ink sm:text-3xl">
                      {summary.display_name}
                    </h1>
                    <p className="text-sm text-court-muted">
                      {[summary.identifier, summary.leagues.join("/") || null]
                        .filter(Boolean)
                        .join(" · ")}
                    </p>
                  </div>
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
                    <TeamSearch compact />
                    <StatusPill />
                  </div>
                </div>

                <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                  <nav
                    className="flex gap-1 overflow-x-auto pb-1"
                    aria-label="Team Hub tabs"
                  >
                    {tabs.map((tab) => (
                      <button
                        key={tab.id}
                        type="button"
                        onClick={() => setParam("tab", tab.id)}
                        data-active={tab.id === activeTab ? "" : undefined}
                        className="h-9 shrink-0 rounded-md px-3 text-sm font-medium text-court-muted transition hover:bg-zinc-100 hover:text-court-ink data-active:bg-court-accent data-active:text-white"
                      >
                        {tab.label}
                      </button>
                    ))}
                  </nav>

                  <div className="flex flex-wrap items-center gap-2">
                    <label className="flex items-center gap-2 text-sm text-court-muted">
                      Season
                      <select
                        value={selectedSeason ?? ""}
                        onChange={(event) =>
                          setParam("season", event.target.value)
                        }
                        className="h-9 rounded-md border border-court-line bg-white px-2 text-sm text-court-ink outline-none focus:border-court-accent focus:ring-2 focus:ring-teal-100"
                      >
                        {summary.available_seasons.map((season) => (
                          <option key={season} value={season}>
                            {seasonLabel(season)}
                          </option>
                        ))}
                      </select>
                    </label>
                    {/* TODO P2-BE-03: hide or disable this control per dataset
                        once catalog metadata declares whether
                        `include_inactive_games` is supported. Current team
                        roster responses accept the query param but do not use
                        it. */}
                    <label className="inline-flex h-9 items-center gap-2 rounded-md border border-court-line bg-white px-3 text-sm text-court-muted">
                      <input
                        type="checkbox"
                        checked={includeInactiveGames}
                        onChange={(event) =>
                          setIncludeInactiveGames(event.target.checked)
                        }
                      />
                      Inactive
                    </label>
                    <Button
                      size="icon"
                      onClick={() => summaryQuery.refetch()}
                      title="Refresh team"
                    >
                      <RefreshCcw className="size-4" aria-hidden="true" />
                      <span className="sr-only">Refresh team</span>
                    </Button>
                  </div>
                </div>
              </div>
            </header>

            <main className="mx-auto max-w-7xl px-4 py-5 sm:px-6 lg:px-8">
              {catalogQuery.isLoading ? (
                <LoadingBlock label="Loading catalog" />
              ) : catalogQuery.isError ? (
                <EmptyState
                  title="Team catalog unavailable"
                  detail={catalogQuery.error.message}
                />
              ) : currentTab === undefined ? (
                <EmptyState
                  title="Team catalog unavailable"
                  detail="No team hub tabs were returned by the API."
                />
              ) : currentTab.id === "overview" ? (
                <Overview summary={summary} />
              ) : (
                <div className="space-y-6">
                  {currentTab.datasets.map((datasetId) => {
                    const dataset = datasetById.get(datasetId);
                    if (dataset === undefined) {
                      return null;
                    }
                    return (
                      <DatasetPanel
                        key={datasetId}
                        identifier={identifier}
                        dataset={dataset}
                        seasonEndYear={selectedSeason}
                        includeInactiveGames={includeInactiveGames}
                      />
                    );
                  })}
                </div>
              )}
            </main>
          </>
        )}
      </QueryBoundary>
    </Shell>
  );
}

function Shell({ children }: Readonly<{ children: ReactNode }>) {
  return <div className="min-h-screen bg-court-paper">{children}</div>;
}

function normalizeTab(value: string | null, tabs: TeamHubTab[]): string {
  if (value && tabs.some((tab) => tab.id === value)) {
    return value;
  }
  return tabs[0]?.id ?? "overview";
}
