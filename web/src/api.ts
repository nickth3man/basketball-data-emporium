export type Row = Record<string, unknown>;

export interface Badge {
  season: string;
  label: string;
}

export interface JerseyStint {
  team_id: number;
  abbreviation: string;
  team_name: string;
  jersey_num: string;
  start_year: number;
  end_year: number;
  primary: string;
  trim: string;
}

export interface PlayerProfile {
  bio: Row | null;
  career: Row | null;
  seasons: Row[];
  awards: Row[];
  draft: Row | null;
  hallOfFameYear: number | null;
  isGreatest75: boolean;
  allStarCount: number;
  careerEfgPct: number | null;
  badges: Badge[];
  jerseyHistory: JerseyStint[];
}

export interface TeamProfile {
  bio: Row | null;
  currentStanding: Row | null;
  seasons: Row[];
  franchiseHistory: Row[];
  recentGames: Row[];
}

async function getJSON<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { signal });
  const body: unknown = await res.json();
  if (!res.ok) {
    const message =
      typeof body === "object" && body !== null && "error" in body && typeof body.error === "string"
        ? body.error
        : res.statusText;
    throw new Error(message);
  }
  return body as T;
}

const qs = (params: Record<string, string | null | undefined>): string => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) search.set(key, value);
  }
  const str = search.toString();
  return str ? `?${str}` : "";
};

export interface PerRates {
  per36: Row[];
  per48: Row[];
}

export const api = {
  searchPlayers: (query: string, signal?: AbortSignal) =>
    getJSON<Row[]>(`/api/players${qs({ q: query })}`, signal),
  getFeaturedPlayer: () => getJSON<Row | null>("/api/players/featured"),
  getPlayer: (id: string | number) => getJSON<PlayerProfile>(`/api/players/${id}`),
  getPlayerRates: (id: string | number) => getJSON<PerRates>(`/api/players/${id}/rates`),
  getPlayerAdvanced: (id: string | number) => getJSON<Row[]>(`/api/players/${id}/advanced`),
  getPlayerPer100: (id: string | number) => getJSON<Row[]>(`/api/players/${id}/per100`),
  getPlayerHighs: (id: string | number) => getJSON<Row[]>(`/api/players/${id}/highs`),
  getPlayerRecentGames: (id: string | number) => getJSON<Row[]>(`/api/players/${id}/recent-games`),
  getPlayerShotSplits: (id: string | number) => getJSON<Row[]>(`/api/players/${id}/shot-splits`),
  getPlayerOnOff: (id: string | number) => getJSON<Row[]>(`/api/players/${id}/on-off`),
  getPlayerCombine: (id: string | number) => getJSON<Row | null>(`/api/players/${id}/combine`),
  getSimilarPlayers: (id: string | number) => getJSON<Row[]>(`/api/players/${id}/similar`),

  searchTeams: (query: string, signal?: AbortSignal) =>
    getJSON<Row[]>(`/api/teams${qs({ q: query })}`, signal),
  getTeamsByConference: () => getJSON<Row[]>("/api/teams/by-conference"),
  getTeam: (id: string | number) => getJSON<TeamProfile>(`/api/teams/${id}`),
  getTeamRoster: (id: string | number) => getJSON<Row[]>(`/api/teams/${id}/roster`),
  getTeamPlayoffSeries: (id: string | number) => getJSON<Row[]>(`/api/teams/${id}/playoff-series`),
  getTeamLineups: (id: string | number) => getJSON<Row[]>(`/api/teams/${id}/lineups`),
  getTeamCoaches: (id: string | number) => getJSON<Row[]>(`/api/teams/${id}/coaches`),
  getTeamRanks: (id: string | number) => getJSON<Row[]>(`/api/teams/${id}/ranks`),
  getTeamOpponentStats: (id: string | number) => getJSON<Row[]>(`/api/teams/${id}/opponent-stats`),

  standingsSeasons: () => getJSON<string[]>("/api/standings/seasons"),
  standings: (season: string, type: string) =>
    getJSON<Row[]>(`/api/standings${qs({ season, type })}`),

  draftYears: () => getJSON<string[]>("/api/draft/years"),
  draft: (season: string) => getJSON<Row[]>(`/api/draft${qs({ season })}`),

  awardSeasons: () => getJSON<string[]>("/api/awards/seasons"),
  awardTypes: () => getJSON<string[]>("/api/awards/types"),
  awards: (season: string, type: string | null) =>
    getJSON<Row[]>(`/api/awards${qs({ season, type })}`),
};
