import type { components } from "@/lib/openapi-types";

export type TeamDatasetScope = components["schemas"]["TeamHubTab"]["scope"];
export type ColumnMeta = components["schemas"]["ColumnMeta"];
export type TeamHubTab = components["schemas"]["TeamHubTab"];
export type StatusResponse = components["schemas"]["StatusResponse"];
export type EndpointRowsResponse = components["schemas"]["EndpointRowsResponse"];
export type FeaturedTeam = components["schemas"]["FeaturedTeam"];
export type FeaturedTeamsResponse = components["schemas"]["FeaturedTeamsResponse"];
export type FranchiseArcPoint = components["schemas"]["FranchiseArcPoint"];
export type TeamSearchResult = components["schemas"]["TeamSearchResult"];

export type TeamDatasetCatalogEntry = Omit<
  components["schemas"]["TeamDatasetCatalogEntry"],
  "columns" | "default_visible_columns"
> & {
  columns: ColumnMeta[];
  default_visible_columns: string[];
};

export type TeamHubCatalog = Omit<
  components["schemas"]["TeamHubCatalog"],
  "datasets"
> & {
  datasets: TeamDatasetCatalogEntry[];
};

export type TeamHeroStats = components["schemas"]["TeamHeroStats"] & {
  season: components["schemas"]["TeamHeroStats"]["season"] | null;
  wins: number | null;
  losses: number | null;
  win_pct: number | null;
};

export type TeamHubSummary = Omit<
  components["schemas"]["TeamHubSummary"],
  "franchise_arc" | "hero_stats"
> & {
  franchise_arc?: FranchiseArcPoint[];
  hero_stats: TeamHeroStats;
};
