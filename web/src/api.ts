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
  pts_player_id: number;
  pts_leader_name: string | null;
  ast: number;
  ast_player_id: number;
  ast_leader_name: string | null;
  reb: number;
  reb_player_id: number;
  reb_leader_name: string | null;
  blk: number;
  blk_player_id: number;
  blk_leader_name: string | null;
  stl: number;
  stl_player_id: number;
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

type QueryParam = string | number | null | undefined;

const qs = (params: Record<string, QueryParam>): string => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  }
  const str = search.toString();
  return str ? `?${str}` : "";
};

const playerPath = (id: string | number, suffix = ""): string => `/api/players/${id}${suffix}`;
const teamPath = (id: string | number, suffix = ""): string => `/api/teams/${id}${suffix}`;

export interface PerRates {
  per36: Row[];
  per48: Row[];
}

export interface BrowsePlayer extends Row {
  player_id: number;
  full_name: string;
  position: string | null;
  is_active: boolean;
  team_id: number | null;
  team_abbreviation: string | null;
  team_name: string | null;
}

export interface PlayerFacetTeam {
  team_id: number;
  abbreviation: string;
  name: string;
}

export interface PlayerFacets {
  totalPlayers: number;
  activePlayers: number;
  teams: PlayerFacetTeam[];
  positions: string[];
}

export interface PlayerBrowseResult {
  rows: BrowsePlayer[];
  total: number;
  facets: PlayerFacets;
}

export const api = {
  searchPlayers: (query: string, signal?: AbortSignal) =>
    getJSON<Row[]>(`/api/players${qs({ q: query })}`, signal),
  browsePlayers: (opts?: {
    q?: string;
    position?: string;
    teamId?: number | null;
    active?: boolean | null;
    letter?: string;
    sort?: "name" | "team" | "active";
    limit?: number;
    offset?: number;
  }) =>
    getJSON<PlayerBrowseResult>(
      `/api/players/browse${qs({
        q: opts?.q,
        position: opts?.position,
        team_id: opts?.teamId,
        active:
          opts?.active === undefined || opts?.active === null
            ? null
            : opts?.active
              ? "true"
              : "false",
        letter: opts?.letter,
        sort: opts?.sort,
        limit: opts?.limit,
        offset: opts?.offset,
      })}`,
    ),
  getFeaturedPlayer: () => getJSON<Row | null>("/api/players/featured"),
  getPlayer: (id: string | number) => getJSON<PlayerProfile>(playerPath(id)),
  getPlayerRates: (id: string | number) => getJSON<PerRates>(playerPath(id, "/rates")),
  getPlayerAdvanced: (id: string | number) => getJSON<Row[]>(playerPath(id, "/advanced")),
  getPlayerPer100: (id: string | number) => getJSON<Row[]>(playerPath(id, "/per100")),
  getPlayerHighs: (id: string | number) => getJSON<Row[]>(playerPath(id, "/highs")),
  getPlayerRecentGames: (id: string | number) => getJSON<Row[]>(playerPath(id, "/recent-games")),
  getPlayerForm: (id: string | number, limit?: number) =>
    getJSON<Row[]>(`${playerPath(id, "/form")}${qs({ limit })}`),
  getPlayerShotSplits: (id: string | number) => getJSON<Row[]>(playerPath(id, "/shot-splits")),
  getPlayerOnOff: (id: string | number) => getJSON<Row[]>(playerPath(id, "/on-off")),
  getPlayerCombine: (id: string | number) => getJSON<Row | null>(playerPath(id, "/combine")),
  getSimilarPlayers: (id: string | number) => getJSON<Row[]>(playerPath(id, "/similar")),

  searchTeams: (query: string, signal?: AbortSignal) =>
    getJSON<Row[]>(`/api/teams${qs({ q: query })}`, signal),
  getTeamsByConference: () => getJSON<Row[]>("/api/teams/by-conference"),
  getTeam: (id: string | number) => getJSON<TeamProfile>(teamPath(id)),
  getTeamRoster: (id: string | number) => getJSON<Row[]>(teamPath(id, "/roster")),
  getTeamPlayoffSeries: (id: string | number) => getJSON<Row[]>(teamPath(id, "/playoff-series")),
  getTeamLineups: (id: string | number) => getJSON<Row[]>(teamPath(id, "/lineups")),
  getTeamCoaches: (id: string | number) => getJSON<Row[]>(teamPath(id, "/coaches")),
  getTeamRanks: (id: string | number) => getJSON<Row[]>(teamPath(id, "/ranks")),
  getTeamOpponentStats: (id: string | number) => getJSON<Row[]>(teamPath(id, "/opponent-stats")),

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
    getJSON<SeasonLeader[]>(`/api/leaders/season${qs({ season, stat_key: statKey, limit })}`),
  getAllTimeLeaders: (stat: "pts" | "ast" | "reb" = "pts", limit?: number) =>
    getJSON<AllTimeLeader[]>(`/api/leaders/all-time${qs({ stat, limit })}`),

  getFranchiseLeaders: (teamId: string | number) =>
    getJSON<FranchiseLeaderRow | null>(teamPath(teamId, "/franchise-leaders")),
  getFranchiseTopPlayers: (teamId: string | number, stat?: string, limit?: number) =>
    getJSON<FranchiseTopPlayer[]>(`${teamPath(teamId, "/franchise-top")}${qs({ stat, limit })}`),

  getPlayerSeasonRanks: (playerId: string | number, limit?: number) =>
    getJSON<PlayerSeasonRank[]>(`${playerPath(playerId, "/season-ranks")}${qs({ limit })}`),

  listDraftValueRounds: () => getJSON<number[]>("/api/draft/value/rounds"),
  getDraftValueBoard: (opts?: { round?: number; sort?: string; limit?: number }) =>
    getJSON<DraftValueRow[]>(
      `/api/draft/value${qs({
        round: opts?.round,
        sort: opts?.sort,
        limit: opts?.limit,
      })}`,
    ),

  getPlayerLocationSplits: (playerId: string | number) =>
    getJSON<Row[]>(playerPath(playerId, "/location-splits")),
  getPlayerEstimatedMetrics: (playerId: string | number) =>
    getJSON<Row[]>(playerPath(playerId, "/estimated-metrics")),
  listPlayerShotSeasons: (playerId: string | number) =>
    getJSON<string[]>(playerPath(playerId, "/shot-chart/seasons")),
  getPlayerShotChart: (playerId: string | number, season?: string) =>
    getJSON<ShotBin[]>(`${playerPath(playerId, "/shot-chart")}${qs({ season })}`),

  getTeamHeadToHead: (teamId: string | number) => getJSON<Row[]>(teamPath(teamId, "/head-to-head")),
  getTeamSeasonContext: (teamId: string | number) =>
    getJSON<Row[]>(teamPath(teamId, "/season-context")),

  getGameDetail: (gameId: string) => getJSON<GameDetail>(`/api/games/${gameId}`),

  getAwardVoting: (season: string, award: string) =>
    getJSON<Row[]>(`/api/awards/voting${qs({ season, award })}`),

  listBettingSeasons: () => getJSON<string[]>("/api/betting/seasons"),
  getBettingMarketBeaters: (season?: string) =>
    getJSON<Row[]>(`/api/betting/market-beaters${qs({ season })}`),
  getBettingUpsets: (season?: string, limit?: number) =>
    getJSON<Row[]>(`/api/betting/upsets${qs({ season, limit })}`),
  getBettingCalibration: () => getJSON<Row[]>("/api/betting/calibration"),

  listFourFactorsSeasons: () => getJSON<string[]>("/api/four-factors/seasons"),
  getFourFactorsTeams: (season: string) =>
    getJSON<Row[]>(`/api/four-factors/teams${qs({ season })}`),
  getFourFactorsLeague: () => getJSON<Row[]>("/api/four-factors/league"),
  getGameFourFactors: (gameId: string) => getJSON<Row[]>(`/api/games/${gameId}/four-factors`),

  getPlayerMatchups: (id: string | number, side: "offense" | "defense", limit?: number) =>
    getJSON<Row[]>(
      `/api/matchups/player/${id}${qs({
        side,
        limit: limit !== undefined ? String(limit) : null,
      })}`,
    ),
  getMatchupLeaders: (sort: "toughest" | "workload", limit?: number) =>
    getJSON<Row[]>(
      `/api/matchups/leaders${qs({ sort, limit: limit !== undefined ? String(limit) : null })}`,
    ),

  getGameFlow: (gameId: string) => getJSON<Row[]>(`/api/games/${gameId}/flow`),
  listClutchSeasons: () => getJSON<string[]>("/api/clutch/seasons"),
  getClutchLeaders: (season: string, limit?: number) =>
    getJSON<Row[]>(
      `/api/clutch/leaders${qs({ season, limit: limit !== undefined ? String(limit) : null })}`,
    ),

  getOfficialsLeaders: (limit?: number) => getJSON<Row[]>(`/api/officials/leaders${qs({ limit })}`),
  getCoachingLeaders: (limit?: number) => getJSON<Row[]>(`/api/coaches/leaders${qs({ limit })}`),
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
