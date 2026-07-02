# AGENTS.md

Context for AI coding agents working in this repo.

## What this is

A read-only NBA data explorer: a DuckDB warehouse (`data/nba.duckdb`, not
committed) queried by a small Express API, rendered by a vanilla
TypeScript/Vite frontend. There is **no in-repo ETL** — the warehouse is
built and refreshed elsewhere and only consumed here. A `data/anchors/`
scraper corpus supplements the warehouse for data BBR has that the warehouse
doesn't (jersey numbers, historical coaches).

## Repo layout

```
web/                  The actual app — everything else is tooling/data
  server/
    db.ts             DuckDB connection (READ_ONLY), generic query helpers
    queries.ts         All curated SQL lives here, grouped by entity
    index.ts           Express routes — thin wrappers over queries.ts
    photos.ts          Player headshot lookup
    teamColorEras.ts    Per-franchise, per-era TruColor palette + colorForEra()
                        (jersey chips, recent-games opponent swatches — reuse
                        this rather than adding another team-color source)
  src/
    main.ts            Tab router: Home/Players/Teams/Standings/Draft & Awards,
                        plus a hidden "search" tab (Search Results) reached
                        only via nba:navigate, never shown in the tab bar
    headerSearch.ts      Persistent global search box mounted into
                        #header-search (index.html), outside all tab content —
                        players/teams views have no search UI of their own
    api.ts              Client fetch wrappers + response types
    views/*.ts          One file per tab, DOM built via dom.ts::el()
    dom.ts              Tiny DOM-builder helper (no framework)
  test/                 Vitest unit tests
data/
  nba.duckdb            Local warehouse snapshot (gitignored, not present by default)
  anchors/               Supplemental BBR scrape corpus (see anchors/README.md)
    bbref-pages/         Cached HTML by entity type (committed — reused across runs)
    bbr_jerseys.jsonl     Scraped jersey-number history, read via read_json_auto
    bbr_coaches.jsonl      Scraped historical coach records, read via read_json_auto
    scrape_team_rosters.py / scrape_team_coaches.py   Cache-first BBR scrapers
lefthook.yml            Pre-commit hooks (see below)
.github/workflows/web.yml   CI: typecheck, lint, format check, test, build
```

There is no root `package.json` app — it only installs `lefthook`. All app
work happens inside `web/`.

## Working in `web/`

```sh
cd web
npm run dev            # concurrently: API (tsx watch, :8787) + Vite (:5173)
npm run typecheck       # tsc --noEmit
npm run lint            # eslint .
npm run format          # prettier --write .
npm run test            # vitest run
```

The frontend calls the Express API under `/api/*`; Vite proxies it in dev.
`data/nba.duckdb` must exist locally for the API to serve real data — get it
from wherever the warehouse is built/shared; this repo doesn't build it.

## Conventions

- **Query pattern**: every entity query lives in `web/server/queries.ts`,
  grouped under a `// --- Section ---` banner comment. Adding a feature
  means: extend/add a query in `queries.ts` → add a thin route in
  `index.ts` → add a typed fetcher + type in `web/src/api.ts` → add a
  `renderXxx` section in the relevant `web/src/views/*.ts` file. Follow this
  four-step pattern rather than improvising a new one.
- **DuckDB connection is read-only** (`access_mode: "READ_ONLY"`). Don't try
  to add write paths against `data/nba.duckdb` from the app.
- **BigInt**: `db.ts::toJsonSafe` downcasts a genuine JS `bigint` to `Number`
  when safe, else stringifies it — but `queryObjects()` reads rows via
  `getRowObjectsJson()`, which already serializes DuckDB BIGINT columns
  (e.g. `team_id`) as JS **strings**, not `bigint`, regardless of
  magnitude — so `toJsonSafe`'s numeric downcast never actually fires for
  them. Never `typeof x === "number"` guard a BIGINT-sourced column; convert
  with `Number(x)` (validated via `Number.isFinite`) instead, or the value
  silently falls through as unmatched/undefined (bit `getPlayerRecentGames`'s
  era-color lookup this way).
- **`season_year` is a string like `"2025-26"`**, not a plain year — slice
  the first 4 chars (`.slice(0, 4)`) before doing numeric year math (era
  lookups, comparisons). See the existing pattern in `queries.ts`'s jersey
  color-era code and reuse it rather than `Number(season_year)` directly.
- **No framework** on the frontend — build DOM nodes with `el()` from
  `dom.ts`, don't reach for React/etc.
- **Search is global, not per-view**: the persistent header search
  (`headerSearch.ts`) is the only search UI — Players/Teams tabs show a
  small curated default list (`searchPlayers("")`/`searchTeams("")`, capped
  server-side) and otherwise only render a specific profile via
  `initialPlayerId`/`initialTeamId`. Don't re-add a per-view search box.
- **`navigateToDetail(tab, id?)`** (`dom.ts`) dispatches the `nba:navigate`
  event and takes any tab id (including hidden ones like `"search"`), not
  just `"players" | "teams"` — `id` is optional for tabs that don't need a
  detail target (e.g. Home's plain nav tiles).
- Prettier: 100-char width, double quotes, trailing commas, semicolons (see
  `web/.prettierrc`). Formatting/linting/typechecking run automatically via
  lefthook on `git commit` — don't hand-format around it.

## Known data-quality gotchas (don't relitigate these)

- `dim_player.is_current` / `is_active` mean "latest SCD row for this
  player", **not** "on an active roster right now" — they're true even for
  long-retired players. For a _current_ team roster, use
  `bridge_player_team_season` filtered to `MAX(season_year)`, deduped per
  player (see `getTeamRoster`).
- `fact_playoff_series`'s own `wins`/`losses`/abbreviation columns are
  unreliable — each real game is duplicated once per historical team
  abbreviation era, and win/loss counters don't reset per series. Playoff
  series results are re-derived from `game.wl_home`/`wl_away` instead (see
  the comment above `getTeamPlayoffSeries`).
- `agg_player_career` is corrupt for at least some players (verified via
  Wes Unseld's career GP). Prefer summing `agg_player_season` for career
  totals.
- `agg_player_season.team_abbreviation` is unreliable (shows one team for a
  player's entire career in some cases). Don't trust it for display.
- `fact_team_splits` is present in schema but has 0 rows — there's no
  in-repo ETL to backfill it. This is a warehouse-build gap, not something
  fixable from this repo.
- Player search / name matching in the BBR scrapers is exact-match on
  ASCII-folded names; format mismatches (e.g. "Jo Jo White" vs "Jojo White")
  cause silent misses (`no_player_match` in scraper sidecars). No fuzzy
  matching yet.

## Scrapers (`data/anchors/`)

Both `scrape_team_rosters.py` and `scrape_team_coaches.py` follow the same
shape: cache-first HTML fetch under `data/anchors/bbref-pages/`, rate-limited
live requests (`--delay`), read-only DuckDB lookups to resolve BBR names/
abbreviations to warehouse `player_id`/`team_id`, and an atomic JSONL +
`.meta.json` sidecar write. **Each run replaces the output file** for the
team/season slice requested — it does not merge with previous runs. See
`data/anchors/README.md` for the full jersey-scraper contract (source
priority vs. `inactive_players`/bridge, BBR abbreviation aliasing, CLI
flags) before adding a new scraper; copy its structure rather than
reinventing one.

## Testing/verification expectations

Before calling a change done, run `npm run typecheck && npm run lint && npm
run test` inside `web/`. For query/data changes, sanity-check the live
result via `/api/admin/query` (read-only SQL box) rather than assuming
schema shapes — several of the gotchas above were only caught that way.
