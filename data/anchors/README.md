# Supplemental Basketball-Reference anchor data

This directory holds cached Basketball-Reference (BBR) HTML and a scraped
jersey-history table used by the web app. The jersey file is the **second**
priority source in `web/server/queries.ts::getPlayerProfile` (after
`inactive_players`, before `bridge_player_team_season`).

## Directory layout

| Path | Role |
| --- | --- |
| `bbref-pages/` | On-disk HTML cache for BBR pages (many entity types). |
| `bbref-pages/team_roster/{TLA}_{YYYY}.html` | Per-season team roster pages consumed by the jersey scraper. Cached files are reused across runs; the scraper skips network fetches when a file already exists. |
| `bbref-pages/uniform_numbers/number_{N}.html` | Uniform-number index pages (`friv/numbers.fcgi`) consumed by `scrape_uniform_numbers.py`. |
| `bbr_jerseys.jsonl` | Jersey rows: one JSON object per `(player_id, team_id, season_year, jersey_num)`. Read at request time via DuckDB `read_json_auto`. |
| `bbr_jerseys.jsonl.meta.json` | Sidecar from the last scraper run (`row_count`, `skip_counts`, `teams`, `seasons`, cache/fetch counts). For humans and debugging — DuckDB reads only the `.jsonl`. |
| `scrape_uniform_numbers.py` | **Preferred jersey scraper**: BBR's ~101 uniform-number pages cover every (player, team, season, number) in NBA/ABA/BAA history — no per-team-season page sweep needed. See "Uniform-number scraper" below. |
| `scrape_team_rosters.py` | Legacy jersey scraper: per-(team, season) roster HTML → warehouse IDs → JSONL. Still works, but needs ~1,700 pages for the same coverage. |
| `generate_manifest.py` / `manifest.json` | Inventory of the **whole** `bbref-pages/` tree (player pages, box scores, leaders, etc.). Unrelated to jersey scraping except that both share the cache root. |
| `scrape_run.log` | Ad-hoc stderr capture from a bulk scrape attempt (not written by the script automatically). |

### Current corpus (2026-06-30)

These numbers drift as you scrape; check the sidecar for the latest run.

* **Roster cache:** 272 HTML files under `bbref-pages/team_roster/` (27 BBR team abbreviations; heaviest coverage: PHI, PHO, GSW, ORL, POR).
* **JSONL:** 704 rows in `bbr_jerseys.jsonl` (last run: 41 pages from cache, 1 live fetch).
* **Manifest:** 441 HTML files indexed under `bbref-pages/` (player careers, team seasons, box scores, leaders, etc.).

## How jersey history is built

In `getPlayerProfile`, per-season jersey numbers come from three sources,
ranked by `source_priority` (lower wins):

1. **`inactive_players`** (via `per_season_ip`) — primary. Per-game inactive
   list joined to `game` for the season label; majority vote per
   `(team, season)`. Most accurate for players with regular-season GP since
   coverage begins **1996-97**.
2. **`bbr_jerseys.jsonl`** (this directory) — roster-page fallback for seasons
   where `inactive_players` has no row for that `(player, team, season)`.
   Beats bridge rows for the same `(team, season)` because bridge can carry
   a player's **current** number into historical seasons.
3. **`bridge_player_team_season`** — last resort, only when neither of the
   above covers that `(player, team, season)`.

### Bridge suppression when BBR covers a team-season

When the JSONL has a **roster-sized** scrape for a `(team_id, season_year)`
— at least **5 distinct `player_id` values** in `bbr_raw` — bridge rows for
that whole team-season are excluded. That prevents stale bridge numbers from
leaking in for teammates. A single-player BBR backfill overrides bridge for
that player only; it does **not** mark the entire team-season as covered.

When `bbr_jerseys.jsonl` is missing, the BBR CTE is omitted and the query
uses `inactive_players` plus bridge only.

The dev server resolves the JSONL path once at startup relative to
`web/server/queries.ts`. Override with `BBR_JERSEYS_PATH`. Re-running the
scraper atomically replaces the file; the next player-profile request picks
up the new data without restarting the server.

## JSONL row schema

Each line is one roster assignment:

```json
{
  "player_id": 76130,
  "team_id": 1610612737,
  "season_year": "1969-70",
  "season_end_year": 1970,
  "jersey_num": "14",
  "player_name": "Butch Beard",
  "bbr_slug": "beardbu01",
  "bbr_team": "ATL",
  "source_url": "https://www.basketball-reference.com/teams/ATL/1970.html"
}
```

* `season_year` — bridge/warehouse format (`"YYYY-YY"`, e.g. `"1969-70"` for
  end year 1970).
* `bbr_team` — abbreviation as it appears in the BBR URL (may differ from
  warehouse `dim_team.abbreviation`; see alias map below).
* Rows with unmatched `player_id` / `team_id` are dropped at scrape time and
  never written.

## Uniform-number scraper (preferred)

`scrape_uniform_numbers.py` fetches
`https://www.basketball-reference.com/friv/numbers.fcgi?number={N}&year=`
for `00` plus `0`-`99` (101 pages, ~6 minutes at the 3s delay) and emits the
same JSONL. Per row it resolves:

* **player** — BBR slug (from the player link href) → `player_id` via
  `bridge_player_bbr`, deduped one warehouse id per slug by game-log volume
  (the `PLAYER_BBR_XWALK_CTE` rule), with ASCII-folded name match as
  fallback.
* **team** — team display name + season end year (from the season link
  hrefs, full 4-digit years) → `nba_team_id` via `stg_bref_team_summaries`.
  When the summaries row exists but its `nba_team_id` is null (whole-season
  gaps: 1971, 1976, 1977), the same name's nearest season with an id is
  used — which also disambiguates the two reused names "Baltimore Bullets"
  and "Denver Nuggets".

Unlike the roster scraper, rows that fail resolution are **still written**
with null `player_id`/`team_id` (ABA-only franchises like the Kentucky
Colonels, a few unbridged players); every consumer filters nulls itself.
Extra field vs the roster scraper: `bbr_team_name` (the page's display
name). A player who wore two numbers for one team-season (Jordan 1994-95:
45 then 23) appears on both number pages and yields two rows; downstream
priority/dedup picks one.

```sh
# Full sweep (writes the production JSONL):
python data/anchors/scrape_uniform_numbers.py

# Smoke test, cache only:
python data/anchors/scrape_uniform_numbers.py --numbers 45 --out /tmp/smoke.jsonl --no-network
```

After regenerating the JSONL, rebuild the materialized warehouse tables
(dev server stopped): `duckdb data/nba.duckdb -c ".read data/audit/build_coach_jersey_tables.sql"`.

## Running the legacy roster scraper

```sh
# Smoke test (one cached page, no network):
python data/anchors/scrape_team_rosters.py \
  --teams BOS --seasons 1974 \
  --out /tmp/smoke.jsonl --no-network --limit 1

# Scrape a slice and write the production JSONL:
python data/anchors/scrape_team_rosters.py \
  --teams ATL,BOS,CLE,POR --seasons 1970-1975 \
  --out data/anchors/bbr_jerseys.jsonl

# Offline re-parse from cache only (no HTTP):
python data/anchors/scrape_team_rosters.py \
  --teams PHI,PHO,GSW --seasons 1970-2025 \
  --out data/anchors/bbr_jerseys.jsonl --no-network
```

### CLI flags

| Flag | Default | Purpose |
| --- | --- | --- |
| `--teams` | *(required)* | Comma-separated BBR abbreviations (`ATL,BOS`). |
| `--seasons` | *(required)* | Comma list (`1970,1971`) or range (`1970-1975`). Values are **season end years** (the `YYYY` in BBR URLs). |
| `--out` | `data/anchors/bbr_jerseys.jsonl` | Output JSONL path. |
| `--cache-dir` | `data/anchors/bbref-pages/team_roster` | HTML cache directory. |
| `--db` | `data/nba.duckdb` | Read-only DuckDB for `dim_player` / `dim_team` lookups. |
| `--delay` | `3.0` | Minimum seconds between **live** HTTP requests (BBR asks for ≤20/min). |
| `--no-network` | off | Cache only; missing pages are skipped. |
| `--limit` | `0` | Stop after N live fetches (smoke tests). |
| `--user-agent` | see script | HTTP User-Agent string. |

### Important: each run replaces the output file

The scraper writes **only** rows for the `(team, season)` pairs requested on
that invocation. It does **not** merge with a previous JSONL. To grow the
corpus, include every team-season you want in the file when you re-run (or
concatenate JSONL yourself). HTML cache files **do** accumulate on disk across
runs.

Dependencies: Python 3 stdlib plus `duckdb` (optional — without it, or without
`data/nba.duckdb`, the script still caches HTML and emits rows with null IDs
for inspection).

Before touching the cache or network, the scraper filters the requested
team-season cross product through `BBR_TEAM_SEASON_RANGES` in
`scrape_team_rosters.py`. Those ranges are BBR URL eras, not warehouse team
IDs: for example `SDR` is valid only for 1968-1971, `MLH` only for 1952-1955,
and `WSB` only for 1975-1997. This keeps bulk runs from probing historical
aliases deep into later decades. The sidecar records `pages_requested`,
`pages_planned`, and `pages_skipped_out_of_era` for each run.

Rate limiting: honour `--delay` on live fetches. Bulk runs can hit HTTP 429;
use cache-first workflows (`--no-network` after seeding HTML), smaller team
batches, and the era-bounded planner above. See `scrape_run.log` for an
example of a throttled run.

## BBR → warehouse team abbreviations

BBR URL abbreviations sometimes differ from `dim_team.abbreviation`. The
scraper maps them in `BBR_TO_DIM_ABBREV` inside `scrape_team_rosters.py`
(e.g. `PHO`→`PHX`, `BRK`→`BKN`, `NOJ`→`NEO`, `WSB`→`WAS`, `PHW`→`PHI`).
Rows where the mapped abbreviation still has no `dim_team` match are skipped
(`no_team_match` in the sidecar). Valid BBR seasons for each URL abbreviation
are separately bounded by `BBR_TEAM_SEASON_RANGES`.

## Manifest generator

The jersey scraper and manifest generator are independent:

```sh
python data/anchors/generate_manifest.py
# or
python data/anchors/generate_manifest.py \
  --root data/anchors/bbref-pages \
  --out data/anchors/manifest.json
```

`manifest.json` walks all of `bbref-pages/`, records canonical URLs, MD5,
size, inferred `entity_type`, and derived slugs/years. Re-run after adding
HTML to the cache.

## Edge cases and skip reasons

Sidecar `skip_counts` keys (from `scrape_team_rosters.py`):

| Key | Meaning |
| --- | --- |
| `no_canonical` | No HTML available (cache miss under `--no-network`, fetch failure, or HTTP error). |
| `no_roster_table` | Page loaded but no `<table id="roster">` (stub/off-season page). |
| `no_team_match` | BBR abbreviation not mapped to a warehouse `team_id`. |
| `no_player_match` | Roster name not found in `dim_player` / `common_player_info` after ASCII folding. |
| `blank_jersey` | Reserved; non-numeric jersey cells are filtered during parse. |

Player name matching uses exact lookup on ASCII-folded names (`Varejão` →
`Varejao`). Format differences (e.g. `"Jo Jo White"` vs warehouse `"Jojo
White"`) cause misses; fuzzy matching is not implemented yet.

Jersey `0` is kept (valid NBA number). Non-digit roster markers (e.g. `R`
for rookies) are dropped, consistent with `inactive_players` and bridge
filters.
