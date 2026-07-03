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
  franchiseTotals: Row | null;
  franchiseAlumni: Row[];
}

export interface SeasonLeader {
  player_id: number;
  full_name: string;
  season_year: string;
  season_type: string;
  gp: number;
  stat_value: number;
  stat_rank: number;
  team_abbreviation: string | null;
}

export interface AllTimeLeader {
  stat_rank: number;
  player_id: number;
  full_name: string;
  stat_value: number;
  pts: number;
  ast: number;
  reb: number;
  gp: number;
}

export interface FranchiseLeaderRow extends Row {
  team_id: number;
  pts: number;
  pts_person_id: number;
  pts_player: string;
  pts_leader_name: string | null;
  ast: number;
  ast_person_id: number;
  ast_player: string;
  ast_leader_name: string | null;
  reb: number;
  reb_person_id: number;
  reb_player: string;
  reb_leader_name: string | null;
  blk: number;
  blk_person_id: number;
  blk_player: string;
  blk_leader_name: string | null;
  stl: number;
  stl_person_id: number;
  stl_player: string;
  stl_leader_name: string | null;
}

export interface FranchiseTopPlayer {
  player_id: number;
  source_player_name: string;
  full_name: string | null;
  gp: number;
  pts: number;
  ast: number;
  reb: number;
  stl: number;
  blk: number;
  fg_pct: number;
  fg3_pct: number;
  ft_pct: number;
}

export interface PlayerSeasonRank extends Row {
  player_id: number;
  season_id: string;
  rank_type: string;
  team_id: number | null;
  team_abbreviation: string | null;
  gp: number;
}

export interface DraftValueRow {
  player_id: number;
  source_player_name: string;
  full_name: string | null;
  season: string;
  round_number: number;
  round_pick: number;
  overall_pick: number;
  team_id: number;
  team_abbreviation: string | null;
  position: string;
  country: string;
  career_gp: number;
  career_pts: number;
  career_ppg: number;
  career_rpg: number;
  career_apg: number;
  career_fg_pct: number;
  career_fg3_pct: number;
  seasons_played: number;
  first_season: string;
  last_season: string;
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
  getPlayerForm: (id: string | number, limit?: number) =>
    getJSON<Row[]>(
      `/api/players/${id}/form${qs({ limit: limit !== undefined ? String(limit) : null })}`,
    ),
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

  listLeaderSeasons: () => getJSON<string[]>("/api/leaders/seasons"),
  listLeaderStatKeys: () => getJSON<string[]>("/api/leaders/stat-keys"),
  getSeasonLeaders: (season: string, statKey: string, limit?: number) =>
    getJSON<SeasonLeader[]>(
      `/api/leaders/season${qs({ season, stat_key: statKey, limit: limit !== undefined ? String(limit) : null })}`,
    ),
  getAllTimeLeaders: (stat: "pts" | "ast" | "reb" = "pts", limit?: number) =>
    getJSON<AllTimeLeader[]>(
      `/api/leaders/all-time${qs({ stat, limit: limit !== undefined ? String(limit) : null })}`,
    ),

  getFranchiseLeaders: (teamId: string | number) =>
    getJSON<FranchiseLeaderRow | null>(`/api/teams/${teamId}/franchise-leaders`),
  getFranchiseTopPlayers: (teamId: string | number, stat?: string, limit?: number) =>
    getJSON<FranchiseTopPlayer[]>(
      `/api/teams/${teamId}/franchise-top${qs({
        stat: stat ?? null,
        limit: limit !== undefined ? String(limit) : null,
      })}`,
    ),

  getPlayerSeasonRanks: (playerId: string | number, limit?: number) =>
    getJSON<PlayerSeasonRank[]>(
      `/api/players/${playerId}/season-ranks${qs({ limit: limit !== undefined ? String(limit) : null })}`,
    ),

  listDraftValueRounds: () => getJSON<number[]>("/api/draft/value/rounds"),
  getDraftValueBoard: (opts?: { round?: number; sort?: string; limit?: number }) =>
    getJSON<DraftValueRow[]>(
      `/api/draft/value${qs({
        round: opts?.round !== undefined ? String(opts.round) : null,
        sort: opts?.sort ?? null,
        limit: opts?.limit !== undefined ? String(opts.limit) : null,
      })}`,
    ),

  getPlayerLocationSplits: (playerId: string | number) =>
    getJSON<Row[]>(`/api/players/${playerId}/location-splits`),
  getPlayerEstimatedMetrics: (playerId: string | number) =>
    getJSON<Row[]>(`/api/players/${playerId}/estimated-metrics`),
  listPlayerShotSeasons: (playerId: string | number) =>
    getJSON<string[]>(`/api/players/${playerId}/shot-chart/seasons`),
  getPlayerShotChart: (playerId: string | number, season?: string) =>
    getJSON<ShotBin[]>(`/api/players/${playerId}/shot-chart${qs({ season: season ?? null })}`),

  getTeamHeadToHead: (teamId: string | number) =>
    getJSON<Row[]>(`/api/teams/${teamId}/head-to-head`),
  getTeamSeasonContext: (teamId: string | number) =>
    getJSON<Row[]>(`/api/teams/${teamId}/season-context`),

  getGameDetail: (gameId: string) => getJSON<GameDetail>(`/api/games/${gameId}`),

  getAwardVoting: (season: string, award: string) =>
    getJSON<Row[]>(`/api/awards/voting${qs({ season, award })}`),

  listBettingSeasons: () => getJSON<string[]>("/api/betting/seasons"),
  getBettingMarketBeaters: (season?: string) =>
    getJSON<Row[]>(`/api/betting/market-beaters${qs({ season: season ?? null })}`),
  getBettingUpsets: (season?: string, limit?: number) =>
    getJSON<Row[]>(
      `/api/betting/upsets${qs({
        season: season ?? null,
        limit: limit !== undefined ? String(limit) : null,
      })}`,
    ),
  getBettingCalibration: () => getJSON<Row[]>("/api/betting/calibration"),

  listFourFactorsSeasons: () => getJSON<string[]>("/api/four-factors/seasons"),
  getFourFactorsTeams: (season: string) =>
    getJSON<Row[]>(`/api/four-factors/teams${qs({ season })}`),
  getFourFactorsLeague: () => getJSON<Row[]>("/api/four-factors/league"),
  getGameFourFactors: (gameId: string) => getJSON<Row[]>(`/api/games/${gameId}/four-factors`),
};

export interface ShotBin {
  bin_x: number;
  bin_y: number;
  attempts: number | string;
  makes: number | string;
  league_fg_pct: number | null;
}

export interface GameDetail {
  header: Row | null;
  metadata: Row | null;
  lineScore: Row | null;
  periodScores: Row[];
  teamBoxes: Row[];
  playerBoxes: Row[];
  leaders: Row[];
  officials: Row[];
  starters: Row[];
  lastPlays: Row[];
  context: Row[];
  coverage: Row;
}
