/**
 * Thin compatibility shim for `@/features/player-hub/api/client`.
 *
 * The HTTP plumbing lives in `@/lib/api-client` (a hardened, retry-aware
 * `apiFetch` plus the typed `TypedApiError` surface). This module preserves
 * the original 8 named exports so existing imports in
 * `features/player-hub/api/queries.ts` and
 * `features/player-hub/components/dataset-panel.tsx` keep working.
 */
import { apiFetch, API_BASE_URL, csvExportUrl } from "@/lib/api-client";
import type {
  EndpointRowsResponse,
  FeaturedAthletesResponse,
  PlayerHubCatalog,
  PlayerHubSummary,
  PlayerSearchResult,
  StatusResponse,
} from "@/features/player-hub/types";

export { API_BASE_URL, csvExportUrl };

export const getStatus = (): Promise<StatusResponse> => apiFetch<StatusResponse>("/api/status");

export const getCatalog = (): Promise<PlayerHubCatalog> =>
  apiFetch<PlayerHubCatalog>("/api/endpoints/player-hub");

export const getFeaturedAthletes = (): Promise<FeaturedAthletesResponse> =>
  apiFetch<FeaturedAthletesResponse>("/api/players/featured");

export const searchPlayers = (term: string): Promise<PlayerSearchResult[]> =>
  apiFetch<PlayerSearchResult[]>(`/api/players/search?term=${encodeURIComponent(term)}`);

export const getSummary = (identifier: string): Promise<PlayerHubSummary> =>
  apiFetch<PlayerHubSummary>(`/api/players/${identifier}/summary`);

export const getPlayerDataset = (identifier: string, dataset: string): Promise<EndpointRowsResponse> =>
  apiFetch<EndpointRowsResponse>(`/api/players/${identifier}/${dataset}`);

export const getSeasonDataset = (
  identifier: string,
  seasonEndYear: number,
  dataset: string,
  includeInactiveGames = false,
): Promise<EndpointRowsResponse> => {
  const params = new URLSearchParams();
  if (includeInactiveGames) {
    params.set("include_inactive_games", "true");
  }
  const suffix = params.size > 0 ? `?${params.toString()}` : "";
  return apiFetch<EndpointRowsResponse>(
    `/api/players/${identifier}/seasons/${seasonEndYear}/${dataset}${suffix}`,
  );
};
