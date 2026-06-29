import { useQuery } from "@tanstack/react-query";

import {
  getAvailableSeasons,
  getSeasonLeaders,
  getSeasonStandings,
  type LeaderStat,
} from "@/features/season-hub/api/client";
import { queryKeys } from "@/features/season-hub/api/query-keys";

export function useAvailableSeasons() {
  return useQuery({
    queryKey: queryKeys.availableSeasons,
    queryFn: getAvailableSeasons,
    staleTime: 5 * 60_000,
  });
}

export function useSeasonStandings(seasonEndYear: number | null) {
  return useQuery({
    queryKey: queryKeys.standings(seasonEndYear),
    queryFn: () => getSeasonStandings(seasonEndYear ?? 0),
    enabled: seasonEndYear !== null,
  });
}
export function useSeasonLeaders(
  seasonEndYear: number | null,
  stat: LeaderStat,
) {
  return useQuery({
    queryKey: queryKeys.leaders(seasonEndYear, stat),
    queryFn: () => getSeasonLeaders(seasonEndYear ?? 0, stat),
    enabled: seasonEndYear !== null,
  });
}
