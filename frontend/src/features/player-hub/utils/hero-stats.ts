export interface PlayerHeroStatsView {
  season: string | number | null;
  team: string | null;
  gp: number | null;
  pts: number | null;
  reb: number | null;
  ast: number | null;
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

export function parsePlayerHeroStats(value: unknown): PlayerHeroStatsView {
  const row = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  const season = row.season;
  return {
    season: typeof season === "string" || typeof season === "number" ? season : null,
    team: stringOrNull(row.team),
    gp: numberOrNull(row.gp),
    pts: numberOrNull(row.pts),
    reb: numberOrNull(row.reb),
    ast: numberOrNull(row.ast),
  };
}
