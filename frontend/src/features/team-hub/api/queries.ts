import { useQuery } from "@tanstack/react-query";

import {
  getCatalog,
  getFeaturedTeams,
  getSeasonDataset,
  getSummary,
  getTeamDataset,
  searchTeams,
} from "@/features/team-hub/api/client";
import { queryKeys } from "@/features/team-hub/api/query-keys";

/**
 * Re-export of the shared `useStatus` hook. The canonical implementation
 * lives in `@/lib/use-status` (a feature-agnostic module that drives the
 * `<StatusPill />` in both player-hub and team-hub). Re-exported here so
 * any caller that imports the hook from the historical team-hub path
 * keeps working without churn.
 */
export { useStatus } from "@/lib/use-status";

export function useCatalog() {
  return useQuery({
    queryKey: queryKeys.catalog,
    queryFn: getCatalog,
  });
}

export function useFeaturedTeams() {
  return useQuery({
    queryKey: queryKeys.featuredTeams,
    queryFn: getFeaturedTeams,
    staleTime: 5 * 60_000,
  });
}

export function useTeamSearch(term: string) {
  return useQuery({
    queryKey: queryKeys.teamSearch(term),
    queryFn: () => searchTeams(term),
    enabled: term.trim().length >= 2,
  });
}

export function useTeamSummary(identifier: string) {
  return useQuery({
    queryKey: queryKeys.teamSummary(identifier),
    queryFn: () => getSummary(identifier),
  });
}

export function useTeamDataset(identifier: string, dataset: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.teamDataset(identifier, dataset),
    queryFn: () => getTeamDataset(identifier, dataset),
    enabled,
  });
}

export function useSeasonDataset(
  identifier: string,
  seasonEndYear: number | null,
  dataset: string,
  enabled = true,
  includeInactiveGames = false,
) {
  return useQuery({
    queryKey: queryKeys.seasonDataset(identifier, seasonEndYear, dataset, includeInactiveGames),
    queryFn: () => getSeasonDataset(identifier, seasonEndYear ?? 0, dataset, includeInactiveGames),
    enabled: enabled && seasonEndYear !== null,
  });
}
