# Data-quality audit: warehouse vs. external ground truth

_Audit date: 2026-07-02. Warehouse snapshot: `data/nba.duckdb` (2026-06-30, 9.4 GB)._

The project's goal is 100%-accurate NBA data. This audit reconciled the
warehouse against four independent references:

1. **Basketball-Reference lineage** — the BBR-derived CSVs in the sibling
   repo (`C:/Users/nicolas/Documents/GitHub/basketball-data/csv/nba/`:
   `player_totals.csv`, `team_summaries.csv`, `player_award_shares.csv`,
   `end_of_season_teams.csv`, `all-star_selections.csv`,
   `draft_pick_history.csv`, `player_career_info.csv`, `team_abbrev.csv`).
2. **NBA.com lineage** — the NBA-Stats-derived CSVs in the same repo
   (`playerstatistics.csv` — 1.67M boxscore rows 1946-2026, `players.csv`,
   `games.csv`), joined directly on NBA person/team ids.
3. **Live basketball-reference.com** (via Firecrawl) for adjudicating
   clusters where the offline sources disagreed.
4. The warehouse's **own fact tables** (`fact_player_game_log`, `game`) as an
   internal consistency check against its aggregate layer.

All scripts and outputs live in `data/audit/` (`build_crosswalk.sql`,
`prepare_nba_lineage.sql`, `reconcile.sql`, `reconcile2.sql`, outputs under
`data/audit/out/`). Re-run order: crosswalk → prepare → reconcile → reconcile2.

## Alignment (mapping dictionaries)

- **Players**: warehouse `player_id` = NBA person id; 100% of the 4,887
  warehouse players exist in the NBA-lineage `players.csv`. Mapping to BBR
  slugs required a five-tier crosswalk (exact normalized name → name+career
  span → Jaro-Winkler fuzzy → unique-surname+span → token-set/flip), plus a
  hand-curated 30-pair override file
  (`data/audit/player_crosswalk_overrides.json`) for documented nicknames
  (Tiny Archibald, Fat Lever, Pearl Washington, KJ Martin…). Result:
  **4,855 / 4,887 matched (99.35%)** → `data/audit/out/player_crosswalk.csv`.
  The 32 residuals are duplicate warehouse identities (below) or fringe
  players with no BBR page.
- **Teams**: **100%** of 1,639 team-seasons mapped
  (`data/audit/out/team_crosswalk.csv`), by per-season name match with an
  abbreviation fallback (BBR: BRK/CHO/PHO ↔ NBA: BKN/CHA/PHX, plus historical
  aliases).

## Verdict by surface (what the app serves)

| Surface | Source table | Verdict |
|---|---|---|
| Game scores | `game` | **Accurate where present** (0 diffs in 62,263 games vs NBA lineage after id normalisation; the 55 raw diffs adjudicated in the warehouse's favour vs BBR) — but **10 regular seasons + 13 playoff runs missing entirely** (below) |
| Player game logs | `fact_player_game_log` | **Accurate** (93.8% exact-points vs BBR per season; residual is BBR-side corrections and Cup-game classification) — but only covers 1996-97+ |
| Player season/career aggregates | `agg_player_season`, `agg_player_career`, `agg_player_season_per36/…`, `analytics_player_*` | **Systematically corrupt** — see finding #1 |
| Standings | `fact_standings` | Accurate through 2019-20; **play-in games pollute W-L 2020-21 onward**; 2023-24 is a stale mid-season snapshot |
| Major awards (MVP/ROY/DPOY/SMOY/MIP winners) | `fact_player_awards` (`subtype1='Selected'`) | Accurate **except** diacritic-name players dropped entirely and a few missing winner flags (finding #3) |
| All-Star / All-NBA / All-Defense / All-Rookie | `fact_player_awards` | Same diacritic gap (Dončić has zero rows); otherwise matches BBR |
| Draft | `draft_history` | Round/team accurate; **overall_pick off by one after forfeited picks** (e.g. everything after pick ~29 of 2002: Boozer 34 vs official 35 — 352 rows affected) |
| Player bios | `dim_player` | 2,168 diffs vs NBA lineage (mostly height/weight vintage differences; birthdates largely agree) — low severity, `data/audit/out/recon_player_bio.csv` |
| Team season stats | `agg_team_season` | Mirrors the standings issues (play-in/Cup games included, 2023-24 snapshot); per-game averages otherwise within rounding |

## Finding 1 — the aggregate layer is corrupted by a franchise-era fan-out (CRITICAL)

`analytics_player_game_complete` contains one copy of every player-game **per
historical franchise name**: Harden's 2020-21 Brooklyn games exist three
times (as "NJ", "BKN", and "NYK" — the Nets' name history), his Houston games
twice ("SD", "HOU"). Every aggregate built on top of it inherits integer-
multiple inflation (2×–6× depending on the franchise's era count and which
subset survived filtering):

- Only **44%** of `agg_player_season` player-seasons match BBR points exactly
  (10,828 / 24,680); mid-50s–60s rows are almost uniformly doubled.
- Only **49%** match the warehouse's own game log (1996+).
- GP, totals, averages (22.7 vs true 24.6 PPG for 2020-21 Harden) and per-36
  rates are all affected; `agg_player_career` additionally disagrees with the
  (already wrong) season sums.
- By contrast the game log itself is clean: **93.8%** exact vs BBR, and the
  residual is largely definitional (NBA-Cup game classification, BBR
  scorekeeping corrections).

**Fix path**: rebuild `agg_player_season`/`agg_player_career`/per-36/advanced
from `fact_player_game_log` (1996+) and from the NBA-lineage boxscores or BBR
totals for earlier seasons — not from `analytics_player_game_complete`. This
is an upstream ETL fix (the warehouse is built in the sibling
`basketball-data` repo, `duckdb/build.py`); nothing in this repo can rewrite
the read-only warehouse. Interim app-side mitigation: derive profile
season/career lines from `fact_player_game_log` where coverage exists.

Regression fixture: `season_line.harden_2020_21_traded_totals`.

## Finding 2 — the `game` table is missing whole seasons (CRITICAL)

Missing regular seasons: **1960-61, 1961-62, 1966-67, 1970-71, 1975-76,
1976-77, 2012-13, 2023-24, 2024-25, 2025-26**.
Missing playoff runs: **1957-58, 1958-59, 1960-61, 1964-65, 1968-69, 1993-94,
1995-96, 1999-00, 2001-02, 2005-06, 2023-24, 2024-25, 2025-26**.

Where games exist, scores are excellent: zero mismatches vs the NBA lineage
after game-id normalisation; the 55 raw score diffs (2002-05 cluster) were
adjudicated against live BBR box scores and the **warehouse was right**
(e.g. 2002-11-05 CLE 89–70 LAL confirmed).

Consequences: `getTeamPlayoffSeries` (re-derived from `game`) cannot return
the 1994/1996/2000/2002/2006/2024/2025 title runs; team recent-games and any
game-derived W-L are empty for the missing seasons.

Regression fixture: `playoff_series.finals_2024_bos_dal`.

## Finding 3 — award ETL drops diacritic names (HIGH)

**Luka Dončić has zero rows in `fact_player_awards`** — no 2018-19 ROY, none
of his All-NBA First Team selections, no All-Star selections. Bogdan and
Bojan Bogdanović are likewise absent. The award load evidently matched
players by ASCII name (the same failure mode already documented for the BBR
scrapers). A handful of other winner flags are missing too: 1949-50 ROY
(Alex Groza per BBR shares file), 1952-53 (Monk Meineke), 1976-77 (Adrian
Dantley), 1982-83 SMOY (Bobby Jones), 1996-97 MIP (Isaac Austin). Shared
awards (1971/1995/2000 ROY) are represented correctly on both sides.
Some All-Star diffs (Dantley 1984-86, Fat Lever 1988/1990, Artis Gilmore
1986) may be replacement-selection semantics — not yet adjudicated.

Regression fixture: `roy.2019_luka`. Diff lists:
`data/audit/out/recon_awards.csv`, `recon_allstar.csv`, `recon_allnba.csv`.

## Finding 4 — standings include play-in games; 2023-24 is a stale snapshot (HIGH)

`fact_standings` is exact vs BBR for every season through 2019-20. From
2020-21 on, the eight play-in participants each season carry their play-in
results in the regular-season W-L (e.g. 2021-22 ATL 45-39 vs official 43-39).
2023-24 is frozen a few games before season end for all 30 teams (BOS 61-16
vs official 64-18); 2024-25 has 15 teams off by one game. Full list:
`data/audit/out/recon_standings.csv`.

Regression fixture: `standings_record.atl_2022_43_39`.

## Finding 5 — draft `overall_pick` numbering ignores forfeited picks (MEDIUM)

352 picks disagree with BBR. Adjudicated 2002 against live BBR: Boozer is
officially #35, warehouse says #34 — the warehouse numbers picks
consecutively, skipping forfeited slots (Minnesota's Joe-Smith-penalty
forfeits, etc.). Round and team are correct. List:
`data/audit/out/recon_draft.csv`.

## Finding 6 — duplicate player identities (MEDIUM)

Same person split across two warehouse `player_id`s (careers fragmented in
profiles): **Loy Vaught** (919 + 78412), **Ed O'Bannon** (709 + 77741),
**Robert Werdann** (438 + 78493), **Kurt Rambis** (77905 + ghost 1272 with a
single 1990-91 game). Same-name pairs that are genuinely different people
(three 1970s-80s George Johnsons, two Eddie Johnsons, etc.) were verified as
distinct. Detection query in the audit session; candidates in
`data/audit/out/player_unmatched_wh.csv`.

## Finding 7 — coverage gaps vs. history (LOW, structural)

The warehouse tracks the NBA-Stats universe: no ABA-only players/seasons
(BBR has them), no BAA 1946-48 completeness (early seasons undercount
substantially — 1946-48 player totals are fractions of BBR's), and
`dim_player` holds only players with stat rows (4,887 vs BBR's 5,416 with
pages). `fact_team_splits` remains empty (pre-existing known gap).

## Not externally verifiable

Shot charts, on/off splits, lineup efficiency, tracking-based advanced stats
(`avg_pie`, usage, ratings) have no counterpart in the reference corpus.
NBA.com live endpoints (e.g. via
[sportsdataverse-py](https://sportsdataverse-py.sportsdataverse.org/docs/intro))
are the only viable check; recommended as a follow-up spot-check, not bulk.

## Ratchet

- Fixture suite: 4 new regression fixtures pin findings 1-4; flip each to
  `stable` when the underlying rebuild lands.
- The crosswalks under `data/audit/out/` are reusable mapping dictionaries
  for future reconciliation runs; the override JSON is the reviewed,
  hand-curated part and should be kept under version control.
- Upstream fixes all live in the sibling `basketball-data` repo's build:
  (a) stop building aggregates on `analytics_player_game_complete`,
  (b) backfill `game` seasons, (c) reload awards with id-based matching,
  (d) exclude play-in games from standings and refresh 2023-25 snapshots,
  (e) renumber draft picks to official slots, (f) merge duplicate player ids.
