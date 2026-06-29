/**
 * Thin compatibility shim for `@/features/team-hub/api/client`.
 *
 * The HTTP plumbing lives in `@/lib/api-client` so player and team hub calls
 * share timeout, retry, and typed-error behavior.
 */
import {
  apiFetch,
  API_BASE_URL,
  buildDatasetParams,
  teamCsvExportUrl,
} from "@/lib/api-client";
import type {
  EndpointRowsResponse,
  FeaturedTeamsResponse,
  StatusResponse,
  TeamHubCatalog,
  TeamHubSummary,
  TeamSearchResult,
} from "@/features/team-hub/types";

export { API_BASE_URL };
export { teamCsvExportUrl as csvExportUrl };

export const getStatus = (): Promise<StatusResponse> =>
  apiFetch<StatusResponse>("/api/status");

export const getCatalog = (): Promise<TeamHubCatalog> =>
  apiFetch<TeamHubCatalog>("/api/endpoints/team-hub");

export const getFeaturedTeams = (): Promise<FeaturedTeamsResponse> =>
  apiFetch<FeaturedTeamsResponse>("/api/teams/featured");

export const searchTeams = (term: string): Promise<TeamSearchResult[]> =>
  apiFetch<TeamSearchResult[]>(
    `/api/teams/search?term=${encodeURIComponent(term)}`,
  );

export const getSummary = (identifier: string): Promise<TeamHubSummary> =>
  apiFetch<TeamHubSummary>(`/api/teams/${identifier}/summary`);

export const getTeamDataset = (
  identifier: string,
  dataset: string,
): Promise<EndpointRowsResponse> =>
  apiFetch<EndpointRowsResponse>(`/api/teams/${identifier}/${dataset}`);

export const getSeasonDataset = (
  identifier: string,
  seasonEndYear: number,
  dataset: string,
  includeInactiveGames = false,
): Promise<EndpointRowsResponse> => {
  const params = buildDatasetParams({ includeInactiveGames });
  const suffix = params.size > 0 ? `?${params.toString()}` : "";
  return apiFetch<EndpointRowsResponse>(
    `/api/teams/${identifier}/seasons/${seasonEndYear}/${dataset}${suffix}`,
  );
};
