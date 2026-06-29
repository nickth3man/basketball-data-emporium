import type { components } from "@/lib/openapi-types";

export type DatasetScope = components["schemas"]["PlayerHubTab"]["scope"];
export type ColumnMeta = components["schemas"]["ColumnMeta"];
export type PlayerSearchResult = components["schemas"]["PlayerSearchResult"];
export type FeaturedAthlete = components["schemas"]["FeaturedAthlete"];
export type FeaturedAthletesResponse = components["schemas"]["FeaturedAthletesResponse"];
export type PlayerHubTab = components["schemas"]["PlayerHubTab"];
export type EndpointRowsResponse = components["schemas"]["EndpointRowsResponse"];
export type StatusResponse = components["schemas"]["StatusResponse"];

export type DatasetCatalogEntry = Omit<
  components["schemas"]["DatasetCatalogEntry"],
  "default_visible_columns"
> & {
  default_visible_columns: string[];
};

export type PlayerHubCatalog = Omit<
  components["schemas"]["PlayerHubCatalog"],
  "datasets"
> & {
  datasets: DatasetCatalogEntry[];
};

export type PlayerHubSummary = components["schemas"]["PlayerHubSummary"];
