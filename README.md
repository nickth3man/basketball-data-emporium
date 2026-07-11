# Basketball Data Emporium

An NBA data explorer: a DuckDB warehouse queried by a small Express API and
rendered by a vanilla TypeScript/Vite frontend. No frameworks, no in-repo
ETL — this repo is the read-only app layer on top of a warehouse that's
built and refreshed elsewhere.

## Features

- **Player profiles** — career/season stats (per-game, per-36, per-100,
  advanced), career highs, shot-location splits, on/off splits, draft
  combine numbers, similar players, awards, and a recent-games log with
  era-accurate team-color accents.
- **Team profiles** — franchise history, season-by-season stats, roster,
  coaching history, playoff series results, lineup efficiency, league ranks,
  and opponent four-factors.
- **Standings & Draft/Awards** — conference standings by season, draft
  classes, and season awards.
- **Home page** with a featured/random player spotlight and teams grouped by
  conference.
- **Global search** — a persistent header search box (not embedded in any
  page) that searches players and teams together, with a live results
  dropdown and a dedicated full-results page.

## Getting started

```sh
cd web
npm install
npm run dev
```

This starts the Express API (`:8787`) and the Vite dev server (`:5173`)
together; Vite proxies `/api/*` to the API in dev. Open
`http://localhost:5173`.

You'll need `data/nba.duckdb` present locally for the API to serve real
data — this repo doesn't build the warehouse, only consumes it. Get a copy
from wherever your warehouse is built/shared and drop it at that path.

## Repo layout

```
web/                  The app — see CLAUDE.md (repo root) for the full breakdown
  server/             Express API: DuckDB queries, routes, team colors, photos
  src/                 Vite/TypeScript frontend: tab views, header search, DOM helpers
  test/                 Vitest unit tests
data/
  nba.duckdb            Local warehouse snapshot (gitignored, not included)
  anchors/               Supplemental Basketball-Reference scrape corpus
                        (jersey numbers, historical coaches) — see
                        data/anchors/README.md
```

## Scripts (run inside `web/`)

```sh
npm run dev            # API + frontend together, with hot reload
npm run typecheck       # tsc --noEmit
npm run lint            # eslint .
npm run format           # prettier --write .
npm run test              # vitest run
npm run build              # production build
```

Pre-commit hooks (lint, format, typecheck on staged files) and pre-push hooks
(typecheck, tests, knip for chat/frontend) run automatically via
[lefthook](https://github.com/evilmartians/lefthook), installed by `npm install`
in the repo root. CI (`.github/workflows/web.yml`) runs the full suite on every
push/PR to `web/`.

## Documentation

- [`CLAUDE.md`](CLAUDE.md) — architecture, conventions, orientation, and
  quality gates for anyone (human or AI) working on the codebase.
- [`data/anchors/README.md`](data/anchors/README.md) — the
  Basketball-Reference scraper corpus that supplements the warehouse.
