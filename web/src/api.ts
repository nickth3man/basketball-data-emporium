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

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url);
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

export const api = {
  searchPlayers: (query: string) => getJSON<Row[]>(`/api/players${qs({ q: query })}`),
  getPlayer: (id: string | number) => getJSON<PlayerProfile>(`/api/players/${id}`),

  searchTeams: (query: string) => getJSON<Row[]>(`/api/teams${qs({ q: query })}`),
  getTeam: (id: string | number) => getJSON<TeamProfile>(`/api/teams/${id}`),

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
