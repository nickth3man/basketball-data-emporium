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
