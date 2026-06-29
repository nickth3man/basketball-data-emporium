import { useQuery } from "@tanstack/react-query";

import {
  getCatalog,
  getFeaturedAthletes,
  getPlayerDataset,
  getSeasonDataset,
  getSummary,
  searchPlayers,
} from "@/features/player-hub/api/client";
import { queryKeys } from "@/features/player-hub/api/query-keys";

/**
 * Re-export of the shared `useStatus` hook. The canonical implementation
 * lives in `@/lib/use-status` (a feature-agnostic module that drives the
 * `<StatusPill />` in both player-hub and team-hub). Re-exported here so
 * any caller that imports the hook from the historical player-hub path
 * keeps working without churn.
 */
export { useStatus } from "@/lib/use-status";

export function useCatalog() {
  return useQuery({
    queryKey: queryKeys.catalog,
    queryFn: getCatalog,
  });
}

export function useFeaturedAthletes() {
  return useQuery({
    queryKey: queryKeys.featuredAthletes,
    queryFn: getFeaturedAthletes,
    staleTime: 5 * 60_000,
  });
}

export function usePlayerSearch(term: string) {
  return useQuery({
    queryKey: queryKeys.playerSearch(term),
    queryFn: () => searchPlayers(term),
    enabled: term.trim().length >= 2,
  });
}

export function usePlayerSummary(identifier: string) {
  return useQuery({
    queryKey: queryKeys.playerSummary(identifier),
    queryFn: () => getSummary(identifier),
  });
}

export function usePlayerDataset(identifier: string, dataset: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.playerDataset(identifier, dataset),
    queryFn: () => getPlayerDataset(identifier, dataset),
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
