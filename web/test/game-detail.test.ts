import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  api,
  type GameDetail,
  type PlayerProfile,
  type Row,
  type TeamProfile,
} from "../src/api.ts";
import { renderPlayers } from "../src/views/players.ts";
import { renderTeams } from "../src/views/teams.ts";
import { renderGame } from "../src/views/game.ts";
import { getGameDetail } from "../server/queries.ts";
import finalsFixture from "./fixtures/game-lines/finals_2024_g5_line.json";

const finalsExpected = finalsFixture.expected;
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DB_PATH = path.resolve(__dirname, "../../data/nba.duckdb");
const describeWithDb = existsSync(DB_PATH) ? describe : describe.skip;

function nextTick(): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, 0));
}

async function waitFor<T>(read: () => T | null | undefined): Promise<T> {
  for (let attempt = 0; attempt < 25; attempt += 1) {
    const value = read();
    if (value) return value;
    await nextTick();
  }
  throw new Error("Timed out waiting for DOM update.");
}

function rowText(row: HTMLTableRowElement): string[] {
  return Array.from(row.cells).map((cell) => cell.textContent ?? "");
}

function findButton(container: HTMLElement, text: string): HTMLButtonElement | null {
  return (
    Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find(
      (button) => button.textContent === text,
    ) ?? null
  );
}

function captureNavigation(): {
  events: { tab: string; id?: string }[];
  cleanup: () => void;
} {
  const events: { tab: string; id?: string }[] = [];
  const handler = (event: Event): void => {
    events.push((event as CustomEvent<{ tab: string; id?: string }>).detail);
  };
  window.addEventListener("nba:navigate", handler);
  return {
    events,
    cleanup: () => window.removeEventListener("nba:navigate", handler),
  };
}

function gameDetail(overrides: Partial<GameDetail>): GameDetail {
  return {
    header: null,
    metadata: null,
    lineScore: null,
    periodScores: [],
    teamBoxes: [],
    playerBoxes: [],
    leaders: [],
    officials: [],
    starters: [],
    lastPlays: [],
    context: [],
    coverage: {},
    ...overrides,
  };
}

function mockPlayerProfile(recentGames: Row[]): void {
  const profile: PlayerProfile = {
    bio: {
      player_id: 2544,
      full_name: "LeBron James",
      position: "F",
      height: "6-9",
      weight: 250,
    },
    career: null,
    seasons: [],
    awards: [],
    draft: null,
    hallOfFameYear: null,
    isGreatest75: false,
    allStarCount: 0,
    careerEfgPct: null,
    badges: [],
    jerseyHistory: [],
  };
  vi.spyOn(api, "getPlayer").mockResolvedValue(profile);
  vi.spyOn(api, "getPlayerHighs").mockResolvedValue([]);
  vi.spyOn(api, "getPlayerRecentGames").mockResolvedValue(recentGames);
  vi.spyOn(api, "getPlayerRates").mockResolvedValue({ per36: [], per48: [] });
  vi.spyOn(api, "getPlayerAdvanced").mockResolvedValue([]);
  vi.spyOn(api, "getPlayerPer100").mockResolvedValue([]);
  vi.spyOn(api, "getPlayerShotSplits").mockResolvedValue([]);
  vi.spyOn(api, "getPlayerOnOff").mockResolvedValue([]);
  vi.spyOn(api, "getPlayerCombine").mockResolvedValue(null);
  vi.spyOn(api, "getSimilarPlayers").mockResolvedValue([]);
  vi.spyOn(api, "getPlayerSeasonRanks").mockResolvedValue([]);
  vi.spyOn(api, "getPlayerLocationSplits").mockResolvedValue([]);
  vi.spyOn(api, "getPlayerEstimatedMetrics").mockResolvedValue([]);
  vi.spyOn(api, "getPlayerShotChart").mockResolvedValue([]);
}

function mockTeamProfile(recentGames: Row[]): void {
  const profile: TeamProfile = {
    bio: {
      team_id: "1610612738",
      nickname: "Boston Celtics",
      abbreviation: "BOS",
    },
    currentStanding: null,
    seasons: [],
    franchiseHistory: [],
    recentGames,
    franchiseTotals: null,
    franchiseAlumni: [],
  };
  vi.spyOn(api, "getTeam").mockResolvedValue(profile);
  vi.spyOn(api, "getTeamRoster").mockResolvedValue([]);
  vi.spyOn(api, "getTeamPlayoffSeries").mockResolvedValue([]);
  vi.spyOn(api, "getTeamLineups").mockResolvedValue([]);
  vi.spyOn(api, "getTeamCoaches").mockResolvedValue([]);
  vi.spyOn(api, "getTeamRanks").mockResolvedValue([]);
  vi.spyOn(api, "getTeamOpponentStats").mockResolvedValue([]);
  vi.spyOn(api, "getTeamHeadToHead").mockResolvedValue([]);
  vi.spyOn(api, "getTeamSeasonContext").mockResolvedValue([]);
}

afterEach(() => {
  vi.restoreAllMocks();
  document.body.replaceChildren();
});

describe("game detail view", () => {
  it("orders an inverted legacy line_score row by the fact_game home/away header", async () => {
    const game = gameDetail({
      header: {
        game_id: "0042300405",
        game_date: "2024-06-17",
        season_year: "2023-24",
        season_type: "Playoffs",
        game_label: "NBA Finals",
        game_sub_label: null,
        series_game_number: 5,
        home_team_id: finalsExpected.lineScore.team_id_home,
        away_team_id: finalsExpected.lineScore.team_id_away,
        home_score: finalsExpected.header.home_score,
        away_score: finalsExpected.header.away_score,
        home_abbreviation: finalsExpected.header.home_abbreviation,
        away_abbreviation: finalsExpected.header.away_abbreviation,
        home_name: "Boston Celtics",
        away_name: "Dallas Mavericks",
      },
      metadata: null,
      lineScore: {
        line_score_source: "line_score",
        team_id_home: finalsExpected.lineScore.team_id_away,
        team_id_away: finalsExpected.lineScore.team_id_home,
        team_city_name_home: "Dallas",
        team_nickname_home: "Mavericks",
        team_city_name_away: "Boston",
        team_nickname_away: "Celtics",
        pts_qtr1_home: 18,
        pts_qtr2_home: 28,
        pts_qtr3_home: 21,
        pts_qtr4_home: 21,
        pts_home: finalsExpected.header.away_score,
        pts_qtr1_away: 28,
        pts_qtr2_away: 39,
        pts_qtr3_away: 19,
        pts_qtr4_away: 20,
        pts_away: finalsExpected.header.home_score,
      },
    });
    vi.spyOn(api, "getGameDetail").mockResolvedValue(game);

    const container = document.createElement("div");
    renderGame(container, "0042300405");

    const body = await waitFor(() =>
      container
        .querySelector("#game-line-score")
        ?.closest("section")
        ?.querySelector<HTMLTableSectionElement>("tbody"),
    );
    const rows = Array.from(body.querySelectorAll<HTMLTableRowElement>("tr"));

    expect(container.querySelector("h2")?.textContent).toBe(
      "Dallas Mavericks 88 @ Boston Celtics 106",
    );
    expect(rowText(rows[0])).toEqual(["Dallas Mavericks", "18", "28", "21", "21", "88"]);
    expect(rowText(rows[1])).toEqual(["Boston Celtics", "28", "39", "19", "20", "106"]);
  });

  it("renders a historical partial box without unavailable modern stat columns", async () => {
    const game = gameDetail({
      header: {
        game_id: "0024600063",
        game_date: "1946-11-30",
        season_year: "1946-47",
        season_type: "Regular",
        home_team_id: "1610612752",
        away_team_id: "1610612744",
        home_score: 64,
        away_score: 60,
        home_abbreviation: "NYK",
        away_abbreviation: "PHW",
        home_name: "New York Knicks",
        away_name: "Philadelphia Warriors",
      },
      lineScore: {
        line_score_source: "line_score",
        team_id_home: "1610612752",
        team_id_away: "1610612744",
        team_city_name_home: "New York",
        team_nickname_home: "Knicks",
        team_city_name_away: "Philadelphia",
        team_nickname_away: "Warriors",
        pts_home: 64,
        pts_away: 60,
      },
      playerBoxes: [
        {
          player_id: "76764",
          full_name: "Joe Fulks",
          team_id: "1610612744",
          team_side: "away",
          team_name: "Philadelphia Warriors",
          fgm: 7,
          ftm: 12,
          points: 26,
          coverage_level: "scoring_only",
        },
      ],
      coverage: {
        coverage_label: "Partial historical box score",
        has_period_scores: false,
        has_team_box: false,
        has_officials: false,
        has_attendance: false,
        has_pbp: false,
      },
    });
    vi.spyOn(api, "getGameDetail").mockResolvedValue(game);

    const container = document.createElement("div");
    renderGame(container, "0024600063");

    const playerSection = await waitFor(() =>
      container.querySelector("#game-player-box")?.closest("section"),
    );
    const headings = Array.from(playerSection.querySelectorAll("th")).map(
      (heading) => heading.textContent ?? "",
    );

    expect(container.textContent).toContain("Partial historical box score");
    expect(playerSection.textContent).toContain("Joe Fulks");
    expect(headings).toContain("FGM");
    expect(headings).toContain("FTM");
    expect(headings).not.toContain("REB");
    expect(headings).not.toContain("AST");
  });
});

describeWithDb("game detail query", () => {
  it("uses modern quarter, team, and player box-score tables for 2024 Finals Game 5", async () => {
    const game = await getGameDetail("0042300405");

    expect(game.lineScore).toMatchObject(finalsExpected.lineScore);
    expect(game.coverage).toMatchObject(finalsExpected.coverage);
    expect(game.periodScores).toHaveLength(8);
    expect(game.teamBoxes.find((row) => String(row.team_id) === "1610612738")).toMatchObject({
      team_name: "Boston Celtics",
      reb: 51,
    });
    expect(game.teamBoxes.find((row) => String(row.team_id) === "1610612742")).toMatchObject({
      team_name: "Dallas Mavericks",
      reb: 35,
    });
    expect(game.playerBoxes.find((row) => row.full_name === "Jayson Tatum")).toMatchObject({
      points: 31,
      reb: 8,
      assists: 11,
      coverage_level: "modern",
    });
    expect(game.playerBoxes.find((row) => row.full_name === "Luka Doncic")).toMatchObject({
      points: 28,
      reb: 12,
      assists: 5,
      coverage_level: "modern",
    });
  });
});

describe("recent-games Box links", () => {
  it("opens the hidden game page from a player recent-games row", async () => {
    mockPlayerProfile([{ game_id: "42300405", game_date: "2024-06-17", opponent: "BOS" }]);
    const navigation = captureNavigation();
    const container = document.createElement("div");

    renderPlayers(container, "2544");
    const box = await waitFor(() => findButton(container, "Box"));
    box.click();

    expect(navigation.events).toEqual([{ tab: "game", id: "0042300405" }]);
    navigation.cleanup();
  });

  it("opens the hidden game page from a team recent-games row", async () => {
    mockTeamProfile([{ game_id: "0042300405", game_date: "2024-06-17", opponent: "DAL" }]);
    const navigation = captureNavigation();
    const container = document.createElement("div");

    renderTeams(container, "1610612738");
    const box = await waitFor(() => findButton(container, "Box"));
    box.click();

    expect(navigation.events).toEqual([{ tab: "game", id: "0042300405" }]);
    navigation.cleanup();
  });
});
