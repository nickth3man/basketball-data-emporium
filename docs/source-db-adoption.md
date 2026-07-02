# What the source DB can add to this project

_Analysis date: 2026-07-02. Source:
`C:/Users/nicolas/Documents/GitHub/basketball-data/duckdb/nba.duckdb` (23 GB,
11 schemas — the database our warehouse snapshot was built from, plus layers
that never made it into the snapshot)._

## ID alignment (how their data joins to ours)

- **NBA-lineage schemas** (`nbadb`, `unified_star`, most of `main`) use the
  same NBA person/team ids as our warehouse — direct joins, no mapping
  needed. All 45 of our franchise ids exist in their `dim_team`.
- **BBR-lineage schemas** (`stg_bref`, `main.fact_bref_*`) key by BBR slug —
  covered by our crosswalk (`data/audit/out/player_crosswalk.csv`).
- The source DB has its own bridge, `main.bridge_player_source_id`
  (name+birthdate matching). Cross-validation against our crosswalk:
  **4,604 / 4,607 shared mappings agree (99.93%)**. Its DOB evidence settled
  three conflicts (the two 2013 Tony Mitchells were swapped in ours; Chuck
  Halbert confirmed) and confirmed our duplicate-identity finding (it maps
  both warehouse ids for Vaught/Werdann to the same BBR player). Our override
  file was extended with 8 entries accordingly; the regenerated crosswalk now
  covers **4,860 / 4,887 players (99.45%)**. Ours also resolves ~250 players
  their bridge left unresolved (the nickname tiers + manual overrides), so
  the extension went both ways in principle — our crosswalk is now the
  superset dictionary.

## Adoptable data, by priority

### 1. `main.fact_player_season_stat_resolved` (40,325 rows) — fixes finding #1

BBR per-season player stats **already resolved to NBA person_id/team_id**,
with regular/playoff split, jersey number, GS, minutes, totals — plus the
BBR-proprietary advanced metrics the app explicitly lacks today
(`queries.ts` notes "PER and Win Shares aren't present anywhere in this
database"): **PER, TS%, ORtg/DRtg, OWS/DWS/WS/WS-48, OBPM/DBPM/BPM, VORP**.
This is a clean, externally-verified replacement for the corrupted
`agg_player_season`/`agg_player_career` layer (which only matches BBR 44% of
the time) and covers all eras including pre-1996 and ABA.

### 2. `main.fact_game` (73,246 games) — fixes finding #2

Complete game dimension with `home_score`/`away_score`, winner, arena,
attendance, overtime flag. Covers **every season missing from our `game`
table**: regular 1960-61 → 1976-77 gaps, 2012-13, 2023-24 → 2025-26, and the
13 missing playoff runs (1993-94, 2001-02, 2024 Finals…). 2025-26 playoffs
appear truncated (~May 5 snapshot, 52 games) — the season's final weeks need
a top-up from a newer pull.

### 3. `stg_bref` award tables + the bridge — fixes finding #3

`player_award_shares`, `end_of_season_teams(_voting)`, `all_star_selections`
keyed by BBR slug; joined through the crosswalk they rebuild
`fact_player_awards` **without the diacritic dropout** (Dončić/Bogdanović).
Note the NBA-lineage award tables in the source DB (`nbadb.fact_player_awards`,
`unified_star.fact_player_awards`) have the *same* Luka gap — do not reuse
them; rebuild from `stg_bref` + bridge.

### 4. `unified_star.fact_player_game_boxscore` / `main.fact_player_game_stats` (1.67M rows)

Full-history player game logs **1946-2026** (our `fact_player_game_log`
starts 1996-97). Same NBA person ids. Enables pre-1996 game logs in the app
and era-complete aggregate rebuilds. Caveats from the audit: DNP rows carry
empty minutes and must be excluded from GP; ~84% exact vs BBR full-history
(scorekeeping differences dominate pre-1970s).

### 5. New entities we have no equivalent for

| Source table | Rows | What it adds |
|---|---|---|
| `main.fact_game_official` + `dim_official` | 4,147 + 235 | Referee assignments (partial coverage) |
| `unified_star.dim_arena` | 273 | Arena dimension (city/state), game-linked |
| `unified_star.fact_game_quarter_scores` / `main.fact_team_game_period` | 303,920 | Quarter-by-quarter line scores |
| `main.fact_starting_lineup_player` | 572,451 | Starting lineups per game |
| `main.fact_game_market_odds` / `fact_game_odds_snapshot` | 160,910 / 513,913 | Betting lines incl. history |
| `stg_bref.team_summaries` / `opponent_*` / `team_stats_per_100_poss` | ~1,900 ea. | SRS/pace/four-factors, opponent splits — a viable substitute for the permanently-empty `fact_team_splits` |
| `stg_bref.player_shooting`, `player_play_by_play` | 18,254 ea. | BBR shooting-distance and position-estimate splits (1997+) |
| `main.dim_bref_player` / `stg_bref.player_career_info` | 5,416 | HOF flags, career spans, colleges — includes ABA-only players our dim lacks |

### Dead ends checked

- `fact_team_splits` is **0 rows in the source DB too** — the gap is not
  fillable from any available source.
- NBA-lineage award tables carry the same diacritic dropout (see #3).
- `unified_star.fact_team_game_boxscore` (75,980) is exactly our
  `fact_team_game_log` — nothing new.

## The remaining schemas (full sweep)

Verdicts for the schemas not covered above, diffed table-by-table against the
warehouse's 417 tables:

- **`nbadb` (251 tables, 110M rows)** — byte-identical row counts to the
  same-named tables in our warehouse (it *is* the layer our snapshot copied).
  Nothing new.
- **`raw_sqlite` / `stg_nba_api_sqlite` (22 tables)** — already fully
  absorbed: `officials` (70,971), `line_score`, `game_info`, `game_summary`,
  `other_stats` all exist in our warehouse at identical counts (as
  `fact_box_score_summary_v3_*` and same-named copies). The `game` table here
  is the source of ours — same season gaps.
- **`audit` (18 tables)** — their build's QA metadata. Not data to adopt,
  but `audit.player_identity_bridge` corroborates our duplicate-identity
  finding.
- **`raw_json` / `raw_parquet`** — staging behind `main.fact_player_season_stat`
  and `unified_star.fact_pbp_events`, both already recommended above.
- **`raw_csv` (59 tables)** — mostly the CSVs the audit already used. The
  genuinely new items:

| Table | Rows | What it adds |
|---|---|---|
| `playerstatisticsextended` | 838,041 | Per-player-game shooting context 1997+: %-assisted/unassisted makes, share-of-team FGA/REB/AST — not in the warehouse in any form |
| `teamstatisticsextended` | 79,658 | Team-side equivalent of the above |
| `nba_detailed_odds`, `nba_main_lines`, preseason variants | ~170k | Richer betting-market detail than `fact_live_odds` |
| `leagueschedule24_25` / `25_26` | ~2,800 | Full league schedules (incl. games our warehouse is missing) |
| `games_advanced/_four_factors/_misc/_scoring/_traditional` | 51,104 ea. | NBA.com per-game team stats 2006+ — **redundant**: our `fact_box_score_*` tables cover more (1996+) |

### Play-by-play is the sleeper headline

`unified_star.fact_pbp_events` (18.7M rows) vs our `play_by_play` (13.6M):

- Covers the **four regular seasons our PBP is missing** (2012-13,
  2023-24 → 2025-26) and the missing playoff runs (1999-00, 2023-25).
- Contains **all play-in tournament events (game-type 5) and NBA-Cup finals
  (game-type 6)** — entirely absent from our warehouse.
- ~10-13% more events per season even where we have coverage (denser event
  stream from the newer NBA CDN endpoint).

### Matching status

No new id systems were found: every NBA-lineage table joins on the shared
person/team/game ids; every BBR-lineage table joins through the existing
crosswalk. The dictionaries as extended (38 overrides, 99.45% player
coverage, 100% team coverage) are sufficient for **all** adoptable tables in
the external database — no further extension required.

## Adoption status: IMPORTED (2026-07-02)

All adoptable tables were imported into the local `data/nba.duckdb` via
`data/audit/import_source_tables.sql` (idempotent; re-run with the dev server
stopped). 55 tables added (417 → 472), file 9.4 → 10.6 GB; the app test
suite passes against the modified warehouse.

What landed:

- **Dictionaries in-database**: `bridge_player_bbr` (4,860 mappings),
  `bridge_team_bbr` (1,639), plus the source DB's own
  `bridge_player_source_id` for reference.
- **Curated facts** under their source names: `fact_game` (complete, with
  scores — the 2024 Finals are now queryable), `fact_player_season_stat_resolved`
  (BBR stats incl. PER/WS/BPM keyed to NBA ids), `fact_player_game_boxscore`
  (1946+ game logs), `fact_pbp_events` (18.7M events incl. play-in and Cup
  finals), quarter scores, starting lineups, market odds + betting lines,
  officials, season leaders, the `fact_bref_player_season_*` family,
  `dim_bref_player`, `dim_team_season`.
- **`stg_bref_*` (22 tables)**: the lossless BBR layer, each player-keyed
  table enriched with an `nba_player_id` column via the crosswalk (96% of
  player-season rows resolve; the rest are ABA-only players), each
  team-season table with `nba_team_id`. Verified: Dončić's award shares are
  reachable by NBA id (9 rows) — the diacritic gap is closed at the data
  level.
- **raw_csv extras**: `playerstatisticsextended`, `teamstatisticsextended`,
  detailed odds/main lines (incl. preseason), 2024-25/2025-26 schedules.

Deliberately not imported (documented in the script header): the source's
resolved award facts (diacritic-lossy — rebuild from `stg_bref_*` +
`bridge_player_bbr` instead) and tables already present at equal/better
coverage (dims, officials, line scores, team game logs, odds snapshots).

Note: this modifies the local warehouse snapshot only (`data/nba.duckdb` is
gitignored). For the change to survive a warehouse rebuild, the same import
belongs in the sibling repo's `build.py`; the script is the spec for that.
The app's regression fixtures (Harden totals, Luka ROY, ATL standings, 2024
Finals) stay `regression` until `queries.ts` is switched to the new tables —
the corrupt `agg_*` layer is untouched.
