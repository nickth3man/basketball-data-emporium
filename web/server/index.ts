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

// --- Players ---------------------------------------------------------------

app.get(
  "/api/players",
  asyncRoute(async (req, res) => {
    const q_ = typeof req.query.q === "string" ? req.query.q : "";
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

app.get(
  "/api/players/:id",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid player id" });
      return;
    }
    res.json(await q.getPlayerProfile(id));
  }),
);

app.get(
  "/api/players/:id/rates",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid player id" });
      return;
    }
    res.json(await q.getPlayerPerRates(id));
  }),
);

app.get(
  "/api/players/:id/advanced",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid player id" });
      return;
    }
    res.json(await q.getPlayerAdvancedStats(id));
  }),
);

app.get(
  "/api/players/:id/per100",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid player id" });
      return;
    }
    res.json(await q.getPlayerPer100(id));
  }),
);

app.get(
  "/api/players/:id/highs",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid player id" });
      return;
    }
    res.json(await q.getPlayerHighs(id));
  }),
);

app.get(
  "/api/players/:id/recent-games",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid player id" });
      return;
    }
    res.json(await q.getPlayerRecentGames(id));
  }),
);

app.get(
  "/api/players/:id/form",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid player id" });
      return;
    }
    const limit = clampLimit(req.query.limit);
    res.json(await q.getPlayerFormTracker(id, limit));
  }),
);

app.get(
  "/api/players/:id/shot-splits",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid player id" });
      return;
    }
    res.json(await q.getPlayerShotSplits(id));
  }),
);

app.get(
  "/api/players/:id/on-off",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid player id" });
      return;
    }
    res.json(await q.getPlayerOnOffSplits(id));
  }),
);

app.get(
  "/api/players/:id/combine",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid player id" });
      return;
    }
    res.json(await q.getPlayerDraftCombine(id));
  }),
);

app.get(
  "/api/players/:id/similar",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid player id" });
      return;
    }
    res.json(await q.getSimilarPlayers(id));
  }),
);

app.get(
  "/api/players/:id/season-ranks",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid player id" });
      return;
    }
    const limit = clampLimit(req.query.limit);
    res.json(await q.getPlayerSeasonRanks(id, limit));
  }),
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
    const q_ = typeof req.query.q === "string" ? req.query.q : "";
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

app.get(
  "/api/teams/:id",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid team id" });
      return;
    }
    res.json(await q.getTeamProfile(id));
  }),
);

app.get(
  "/api/teams/:id/roster",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid team id" });
      return;
    }
    res.json(await q.getTeamRoster(id));
  }),
);

app.get(
  "/api/teams/:id/playoff-series",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid team id" });
      return;
    }
    res.json(await q.getTeamPlayoffSeries(id));
  }),
);

app.get(
  "/api/teams/:id/coaches",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid team id" });
      return;
    }
    res.json(await q.getTeamCoachHistory(id));
  }),
);

app.get(
  "/api/teams/:id/lineups",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid team id" });
      return;
    }
    res.json(await q.getTeamLineupEfficiency(id));
  }),
);

app.get(
  "/api/teams/:id/ranks",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid team id" });
      return;
    }
    res.json(await q.getTeamRanks(id));
  }),
);

app.get(
  "/api/teams/:id/opponent-stats",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid team id" });
      return;
    }
    res.json(await q.getTeamOpponentStats(id));
  }),
);

app.get(
  "/api/teams/:id/franchise-leaders",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid team id" });
      return;
    }
    res.json(await q.getFranchiseLeaders(id));
  }),
);

app.get(
  "/api/teams/:id/franchise-top",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid team id" });
      return;
    }
    const stat =
      typeof req.query.stat === "string" && req.query.stat !== "" ? req.query.stat : "gp";
    const limit = clampLimit(req.query.limit);
    res.json(await q.getFranchiseTopPlayers(id, stat, limit));
  }),
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
    const roundRaw = typeof req.query.round === "string" ? req.query.round : "";
    const round = roundRaw !== "" ? Number(roundRaw) : undefined;
    const sort =
      typeof req.query.sort === "string" && req.query.sort !== "" ? req.query.sort : "career_ppg";
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
    const type =
      typeof req.query.type === "string" && req.query.type !== "" ? req.query.type : null;
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

app.get(
  "/api/leaders/stat-keys",
  asyncRoute(async (_req, res) => {
    res.json(await q.listLeaderStatKeys());
  }),
);

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
    const rawStat = typeof req.query.stat === "string" ? req.query.stat : "pts";
    const stat = rawStat === "ast" || rawStat === "reb" ? rawStat : "pts";
    const limit = clampLimit(req.query.limit);
    res.json(await q.getAllTimeLeaders(stat, limit));
  }),
);

// --- Player splits / estimated metrics / shot chart ----------------------

app.get(
  "/api/players/:id/location-splits",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid player id" });
      return;
    }
    res.json(await q.getPlayerLocationSplits(id));
  }),
);

app.get(
  "/api/players/:id/estimated-metrics",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid player id" });
      return;
    }
    res.json(await q.getPlayerEstimatedMetrics(id));
  }),
);

app.get(
  "/api/players/:id/shot-chart/seasons",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid player id" });
      return;
    }
    res.json(await q.listPlayerShotSeasons(id));
  }),
);

app.get(
  "/api/players/:id/shot-chart",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid player id" });
      return;
    }
    const season =
      typeof req.query.season === "string" && req.query.season !== "" ? req.query.season : null;
    res.json(await q.getPlayerShotChart(id, season));
  }),
);

// --- Team head-to-head + season context -----------------------------------

app.get(
  "/api/teams/:id/head-to-head",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid team id" });
      return;
    }
    res.json(await q.getTeamHeadToHead(id));
  }),
);

app.get(
  "/api/teams/:id/season-context",
  asyncRoute(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) {
      res.status(400).json({ error: "Invalid team id" });
      return;
    }
    res.json(await q.getTeamSeasonContext(id));
  }),
);

// --- Game detail -----------------------------------------------------------

app.get(
  "/api/games/:id",
  asyncRoute(async (req, res) => {
    const id = String(req.params.id);
    // 10-char zero-padded numeric id shared by every game-keyed table.
    if (!/^\d{8,10}$/.test(id)) {
      res.status(400).json({ error: "Invalid game id" });
      return;
    }
    res.json(await q.getGameDetail(id.padStart(10, "0")));
  }),
);

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
    const season =
      typeof req.query.season === "string" && req.query.season !== "" ? req.query.season : null;
    res.json(await q.getBettingMarketBeaters(season));
  }),
);

app.get(
  "/api/betting/upsets",
  asyncRoute(async (req, res) => {
    const season =
      typeof req.query.season === "string" && req.query.season !== "" ? req.query.season : null;
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

app.get(
  "/api/games/:id/four-factors",
  asyncRoute(async (req, res) => {
    const id = String(req.params.id);
    if (!/^\d{8,10}$/.test(id)) {
      res.status(400).json({ error: "Invalid game id" });
      return;
    }
    res.json(await q.getGameFourFactors(id.padStart(10, "0")));
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
