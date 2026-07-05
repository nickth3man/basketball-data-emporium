import express, { type Request, type Response } from "express";
import { listTables, resolveTable, readTablePage, runReadOnlyQuery } from "./db.ts";
import * as q from "./queries.ts";
import { getPlayerPhoto } from "./photos.ts";

const app = express();
app.use(express.json());

const MAX_LIMIT = 500;
const DEFAULT_LIMIT = 50;

function clampLimit(raw: unknown): number {
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_LIMIT;
  return Math.min(Math.trunc(n), MAX_LIMIT);
}

function clampOffset(raw: unknown): number {
  const n = Number(raw);
  if (!Number.isFinite(n) || n < 0) return 0;
  return Math.trunc(n);
}

/** Wraps an async route handler so thrown errors become a JSON 500 instead
 *  of crashing the process (Express 5 does this automatically for rejected
 *  promises, but an explicit wrapper keeps the error payload consistent). */
function asyncRoute(handler: (req: Request, res: Response) => Promise<void>) {
  return async (req: Request, res: Response) => {
    try {
      await handler(req, res);
    } catch (err) {
      res.status(500).json({ error: String(err) });
    }
  };
}

function requireQueryString(req: Request, res: Response, name: string): string | null {
  const value = req.query[name];
  if (typeof value !== "string" || value.trim() === "") {
    res.status(400).json({ error: `Missing '${name}' query parameter` });
    return null;
  }
  return value;
}

function optionalQueryString(req: Request, name: string): string | null {
  const value = req.query[name];
  return typeof value === "string" && value !== "" ? value : null;
}

function requireIntegerParam(
  req: Request,
  res: Response,
  name: string,
  errorMessage: string,
): number | null {
  const id = Number(req.params[name]);
  if (!Number.isInteger(id)) {
    res.status(400).json({ error: errorMessage });
    return null;
  }
  return id;
}

function idRoute(
  path: string,
  errorMessage: string,
  handler: (id: number, req: Request) => Promise<unknown>,
): void {
  app.get(
    path,
    asyncRoute(async (req, res) => {
      const id = requireIntegerParam(req, res, "id", errorMessage);
      if (id === null) return;
      res.json(await handler(id, req));
    }),
  );
}

function playerRoute(path: string, handler: (id: number, req: Request) => Promise<unknown>): void {
  idRoute(path, "Invalid player id", handler);
}

function teamRoute(path: string, handler: (id: number, req: Request) => Promise<unknown>): void {
  idRoute(path, "Invalid team id", handler);
}

function requireGameId(req: Request, res: Response): string | null {
  const id = String(req.params.id);
  // 10-char zero-padded numeric id shared by every game-keyed table.
  if (!/^\d{8,10}$/.test(id)) {
    res.status(400).json({ error: "Invalid game id" });
    return null;
  }
  return id.padStart(10, "0");
}

function gameRoute(path: string, handler: (gameId: string) => Promise<unknown>): void {
  app.get(
    path,
    asyncRoute(async (req, res) => {
      const gameId = requireGameId(req, res);
      if (gameId === null) return;
      res.json(await handler(gameId));
    }),
  );
}

// --- Players ---------------------------------------------------------------

app.get(
  "/api/players",
  asyncRoute(async (req, res) => {
    const q_ = optionalQueryString(req, "q") ?? "";
    res.json(await q.searchPlayers(q_));
  }),
);

// Registered before /api/players/:id so the literal "featured" segment isn't
// swallowed by the :id param route.
app.get(
  "/api/players/featured",
  asyncRoute(async (_req, res) => {
    res.json(await q.getFeaturedPlayer());
  }),
);

// Registered before /api/players/:id so the literal "browse" segment isn't
// swallowed by the :id param route. Powers the Players tab's filterable /
// sortable / paginated roster browse.
app.get(
  "/api/players/browse",
  asyncRoute(async (req, res) => {
    const query = (optionalQueryString(req, "q") ?? "").trim();
    const position = optionalQueryString(req, "position") ?? "";
    const teamIdRaw = optionalQueryString(req, "team_id");
    const teamId = teamIdRaw !== null ? Number(teamIdRaw) : null;
    const activeRaw = optionalQueryString(req, "active");
    const active = activeRaw === "true" ? true : activeRaw === "false" ? false : null;
    const letterRaw = optionalQueryString(req, "letter") ?? "";
    const letter = /^[A-Z]$/.test(letterRaw) ? letterRaw : "";
    const sortRaw = optionalQueryString(req, "sort") ?? "name";
    const sort = sortRaw === "team" || sortRaw === "active" ? sortRaw : "name";
    // Default limit is 60 (not clampLimit's DEFAULT_LIMIT=50); clampLimit's
    // MAX_LIMIT=500 ceiling still applies when the client passes `limit`.
    const limit = req.query.limit !== undefined ? clampLimit(req.query.limit) : 60;
    const offset = clampOffset(req.query.offset);
    const teamIdValid = teamId !== null && Number.isFinite(teamId) ? teamId : null;
    const teamIdInt = teamIdValid !== null && Number.isInteger(teamIdValid) ? teamIdValid : null;
    res.json(
      await q.browsePlayers({
        q: query,
        position,
        teamId: teamIdInt,
        active,
        letter,
        sort,
        limit,
        offset,
      }),
    );
  }),
);

playerRoute("/api/players/:id", (id) => q.getPlayerProfile(id));
playerRoute("/api/players/:id/rates", (id) => q.getPlayerPerRates(id));
playerRoute("/api/players/:id/advanced", (id) => q.getPlayerAdvancedStats(id));
playerRoute("/api/players/:id/per100", (id) => q.getPlayerPer100(id));
playerRoute("/api/players/:id/highs", (id) => q.getPlayerHighs(id));
playerRoute("/api/players/:id/recent-games", (id) => q.getPlayerRecentGames(id));
playerRoute("/api/players/:id/form", (id, req) =>
  q.getPlayerFormTracker(id, clampLimit(req.query.limit)),
);
playerRoute("/api/players/:id/shot-splits", (id) => q.getPlayerShotSplits(id));
playerRoute("/api/players/:id/on-off", (id) => q.getPlayerOnOffSplits(id));
playerRoute("/api/players/:id/combine", (id) => q.getPlayerDraftCombine(id));
playerRoute("/api/players/:id/similar", (id) => q.getSimilarPlayers(id));
playerRoute("/api/players/:id/season-ranks", (id, req) =>
  q.getPlayerSeasonRanks(id, clampLimit(req.query.limit)),
);

app.get(
  "/api/players/:id/photo",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).end();
      return;
    }
    const photo = await getPlayerPhoto(id);
    if (!photo) {
      res.status(404).end();
      return;
    }
    res.setHeader("Content-Type", "image/png");
    res.setHeader("Cache-Control", "public, max-age=86400");
    res.send(photo);
  }),
);

// --- Teams -------------------------------------------------------------

app.get(
  "/api/teams",
  asyncRoute(async (req, res) => {
    const q_ = optionalQueryString(req, "q") ?? "";
    res.json(await q.searchTeams(q_));
  }),
);

// Registered before /api/teams/:id so the literal "by-conference" segment
// isn't swallowed by the :id param route.
app.get(
  "/api/teams/by-conference",
  asyncRoute(async (_req, res) => {
    res.json(await q.getTeamsByConference());
  }),
);

teamRoute("/api/teams/:id", (id) => q.getTeamProfile(id));
teamRoute("/api/teams/:id/roster", (id) => q.getTeamRoster(id));
teamRoute("/api/teams/:id/playoff-series", (id) => q.getTeamPlayoffSeries(id));
teamRoute("/api/teams/:id/coaches", (id) => q.getTeamCoachHistory(id));
teamRoute("/api/teams/:id/lineups", (id) => q.getTeamLineupEfficiency(id));
teamRoute("/api/teams/:id/ranks", (id) => q.getTeamRanks(id));
teamRoute("/api/teams/:id/opponent-stats", (id) => q.getTeamOpponentStats(id));
teamRoute("/api/teams/:id/franchise-leaders", (id) => q.getFranchiseLeaders(id));
teamRoute("/api/teams/:id/franchise-top", (id, req) =>
  q.getFranchiseTopPlayers(
    id,
    optionalQueryString(req, "stat") ?? "gp",
    clampLimit(req.query.limit),
  ),
);

// --- Standings ---------------------------------------------------------

app.get(
  "/api/standings/seasons",
  asyncRoute(async (_req, res) => {
    res.json(await q.listStandingsSeasons());
  }),
);

app.get(
  "/api/standings",
  asyncRoute(async (req, res) => {
    const season = requireQueryString(req, res, "season");
    if (!season) return;
    const seasonType = typeof req.query.type === "string" ? req.query.type : "Regular";
    res.json(await q.getStandings(season, seasonType));
  }),
);

// --- Draft ---------------------------------------------------------------

app.get(
  "/api/draft/years",
  asyncRoute(async (_req, res) => {
    res.json(await q.listDraftYears());
  }),
);

app.get(
  "/api/draft",
  asyncRoute(async (req, res) => {
    const season = requireQueryString(req, res, "season");
    if (!season) return;
    res.json(await q.getDraftYear(season));
  }),
);

// Registered before /api/draft/value/:something variants would be added.
// Currently only /value and /value/rounds are used; no :id param route.
app.get(
  "/api/draft/value/rounds",
  asyncRoute(async (_req, res) => {
    res.json(await q.listDraftValueRounds());
  }),
);

app.get(
  "/api/draft/value",
  asyncRoute(async (req, res) => {
    const roundRaw = optionalQueryString(req, "round");
    const round = roundRaw !== null ? Number(roundRaw) : undefined;
    const sort = optionalQueryString(req, "sort") ?? "career_ppg";
    const limit = clampLimit(req.query.limit);
    res.json(await q.getDraftValueBoard({ round, sortBy: sort, limit }));
  }),
);

// --- Awards --------------------------------------------------------------

app.get(
  "/api/awards/seasons",
  asyncRoute(async (_req, res) => {
    res.json(await q.listAwardSeasons());
  }),
);

app.get(
  "/api/awards/types",
  asyncRoute(async (_req, res) => {
    res.json(await q.listAwardTypes());
  }),
);

app.get(
  "/api/awards",
  asyncRoute(async (req, res) => {
    const season = requireQueryString(req, res, "season");
    if (!season) return;
    const type = optionalQueryString(req, "type");
    res.json(await q.getAwards(season, type));
  }),
);

// --- Leaders -------------------------------------------------------------
//
// Literal-segment routes are registered before /api/leaders/* :something
// variants would be added (none currently — all leader endpoints are
// query-string driven).

app.get(
  "/api/leaders/seasons",
  asyncRoute(async (_req, res) => {
    res.json(await q.listLeaderSeasons());
  }),
);

app.get("/api/leaders/stat-keys", (_req, res) => {
  res.json(q.listLeaderStatKeys());
});

app.get(
  "/api/leaders/season",
  asyncRoute(async (req, res) => {
    const season = requireQueryString(req, res, "season");
    if (!season) return;
    const statKey = requireQueryString(req, res, "stat_key");
    if (!statKey) return;
    const limit = clampLimit(req.query.limit);
    res.json(await q.getSeasonLeaders(season, statKey, limit));
  }),
);

app.get(
  "/api/leaders/all-time",
  asyncRoute(async (req, res) => {
    const rawStat = optionalQueryString(req, "stat") ?? "pts";
    const stat = rawStat === "ast" || rawStat === "reb" ? rawStat : "pts";
    const limit = clampLimit(req.query.limit);
    res.json(await q.getAllTimeLeaders(stat, limit));
  }),
);

// --- Player splits / estimated metrics / shot chart ----------------------

playerRoute("/api/players/:id/location-splits", (id) => q.getPlayerLocationSplits(id));
playerRoute("/api/players/:id/estimated-metrics", (id) => q.getPlayerEstimatedMetrics(id));
playerRoute("/api/players/:id/shot-chart/seasons", (id) => q.listPlayerShotSeasons(id));
playerRoute("/api/players/:id/shot-chart", (id, req) =>
  q.getPlayerShotChart(id, optionalQueryString(req, "season")),
);

// --- Team head-to-head + season context -----------------------------------

teamRoute("/api/teams/:id/head-to-head", (id) => q.getTeamHeadToHead(id));
teamRoute("/api/teams/:id/season-context", (id) => q.getTeamSeasonContext(id));

// --- Game detail -----------------------------------------------------------

gameRoute("/api/games/:id", (gameId) => q.getGameDetail(gameId));

// --- Award voting detail ----------------------------------------------------

app.get(
  "/api/awards/voting",
  asyncRoute(async (req, res) => {
    const season = requireQueryString(req, res, "season");
    if (!season) return;
    const award = requireQueryString(req, res, "award");
    if (!award) return;
    res.json(await q.getAwardVoting(season, award));
  }),
);

// --- Betting (Vegas vs Reality) --------------------------------------------
//
// All literal-segment routes; no :id params under /api/betting.

app.get(
  "/api/betting/seasons",
  asyncRoute(async (_req, res) => {
    res.json(await q.listBettingSeasons());
  }),
);

app.get(
  "/api/betting/market-beaters",
  asyncRoute(async (req, res) => {
    const season = optionalQueryString(req, "season");
    res.json(await q.getBettingMarketBeaters(season));
  }),
);

app.get(
  "/api/betting/upsets",
  asyncRoute(async (req, res) => {
    const season = optionalQueryString(req, "season");
    const limit = clampLimit(req.query.limit);
    res.json(await q.getBettingUpsets(season, limit));
  }),
);

app.get(
  "/api/betting/calibration",
  asyncRoute(async (_req, res) => {
    res.json(await q.getBettingCalibration());
  }),
);

// --- Four factors ------------------------------------------------------------

app.get(
  "/api/four-factors/seasons",
  asyncRoute(async (_req, res) => {
    res.json(await q.listFourFactorsSeasons());
  }),
);

app.get(
  "/api/four-factors/teams",
  asyncRoute(async (req, res) => {
    const season = requireQueryString(req, res, "season");
    if (!season) return;
    res.json(await q.getFourFactorsTeams(season));
  }),
);

app.get(
  "/api/four-factors/league",
  asyncRoute(async (_req, res) => {
    res.json(await q.getFourFactorsLeague());
  }),
);

gameRoute("/api/games/:id/four-factors", (gameId) => q.getGameFourFactors(gameId));

// --- Matchups (who guarded whom) --------------------------------------------
//
// /api/matchups/leaders is literal-only; the player route nests the :id one
// segment deeper so there's no literal/param collision.

app.get(
  "/api/matchups/leaders",
  asyncRoute(async (req, res) => {
    const sort = req.query.sort === "workload" ? "workload" : "toughest";
    const limit = clampLimit(req.query.limit);
    res.json(await q.getMatchupDefenderLeaders(sort, limit));
  }),
);

// --- Game flow + clutch -------------------------------------------------------

gameRoute("/api/games/:id/flow", (gameId) => q.getGameFlow(gameId));

app.get(
  "/api/clutch/seasons",
  asyncRoute(async (_req, res) => {
    res.json(await q.listClutchSeasons());
  }),
);

app.get(
  "/api/clutch/leaders",
  asyncRoute(async (req, res) => {
    const season = requireQueryString(req, res, "season");
    if (!season) return;
    const limit = clampLimit(req.query.limit);
    res.json(await q.getClutchLeaders(season, limit));
  }),
);

playerRoute("/api/matchups/player/:id", (id, req) => {
  const side = req.query.side === "defense" ? "defense" : "offense";
  return q.getPlayerMatchups(id, side, clampLimit(req.query.limit));
});

// --- Officials + Coaching leaderboards --------------------------------------

app.get(
  "/api/officials/leaders",
  asyncRoute(async (req, res) => {
    const limit = clampLimit(req.query.limit);
    res.json(await q.getOfficialsLeaders(limit));
  }),
);

app.get(
  "/api/coaches/leaders",
  asyncRoute(async (req, res) => {
    const limit = clampLimit(req.query.limit);
    res.json(await q.getCoachingLeaders(limit));
  }),
);

// --- Generic table browser (developer escape hatch, not used by the UI) --

app.get("/api/admin/tables", async (_req, res) => {
  try {
    res.json(await listTables());
  } catch (err) {
    res.status(500).json({ error: String(err) });
  }
});

app.get("/api/admin/tables/:name", async (req, res) => {
  try {
    const table = await resolveTable(req.params.name);
    if (!table) {
      res.status(404).json({ error: `Unknown table: ${req.params.name}` });
      return;
    }
    const limit = clampLimit(req.query.limit);
    const offset = clampOffset(req.query.offset);
    const page = await readTablePage(table, limit, offset);
    res.json({ ...page, table, limit, offset });
  } catch (err) {
    res.status(500).json({ error: String(err) });
  }
});

app.post("/api/admin/query", async (req, res) => {
  const body = req.body as { sql?: unknown };
  const sql = typeof body.sql === "string" ? body.sql.trim() : "";
  if (!sql) {
    res.status(400).json({ error: "Missing 'sql' in request body" });
    return;
  }
  try {
    res.json(await runReadOnlyQuery(sql));
  } catch (err) {
    res.status(400).json({ error: String(err) });
  }
});

const port = Number(process.env.API_PORT ?? 8787);
app.listen(port, () => {
  console.log(`[api] listening on http://localhost:${port}`);
});
