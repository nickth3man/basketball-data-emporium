export interface QueryKeys {
  readonly availableSeasons: readonly ["available-seasons"];
  readonly standings: (seasonEndYear: number | null) => readonly [
    "season-standings",
    number | null,
  ];
  readonly leaders: (
    seasonEndYear: number | null,
    stat: string,
  ) => readonly ["season-leaders", number | null, string];
}

export const queryKeys: QueryKeys = Object.freeze({
  availableSeasons: ["available-seasons"] as const,
  standings: (seasonEndYear: number | null) =>
    ["season-standings", seasonEndYear] as const,
  leaders: (seasonEndYear: number | null, stat: string) =>
    ["season-leaders", seasonEndYear, stat] as const,
});
