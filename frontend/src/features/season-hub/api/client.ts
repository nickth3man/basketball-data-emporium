import { apiFetch } from "@/lib/api-client";
import type {
  AvailableSeasonsResponse,
  EndpointRowsResponse,
} from "@/features/season-hub/types";

export type LeaderStat = "pts" | "reb" | "ast" | "stl" | "blk";

export const getAvailableSeasons = (): Promise<AvailableSeasonsResponse> =>
  apiFetch<AvailableSeasonsResponse>("/api/seasons");

export const getSeasonStandings = (
  seasonEndYear: number,
): Promise<EndpointRowsResponse> =>
  apiFetch<EndpointRowsResponse>(`/api/seasons/${seasonEndYear}/standings`);

export const getSeasonLeaders = (
  seasonEndYear: number,
  stat: LeaderStat,
): Promise<EndpointRowsResponse> => {
  const params = new URLSearchParams({ stat });
  return apiFetch<EndpointRowsResponse>(
    `/api/seasons/${seasonEndYear}/leaders?${params.toString()}`,
  );
};
