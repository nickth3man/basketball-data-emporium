export interface QueryKeys {
  readonly status: readonly ["status"];
  readonly catalog: readonly ["team-hub-catalog"];
  readonly featuredTeams: readonly ["featured-teams"];
  readonly teamSearch: (term: string) => readonly ["team-search", string];
  readonly teamSummary: (identifier: string) => readonly ["team-summary", string];
  readonly teamDataset: (identifier: string, dataset: string) => readonly [
    "team-dataset",
    string,
    string,
  ];
  readonly seasonDataset: (
    identifier: string,
    seasonEndYear: number | null,
    dataset: string,
    includeInactiveGames: boolean,
  ) => readonly [
    "team-season-dataset",
    string,
    number | null,
    string,
    boolean,
  ];
}

export const queryKeys: QueryKeys = Object.freeze({
  status: ["status"] as const,
  catalog: ["team-hub-catalog"] as const,
  featuredTeams: ["featured-teams"] as const,
  teamSearch: (term: string) => ["team-search", term] as const,
  teamSummary: (identifier: string) => ["team-summary", identifier] as const,
  teamDataset: (identifier: string, dataset: string) =>
    ["team-dataset", identifier, dataset] as const,
  seasonDataset: (
    identifier: string,
    seasonEndYear: number | null,
    dataset: string,
    includeInactiveGames: boolean,
  ) =>
    [
      "team-season-dataset",
      identifier,
      seasonEndYear,
      dataset,
      includeInactiveGames,
    ] as const,
});
