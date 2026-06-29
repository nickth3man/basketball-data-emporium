export interface QueryKeys {
  readonly status: readonly ["status"];
  readonly catalog: readonly ["player-hub-catalog"];
  readonly featuredAthletes: readonly ["featured-athletes"];
  readonly playerSearch: (term: string) => readonly ["player-search", string];
  readonly playerSummary: (identifier: string) => readonly ["player-summary", string];
  readonly playerDataset: (identifier: string, dataset: string) => readonly [
    "player-dataset",
    string,
    string,
  ];
  readonly seasonDataset: (
    identifier: string,
    seasonEndYear: number | null,
    dataset: string,
    includeInactiveGames: boolean,
  ) => readonly [
    "season-dataset",
    string,
    number | null,
    string,
    boolean,
  ];
}

export const queryKeys: QueryKeys = Object.freeze({
  status: ["status"] as const,
  catalog: ["player-hub-catalog"] as const,
  featuredAthletes: ["featured-athletes"] as const,
  playerSearch: (term: string) => ["player-search", term] as const,
  playerSummary: (identifier: string) => ["player-summary", identifier] as const,
  playerDataset: (identifier: string, dataset: string) =>
    ["player-dataset", identifier, dataset] as const,
  seasonDataset: (
    identifier: string,
    seasonEndYear: number | null,
    dataset: string,
    includeInactiveGames: boolean,
  ) =>
    [
      "season-dataset",
      identifier,
      seasonEndYear,
      dataset,
      includeInactiveGames,
    ] as const,
});
