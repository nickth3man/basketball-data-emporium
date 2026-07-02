# NBA Site vs Database Gap Analysis

## Status update (2026-07-02, post-roadmap build)

Roadmap items shipped since this audit was written: League Leaders tab (B1),
franchise leaders + top players (B2/B5), league-rank badges (B3), draft value
board (B4), the fact_player_career switch (#8), **plus this session's build**:
home/away splits and estimated-metrics cards and a binned shot heat map on
player profiles (I2/I5/A1 — from `analytics_player_general_splits`,
`fact_player_estimated_metrics`, `analytics_shooting_efficiency`), a
head-to-head section and BBR season-context table (SRS/pace/four factors)
on team profiles (I1), a hidden per-game box-score page (line score, game
leaders, starting lineups, officials, closing odds, final scoring plays)
reached from recent-games rows, and full award voting-share tables on
Draft & Awards. New data quirk found during the build: the legacy
`line_score` table's home/away column orientation disagrees with the
verified `fact_game` for ~47% of games — the game view resolves sides by
team id (`web/src/views/game.ts`). Still unshipped: PBP timeline viewer,
player-vs-player comparison, playoff picture, defunct-teams page,
coach detail, rolling averages, betting-lines page.

## Executive Summary

The project ships a **read-only NBA data explorer** powered by a DuckDB warehouse (`data/nba.duckdb`) of **10.6 GB / 384 visible tables / 304 curated domain tables** spanning 1946-2026. The front-end (vanilla TypeScript + Vite) renders five visible tabs (Home, Players, Teams, Standings, Draft & Awards) plus one hidden search-results tab reachable via the global header search.

The audit found a **wide, intentional gap between warehouse contents and UI surface area**. The app exercises **28 query functions over ~63 base tables** (via SQL aliases and CTEs), but the warehouse contains **~241 curated domain tables that are never queried by the app** — including entire analytics domains (league leaders, franchise leaders, head-to-head matchups, draft value, fantasy profiles, betting odds, estimated advanced metrics, BBR proprietary metrics, shot-chart heat maps, on/off impact, splits). Three `fact_team_*` tables (`fact_team_splits`, `fact_team_matchups`, `fact_team_lineups_overall`) are present in the warehouse but **0 rows**, matching the warehouse-side gap already documented in `AGENTS.md`.

The most concrete gap, mapped against competitive research from Basketball-Reference / Crafted NBA / Cleaning the Glass / StatMuse / PBP Stats, is that **the data already exists in the warehouse to ship**:
- a League Leaders view (BBRef's "PTS leaders", etc.) — `fact_season_leader` covers **60+ stat_keys across 79 seasons**
- an All-Time Career Leaders view — `agg_all_time_leaders` covers **4,888 players**
- a Franchise Leaders view — `fact_franchise_leaders` covers **43 franchises** with PTS/AST/REB/BLK/STL leaders
- a Head-to-Head matchup view — `analytics_head_to_head` covers **77,552 matchup-seasons**
- a Shot-Chart heat map — `analytics_shooting_efficiency` carries **6.5 M shot rows with `loc_x`/`loc_y`**
- a Draft Value / Career Success view — `analytics_draft_value` covers **8,658 picks**
- Estimated Advanced Metrics on player profiles — `fact_player_estimated_metrics` covers **2,860 players since 1996-97**
- League Rank badges on player profile — `fact_player_season_ranks` covers **4,888 players since 1946**

The UI is currently **strong on per-entity deep dives** (player and team profiles are genuinely rich) but **weak on cross-cutting discovery**: there is no leaderboard, no comparison, no H2H, no shot chart, no franchise history, no coach detail, no defunct-team view, no playoff picture, no odds page. The recommended roadmap in §10 prioritizes these.

Observed runtime errors (7 total): all are 404s for **player headshot PNGs** for historical players whose `dim_player` rows exist but whose photos are not in the local photo store (`web/server/photos.ts`) — this is a known limitation of the static-photo strategy.

---

## Project Overview

| Aspect | Value |
|---|---|
| Repository | `C:/Users/nicolas/Documents/GitHub/basketball-data-emporium` |
| Frontend | Vite + vanilla TypeScript (`web/`) |
| API framework | Express 5 (TypeScript, `tsx watch`) |
| Database engine | DuckDB (`@duckdb/node-api 1.5.4-r.1`), **read-only** (`access_mode: READ_ONLY`) |
| Warehouse file | `data/nba.duckdb`, **10,642,010,112 bytes (10.6 GB)** |
| Schema count | 1 (`main`), 384 visible tables after filtering |
| Curated domain tables | 304 (excluding `stg_*` staging and `_pipeline_*`/`_extraction_*`/`_lane_*`/`_schema_*` metadata) |
| Dev server | Vite :5173, API :8787 (both running at audit time, PID 43980 / 42376) |
| Audit timestamp | 2026-07-02T02:31 (local) |
| Audited by | MiniMax-M3 via orchestrator + chrome-devtools MCP + duckdb CLI + firecrawl MCP |
| Audit mode | Read-only; no DDL/DML issued against the warehouse |

---

## Database Inventory

Curated domain tables in `main` schema, ordered by row count (top ~80 of 304 shown; full inventory in the JSON companion). Each table was verified by read-only `SELECT` against `information_schema` and `duckdb_tables()`.

| # | Table | Rows | Key Columns | Coverage | Notes |
|---|---|---|---|---|---|
| 1 | `fact_pbp_events` | 18,722,958 | `game_id, event_type, loc_x, loc_y, ...` | 1996-2026 | Granular play-by-play events (used to feed the shot chart derivation in `fact_shot_chart`/`fact_player_game_log`). NOT exposed to UI. |
| 2 | `fact_cumulative_stats` | 17,629,314 | `game_id, person_id, ...` | 1996-2026 | Per-game running aggregates. NOT exposed to UI. |
| 3 | `fact_cumulative_stats_detail` | 17,629,314 | (mirror of `fact_cumulative_stats` with detail) | 1996-2026 | NOT exposed to UI. |
| 4 | `bridge_play_player` | 17,559,522 | `play_id, player_id` | 1996-2026 | Play↔player join bridge. NOT exposed to UI. |
| 5 | `fact_play_by_play_v2` | 13,592,899 | `game_id, period, clock, ...` | 1996-2026 | Play-by-play v2. NOT exposed to UI. |
| 6 | `play_by_play` | 13,592,899 | `game_id, period, ...` | 1996-2026 | Raw PBP. NOT exposed to UI. |
| 7 | `fact_play_by_play` | 13,555,800 | (legacy PBP) | 1996-2026 | NOT exposed to UI. |
| 8 | `analytics_shooting_efficiency` | 6,490,494 | `loc_x, loc_y, shot_zone_basic/area/range, shot_distance, shot_made_flag, league_avg_fg_pct` | 1996-2026 | Per-shot with league-average join. **No UI view exposes this; the player "Shooting by zone" tab is built from `agg_shot_zones`, not from this row-level data.** |
| 9 | `fact_shot_chart` | 6,490,494 | `loc_x, loc_y, shot_type, ...` | 1996-2026 | Raw shot chart. NOT exposed to UI. |
| 10 | `analytics_player_game_complete` | 3,116,411 | all box + advanced + tracking | 1996-2026 | 50+ columns per player-game. NOT exposed to UI. |
| 11 | `fact_player_game_boxscore` | 1,667,844 | boxscore row | 1996-2026 | NOT exposed to UI. |
| 12 | `fact_player_game_traditional` | 1,558,590 | traditional boxscore | 1996-2026 | NOT exposed to UI. |
| 13 | `fact_player_game_advanced` | 1,557,218 | `ts_pct, usg_pct, pace, pie, off_rating, def_rating` | 1996-2026 | NOT exposed to UI. |
| 14 | `agg_player_rolling` | 1,557,218 | `pts_roll5/10/20, reb_roll5/10/20, ast_roll5/10/20` | 1996-2026 | Pre-computed rolling averages. NOT exposed to UI. |
| 15 | `fact_player_game_log` | 786,668 | per-game boxscore | 1996-2026 | **Used** in player "Recent games" via `getPlayerRecentGames`. |
| 16 | `fact_player_game_misc` | 786,668 | per-game misc | 1996-2026 | NOT exposed to UI. |
| 17 | `fact_box_score_summary_v3_last_five_meetings` | 1,431,121 | head-to-head mini-table | 1996-2026 | NOT exposed to UI. |
| 18 | `fact_game_leaders` | 840,393 | per-game stat leaders | 1996-2026 | NOT exposed to UI. |
| 19 | `playerstatisticsextended` | 838,041 | per-game traditional | 1996-2026 | NOT exposed to UI directly (warehouse raw). |
| 20 | `fact_player_game_log` (player) | 786,668 | … | … | (see row 15) |
| 21 | `fact_starting_lineup_player` | 572,451 | game starters | 1996-2026 | NOT exposed to UI. |
| 22 | `bridge_lineup_player` | 571,924 | lineup↔player bridge | 1996-2026 | Underpins `agg_lineup_efficiency` which IS exposed via team "Most-used lineup outings". |
| 23 | `fact_live_odds` | 513,913 | per-game live odds | 2020-2026 | NOT exposed to UI. |
| 24 | `fact_rotation` | 337,305 | rotation segments | 2014-2026 | NOT exposed to UI. |
| 25 | `fact_game_quarter_scores` | 303,920 | per-quarter scores | 1996-2026 | NOT exposed to UI. |
| 26 | `fact_game_scoring` | 303,880 | scoring run detail | 1996-2026 | NOT exposed to UI. |
| 27 | `analytics_game_summary` | 291,215 | full game summary | 1996-2026 | NOT exposed to UI. |
| 28 | `stg_season_matchups` | 288,558 | matchup data | 1946-2026 | Staging; not directly queried. |
| 29 | `fact_scoreboard_team_leaders` | 280,131 | per-game team leaders | 1996-2026 | NOT exposed to UI. |
| 30 | `fact_league_leaders_detail` | 236,769 | league leader detail | 1947-2026 | NOT exposed to UI. |
| 31 | `fact_game_context` | 224,963 | game context (date, season, broadcast) | 1946-2026 | NOT exposed to UI. |
| 32 | `agg_shot_zones` | 187,062 | `player_id, season_year, shot_zone_*, attempts, makes, fg_pct, avg_distance` | 1996-2026 | **Used** in player "Shooting by zone" tab via `getPlayerShotSplits`. |
| 33 | `fact_game_market_odds` | 160,910 | pre-game odds | 2010-2026 | NOT exposed to UI. |
| 34 | `nba_detailed_odds` | 148,042 | pre-game detailed odds | 2010-2026 | NOT exposed to UI. |
| 35 | `analytics_team_game_complete` | 147,059 | full team-game row | 1996-2026 | NOT exposed to UI. |
| 36 | `fact_box_score_team` | 147,059 | team boxscore row | 1996-2026 | NOT exposed to UI. |
| 37 | `fact_box_score_four_factors_team` | 147,059 | team four factors | 1996-2026 | NOT exposed to UI. |
| 38 | `fact_box_score_summary_v3_line_score` | 116,106 | per-quarter line score | 1996-2026 | NOT exposed to UI. |
| 39 | `inactive_players` | 110,191 | per-game inactive | 1996-2026 | NOT exposed to UI. |
| 40 | `analytics_player_general_splits` | 97,631 | `split_type='Location', group_value='Home'/'Away'` | 1946-2026 | Splits by **Location only**. W/L, Pre/Post ASG, Conf/Div, Month, Day-of-Week split types are absent despite the `split_type` column structure. NOT exposed to UI. |
| 41 | `teamstatisticsextended` | 79,658 | raw team-game | 1996-2026 | NOT exposed to UI. |
| 42 | `fact_league_leaders` | 78,923 | season league leaders | 1947-2026 | NOT exposed to UI. |
| 43 | `analytics_player_impact` | 78,923 | season-level on/off impact per player | 1946-2026 | NOT exposed to UI (player on/off IS exposed via `getPlayerOnOffSplits` reading `agg_on_off_splits` instead). |
| 44 | `analytics_player_season_complete` | 78,923 | season-level precomputed PTS/REB/AST/etc + per36/per48/avg | 1996-2026 | NOT exposed to UI (player profile uses `fact_player_season_stat_resolved`). |
| 45 | `analytics_head_to_head` | 77,552 | `team_id, opponent_team_id, season_year, gp, w, l, avg_pts_scored/allowed/margin` | 1946-2026 | NOT exposed to UI. |
| 46 | `fact_box_score_four_factors` | 75,980 | player four factors | 1996-2026 | NOT exposed to UI. |
| 47 | `fact_team_game` | 75,980 | team-game aggregate | 1996-2026 | NOT exposed to UI. |
| 48 | `fact_team_game_log` | 75,970 | team-game log | 1996-2026 | NOT exposed to UI. |
| 49 | `dim_game` | 73,331 | `game_id, game_date, season_year, home_team_id, visitor_team_id, arena_*` | 1946-2023 | **Dim; "game" raw table stops in 2023-06 but `dim_game` stops too; gap to 2025-26 seasons.** |
| 50 | `fact_game_result` | 73,246 | final result | 1946-2023 | NOT exposed to UI (raw `game` table used in standings). |
| 51 | `agg_game_totals` | 73,246 | game totals | 1946-2023 | NOT exposed to UI. |
| 52 | `fact_game` | 73,246 | full game | 1946-2023 | NOT exposed to UI (raw `game` used instead). |
| 53 | `fact_player_season_stat_resolved` | 40,325 | per-player per-season BBR-resolved stats (PER, WS, OWS/DWS, OBPM/DBPM/BPM, VORP) | 1946-2026 | **Used** as base for `player_season_stats` CTE feeding `getPlayerProfile`/`getPlayerAdvancedStats`/`getPlayerPerRates`/`getPlayerPer100`. |
| 54 | `analytics_player_game_complete` (player boxscore) | (see row 10) | — | — | — |
| 55 | `agg_player_season_advanced` | 41,988 | PER, OWS/DWS/WS, OBPM/DBPM/BPM, VORP, TS/USG/AST/REB% | 1996-2026 | **Used** via `getPlayerAdvancedStats`. |
| 56 | `agg_on_off_splits` | 41,586 | `on_off, gp, min, pts, reb, ast, off_rating, def_rating, net_rating` | 1996-2026 | **Used** via `getPlayerOnOffSplits`. |
| 57 | `agg_player_season` | 40,628 | per-season basic totals+averages | 1996-2026 | **Used** via `getPlayerProfile.seasons`. |
| 58 | `agg_player_season_per48` | 40,628 | per-48 rates | 1996-2026 | **Used** via `getPlayerPer100` (note: name is `per100` but underlying table is per48 — check `queries.ts`). |
| 59 | `agg_player_season_per36` | 40,628 | per-36 rates | 1996-2026 | **Used** via `getPlayerPer100`. |
| 60 | `fact_player_season_stat_resolved` (PER/WS) | 40,325 | (see row 53) | — | — |
| 61 | `fact_player_season_ranks` | 39,807 | `rank_min, rank_fgm, rank_pts, rank_eff, ...` (24 ranks) | 1946-2026 | **NOT exposed to UI** despite being directly comparable to `fact_player_season_stat_resolved` for league-rank badges. |
| 62 | `agg_player_season_advanced_legacy_fanout` | 39,807 | wide legacy fan-out | 1996-2026 | NOT exposed to UI. |
| 63 | `agg_player_bio` | 39,807 | `player_id, age, height, weight, college, country, draft_*, gp, pts, reb, ast, net_rating, oreb_pct, dreb_pct, usg_pct, ts_pct, ast_pct` | 1946-2026 | NOT exposed to UI (bio info derived from `dim_player` + BBR scrapers instead). |
| 64 | `agg_league_leaders` | 36,856 | league leaders (rolled up) | 1947-2026 | NOT exposed to UI. |
| 65 | `stg_bref_player_per_game` | 33,339 | BBR per-game | 1946-2026 | Staging. |
| 66 | `fact_bref_player_season_per_game` | 31,119 | BBR per-game resolved | 1946-2026 | NOT exposed to UI (the player "Season stats" tab uses `fact_player_season_stat_resolved`). |
| 67 | `fact_bref_player_season_advanced` | 31,119 | BBR-proprietary advanced (PER/WS/VORP/etc, **canonical source**) | 1946-2026 | NOT exposed to UI directly. |
| 68 | `fact_bref_player_season_totals` | 31,119 | BBR season totals | 1946-2026 | NOT exposed to UI. |
| 69 | `fact_bref_player_season_per36` | 30,618 | BBR per-36 | 1946-2026 | NOT exposed to UI. |
| 70 | `fact_player_career` | 30,160 | BBR career row | 1946-2026 | NOT exposed to UI (player profile uses `agg_player_career` instead, which is documented as corrupt for some players). |
| 71 | `stg_league_game_finder` | 30,000 | league game finder | 1946-2026 | Staging. |
| 72 | `fact_game_betting_lines` | 29,522 | betting lines | 2010-2026 | NOT exposed to UI. |
| 73 | `fact_bref_player_season_per100` | 27,219 | BBR per-100 | 1946-2026 | NOT exposed to UI. |
| 74 | `fact_franchise_players` | 19,275 | player-team career totals (every player who ever wore the jersey) | 1946-2026 | NOT exposed to UI. |
| 75 | `fact_bref_player_season_shooting` | 18,254 | BBR shooting splits | 1946-2026 | NOT exposed to UI. |
| 76 | `fact_bref_player_season_play_by_play` | 18,254 | BBR PBP summary | 1946-2026 | NOT exposed to UI. |
| 77 | `fact_draft_combine_detail` | 17,626 | combine detail | 2000-2025 | NOT exposed to UI (player "Combine" section reads from `fact_player_draft_combine` not this). |
| 78 | `dim_player` | 17,229 | `player_id, full_name, position, height, weight, birth_date, country, college_id, draft_year/round/number, from_year, to_year, is_active, is_current` | 1946-2026 | SCD-aware dimension. **Used** for player bio header. |
| 79 | `fact_player_estimated_metrics` | 16,485 | `e_off_rating, e_def_rating, e_net_rating, e_pace, e_ast_ratio, e_oreb/dreb/reb_pct, e_tov_pct, e_usg_pct` | 1996-2026 | **NOT exposed to UI**. Tracking-era Duck "Estimated" advanced metrics that the per-game advanced table does not include. |
| 80 | `agg_shot_location_season` | 15,384 | `player_id, season_year, fgm, season_fgm_rank` | 1996-2026 | NOT exposed to UI. |
| 81 | `dim_date` | 14,442 | date dim | 1946-2026 | NOT exposed to UI. |
| 82 | `fact_playoff_series` | 14,060 | playoff series row | 1946-2026 | **Used** via `getTeamPlayoffSeries` (with caveat in AGENTS.md about `wins`/`losses` columns). |
| 83 | `nba_preseason_detailed_odds` | 12,868 | preseason odds | 2010-2026 | NOT exposed to UI. |
| 84 | `fact_dunk_score_leaders` | 12,042 | dunk leaders | 1996-2026 | NOT exposed to UI. |
| 85 | `fact_season_leader` | 11,154 | `season, split, stat_key, stat_value` — **60 distinct stat_keys** | 1947-2026 | **NOT exposed to UI**. Perfect base for a League Leaders tab. |
| 86 | `fact_draft_history` | 8,701 | draft history | 1947-2025 | **Used** via `getDraftYear`. |
| 87 | `analytics_draft_value` | 8,658 | `overall_pick, career_gp, career_pts, career_ppg/rpg/apg, career_fg_pct, career_fg3_pct, seasons_played, first_season, last_season` | 1947-2025 | **NOT exposed to UI** — perfect base for a Draft Value / Career Success view. |
| 88 | `fact_player_fantasy_profile_season_avg` | 8,292 | `fan_duel_pts, nba_fantasy_pts` per season | 1996-2026 | NOT exposed to UI. |
| 89 | `fact_player_awards` | 7,238 | `player_id, season, month, week, award_type, subtype1/2/3, description, all_nba_team_number` | 1950-2026 | **Used** via `getAwards` and player "Awards" section. |
| 90 | `fact_playoff_picture` | 6,949 | current playoff picture per conference (rank, clinched_*, gb, gr_over/under_500) | 2002-2026 | **NOT exposed to UI** (standings exist but no "playoff picture" or "playoff odds" view). |
| 91 | `fact_player_headline_stats` | 6,270 | `time_frame, pts, ast, reb, pie` | 1947-2026 | NOT exposed to UI. |
| 92 | `fact_static_players` | 6,692 | static player roster | 1946-2026 | NOT exposed to UI. |
| 93 | `dim_all_players` | 6,692 | wide player index | 1946-2026 | NOT exposed to UI. |
| 94 | `fact_player_awards_legacy_names` | 11,583 | legacy award name variants | 1950-2026 | NOT exposed to UI. |
| 95 | `fact_player_index` | 3,632 | player index (search) | 1946-2026 | NOT exposed to UI (`searchPlayers` uses `dim_player`/BBR scrapers instead). |
| 96 | `agg_player_career` | 5,060 | career totals (per-player) | 1946-2026 | **Used** in player "Summary" via `getPlayerProfile.career`. AGENTS.md warns: corrupted for at least some players (Wes Unseld verified wrong). |
| 97 | `fact_franchise_leaders` | 43 | franchise PTS/AST/REB/BLK/STL leaders | 1946-2026 | **NOT exposed to UI**. Single row per franchise with all five leaders. |
| 98 | `dim_coach` | 27 | `coach_id, team_id, season_year, first_name, last_name, coach_type, is_assistant` | 1946-2026 | **Used** via `getTeamCoachHistory` (the BBR scrape + `dim_coach` join). |
| 99 | `fact_team_season_summary` | 1,672 | `gp, avg_pts/reb/ast, fg/fg3/ft_pct, wins/losses/win_pct, conference_rank, division_rank` | 1996-2026 | NOT exposed to UI (team profile uses `agg_team_season`/`fact_team_game_log` instead). |
| 100 | `agg_team_defense` | 1,672 | team defensive metrics | 1996-2026 | **Used** via `getTeamOpponentStats`. |
| 101 | `agg_team_franchise` | 43 | `years, games, wins, losses, win_pct, po_appearances, div_titles, conf_titles, league_titles, franchise_age_years` | 1946-2026 | NOT exposed to UI (would be perfect for a "Franchise History" / "Dynasties" view). |
| 102 | `agg_team_season` | 1,402 | team season rollup | 1996-2026 | **Used** via `getTeamProfile.seasons`. |
| 103 | `fact_team_season_ranks` | 1,402 | team PTS/REB/AST/OPP_PTS ranks | 1996-2026 | **Used** via `getTeamRanks`. |
| 104 | `analytics_team_season_summary` | 2,644 | team season summary | 1946-2026 | NOT exposed to UI (but is the source for `dim_team.conference`/`division` which are otherwise NULL — see §6). |
| 105 | `analytics_team_general_splits` | 5,288 | `split_type='Location', group_value='Home'/'Away'` | 1946-2026 | Splits by Location only. NOT exposed to UI. |
| 106 | `analytics_team_game_complete` | 147,059 | full team-game row | 1996-2026 | NOT exposed to UI. |
| 107 | `fact_box_score_summary_v3_game_summary` | 58,190 | per-game summary v3 | 1996-2026 | NOT exposed to UI. |
| 108 | `fact_box_score_summary_v3` | 58,190 | full box score v3 | 1996-2026 | NOT exposed to UI. |
| 109 | `dim_season_week` | 35 | season week dim | 1946-2026 | NOT exposed to UI. |
| 110 | `fact_shot_chart_league_averages` | 32 | league shot averages | 1996-2026 | NOT exposed to UI (player "Shooting by zone" joins `agg_shot_zones` to `fact_shot_chart_league` instead). |
| 111 | `dim_play_event_type` | 14 | event type dim | — | NOT exposed to UI. |
| 112 | `dim_shot_zone` | 17 | shot zone dim | — | NOT exposed to UI. |
| 113 | `dim_team` | 72 | `team_id, abbreviation, full_name, city, state, arena, year_founded, conference, division` | **conference and division are NULL for all 72 rows** | The UI sources conference/division from `analytics_team_season_summary` instead — see §6. |
| 114 | `dim_team_history` | 40 | team historical dim | 1946-2026 | NOT exposed to UI. |
| 115 | `dim_team_season` | 1,818 | team-season dim | 1946-2026 | NOT exposed to UI (used internally by `dim_team_history`). |
| 116 | `dim_season_phase` | 8 | phase dim | — | NOT exposed to UI. |
| 117 | `dim_defunct_team` | 15 | defunct teams dim | 1946-1976 | NOT exposed to UI. |
| 118 | `dim_player_sk` (logical) | 17,229 | see `dim_player` | — | (see row 78) |
| 119 | `agg_all_time_leaders` | 4,888 | `player_name, pts, ast, reb, pts_rank, ast_rank, reb_rank` | 1946-2026 | **NOT exposed to UI** — perfect base for All-Time Career Leaders tab. |
| 120 | `bridge_player_team_season` | 56,676 | bridge: player↔team↔season | 1946-2026 | **Used** in player profile season join and `getTeamRoster`. |

### Empty tables (warehouse gaps)

| Table | Rows | Note |
|---|---|---|
| `fact_team_splits` | 0 | Already documented in `AGENTS.md` as a warehouse-build gap. |
| `fact_team_matchups` | 0 | Same. |
| `fact_team_lineups_overall` | 0 | Same. |
| `fact_team_matchups_detail` | 0 | Same. |
| `fact_team_matchups_shot_detail` | 0 | Same. |
| `fact_team_general_splits_detail` | 0 | Same. |
| `fact_team_shooting_splits_detail` | 0 | Same. |
| `fact_team_pt_shots_detail` | 0 | Same. |
| `fact_team_pt_reb_detail` | 0 | Same. |
| `fact_team_lineups_detail` | 0 | Same. |
| `fact_team_pt_tracking` | 0 | Same. |
| `fact_team_player_dashboard` | 0 | Same. |
| `fact_team_game_hustle` | 0 | Same. |
| `fact_team_retired` | 0 | Same. |
| `fact_team_hof` | 0 | Same. |
| `fact_team_background` | 27 (NOT 0) | listed in dim_team context but actually 27 rows. |
| `fact_team_social_sites` | 0 | Same. |
| `fact_gravity_leaders` | 0 | Same. |
| `fact_hustle_availability` | 0 | Same. |
| `fact_homepage` | 0 | Homepage leaders table not yet populated. |
| `fact_homepage_detail` | 0 | Same. |
| `fact_homepage_leaders` | 0 | Same. |
| `fact_homepage_leaders_detail` | 0 | Same. |

**Aggregate count:** 21 tables are present in schema but contain 0 rows, matching the AGENTS.md-known "warehouse-build gap, not fixable from this repo" issue.

### Date-range anomalies

- **`game`** (raw): 1946-11-01 → 2023-06-12 (225 distinct season_ids). Stops in mid-2023. **`dim_game`** mirrors this gap.
- **`fact_standings`**: covers 30 seasons through 2025-26 (used by standings tab).
- **`fact_player_game_log`** / `fact_player_game_*`: 1996-97 onward (tracking era). Pre-1996 player game logs absent.
- **`analytics_*` and `agg_player_*` tables**: typically 1996-2026 (tracking-era aggregates); `analytics_player_general_splits` and `analytics_head_to_head` extend back to 1946.
- **`fact_player_awards`**: 1950-2026, 7,238 rows.
- **`fact_season_leader`**: 1947-2025, 11,154 rows. **Stat keys with full 79-season coverage:** `pts, ast, reb, stl, blk, fg, fga, fg3m, fg3a, ft, fta, gp, pf, ws, ows, dws, per, ts%, efg%, fg%, 3p%, ft%, 2p, 2pa, 2p%, trb, min`. **Tracking-era-only stat keys (52 seasons):** `ortg, drtg, obpm, dbpm, bpm, vorp, usg%, tov%, pace, tpar, tpp`. **Recent stat keys (29 seasons):** `pm100, ba, onOff100`.
- **`fact_player_season_ranks`**: 1946-2026 (4,888 players, 24 ranks each).
- **`fact_player_estimated_metrics`**: 1996-2026 (2,860 players). Duck's "Estimated Advanced" pre-tracking-completion.
- **`analytics_player_general_splits`**: only **Home/Away Location splits**. Despite the column structure (`split_type`, `group_set`, `group_value`) supporting arbitrary splits (W/L, Conf/Div, Month, Pre/Post ASG, etc.), only `Location` is populated.

### Categorical values for UI filters

| Filter | Distinct values | Source |
|---|---|---|
| Conference | 2 (East, West) | `analytics_team_season_summary`, `fact_standings` |
| Division | 6 (Atlantic/Central/Southeast/Northwest/Pacific/Southwest) | `analytics_team_season_summary` |
| Position | G, F, C (with compound G-F, F-C) | `dim_player.position` |
| Season type | 3 (Regular Season, Playoffs, Cup) | `fact_standings.season_type`, `fact_player_game_log.season_type` |
| Season year | 30 (1996-97 to 2025-26 in standings; 79 in `fact_season_leader`) | `fact_standings`, `fact_season_leader` |
| Country | 81 distinct | `dim_player.country` |
| Award type | 10 (All-Defense, All-NBA, All-Rookie, MVP, DPOY, ROY, SMOY, MIP, etc.) | `fact_player_awards.award_type` |

---

## Website Inventory

| Page/Route | Data Shown | Filters / Sorts | API Endpoints | Issues |
|---|---|---|---|---|
| **Home** (`#home`) | Featured player card + 30 teams grouped by conference | None | `GET /api/players/featured`, `GET /api/teams/by-conference` | (1) Featured player photo returns 404 — `Raymond Brown` (`/api/players/76282/photo`) is a known test-data player without a stored headshot. (2) Homepage is minimal — `fact_homepage*` tables are all empty so the planned "league leaders on the home page" feature cannot launch until those are populated. |
| **Players** — search/curated list (`#players`) | 12 curated players (no in-tab search; global header search is the only search) | Position · Team label only | `GET /api/players?q=` | (1) Per AGENTS.md, no per-tab search. (2) `GET /api/players?q=""` returns a small server-capped curated list — there's no way for a user to paginate or sort the full player base without using the global search. |
| **Players** — profile (`#players/:id`) | Bio (BBR-style header), Career Summary, Recent Games, Season Stats (Per-Game/Per-36/Per-100/Advanced tabs × Regular/Playoffs), Career Highs, Awards, Shooting by Zone (By Season / All Seasons Chart tabs), On/Off Court Splits, Similar Players | Jump-nav anchors; in-place tabs | `GET /api/players/:id`, `:highs`, `:recent-games`, `:rates`, `:advanced`, `:per100`, `:shot-splits`, `:on-off`, `:combine`, `:similar`, `:photo` | (1) No league-rank badges even though `fact_player_season_ranks` is available. (2) No Estimated Advanced Metrics even though `fact_player_estimated_metrics` covers 2,860 players since 1996. (3) No Fantasy profile even though `fact_player_fantasy_profile_season_avg` covers 2,860 players. (4) "All Seasons Chart" tab for shooting is supposedly rendered as a chart; the snapshot shows tabular data only — verify chart rendering works. (5) No "Compare with..." action despite the similar-players list. (6) Historical players show 404 on `/photo`. |
| **Teams** — list (`#teams`) | 12 curated teams (no in-tab search; global header search only) | Position · Conference (grouped) | `GET /api/teams?q=` | (1) Only 12 teams shown by default — the conference grouping on Home is the actual list of 30 franchises. (2) Defunct teams absent from any list (15 rows in `dim_defunct_team` ignored). |
| **Teams** — profile (`#teams/:id`) | Season-by-season, Recent Games, Current Roster, Coaching History, Playoff Series by Season, Most-Used Lineup Outings, League Ranks, Opponent Four-Factors | Anchor jump-nav; in-place year grouping | `GET /api/teams/:id`, `:roster`, `:playoff-series`, `:coaches`, `:lineups`, `:ranks`, `:opponent-stats` | (1) No franchise history / all-time franchise leaders panel (43 franchises × 5 leaders are in `fact_franchise_leaders` ready). (2) No "Head-to-Head" tab (`analytics_head_to_head` covers 77,552 matchup-seasons). (3) No "Splits" tab (only `Location` splits are populated). (4) No coach detail page (27 coaches in `dim_coach`). (5) Roster is current-only (per AGENTS.md known approach). (6) Logo is fetched from `cdn.nba.com/logos/nba/{team_id}/global/L/logo.svg` — works in test but external dependency. |
| **Standings** (`#standings`) | East + West tables for selected season + type | Season dropdown (2025-26, 2024-25, 2023-24, 2022-23, 2021-22, ...), Season Type (Regular / Playoffs) | `GET /api/standings/seasons`, `GET /api/standings?season=&type=` | (1) No "Playoff Picture" view (`fact_playoff_picture` has current conference bracket, clinch flags, games-behind). (2) Standings data covers 30 seasons — wider range than the UI dropdown displays (UI shows 5). (3) No "Cup" standings view surfaced (fact_standings has 30 `Cup` rows). |
| **Draft & Awards** (`#draft-awards`) | Draft picks per year (pick, round, player, team, school); Awards per season (player, award type, detail) | Year dropdown (1947-2025, 77 years), Awards season dropdown (1950-2026, 77 seasons), Award Type filter (10 types) | `GET /api/draft/years`, `GET /api/draft?season=`, `GET /api/awards/seasons`, `GET /api/awards/types`, `GET /api/awards?season=&type=` | (1) No draft career value / "best value picks" view (`analytics_draft_value` is purpose-built for this). (2) No combine stats drill-down (player profile includes combine; team-level combine leaderboards absent). (3) No "End of Season Teams" All-Star view (`stg_bref_end_of_season_teams` has 2,222 rows; `stg_bref_all_star_selections` has 2,058). |
| **Search Results** (`#search`, hidden) | List of players/teams matching the global header-search query | Global header textbox, debounced | Same as Players/Teams search | Reached only via `navigateToDetail('search', id)`. No tab bar entry (per AGENTS.md). |

### API endpoints exercised by the UI (29 routes)

```
GET /api/players/featured
GET /api/players                   (search, q="")
GET /api/players/:id
GET /api/players/:id/photo
GET /api/players/:id/highs
GET /api/players/:id/recent-games
GET /api/players/:id/rates
GET /api/players/:id/advanced
GET /api/players/:id/per100
GET /api/players/:id/shot-splits
GET /api/players/:id/on-off
GET /api/players/:id/combine
GET /api/players/:id/similar
GET /api/teams/by-conference
GET /api/teams                      (search, q="")
GET /api/teams/:id
GET /api/teams/:id/roster
GET /api/teams/:id/playoff-series
GET /api/teams/:id/coaches
GET /api/teams/:id/lineups
GET /api/teams/:id/ranks
GET /api/teams/:id/opponent-stats
GET /api/standings/seasons
GET /api/standings
GET /api/draft/years
GET /api/draft
GET /api/awards/seasons
GET /api/awards/types
GET /api/awards
```

### Console errors observed (7 total, all 404 photo loads)

| Request | Player | Tab |
|---|---|---|
| `GET /api/players/76282/photo` | Raymond Brown (featured player) | Home |
| `GET /api/players/920/photo` | A.C. Green | Players list |
| `GET /api/players/1920/photo` | A.J. English | Players list |
| `GET /api/players/2062/photo` | A.J. Guyton | Players list |
| `GET /api/players/78627/photo` | AJ Hammons | Players list |
| `GET /api/players/76672/photo` | (unknown player) | Players list |
| `GET /api/players/201985/photo` | (unknown player) | Players list |

All other 22 API calls returned 200/304.

---

## Coverage Matrix

| DB Domain | UI Presence | Route | Coverage Level | Gaps | Priority |
|---|---|---|---|---|---|
| **Player bio** (`dim_player`) | ✅ Full | Player profile header | High | None material. | — |
| **Player season stats** (`fact_player_season_stat_resolved`) | ✅ Full | Player profile "Season stats" | High | None. | — |
| **Player advanced** (`agg_player_season_advanced`) | ✅ Full | Player profile "Advanced" tab | High | Could surface more columns (VORP, PIE, WS/48, pace). | LOW |
| **Player per-36 / per-100** (`agg_player_season_per36`/`per48`) | ✅ Full | Player profile tabs | High | None. | — |
| **Player rates** (`fact_player_game_advanced`) | ✅ Full | Player profile "Per Game" | High | None. | — |
| **Player recent games** (`fact_player_game_log`) | ✅ Full | Player profile "Recent" | High | No "clutch-only" / "by quarter" filter. | LOW |
| **Player career highs** (`fact_player_game_log` self-join) | ✅ Full | Player profile "Highs" | High | Triple-doubles, 4/5-by-5 (4pts/4reb/4ast/4stl/4blk in 5 min) absent. | LOW |
| **Player awards** (`fact_player_awards`) | ✅ Full | Player profile + Standalone Awards tab | High | Could filter awards by award_type (only all_defense/all_nba/all_rookie surfaced in dropdown). | LOW |
| **Player shot splits** (`agg_shot_zones`) | ✅ Full | Player profile "Shooting" | High | Per-zone shot CHART is missing — currently tabular; raw 6.5M-shot heat map data is unused. | MEDIUM |
| **Player on/off** (`agg_on_off_splits`) | ✅ Full | Player profile "On/Off" | High | "Wowy" multi-player combos absent; data is single-player only. | MEDIUM |
| **Player combine** (BBR-scraped / `fact_player_draft_combine`) | ✅ Full | Player profile "Combine" | High | Only shown for own player; no "best combine" leaderboard. | LOW |
| **Similar players** (computed) | ✅ Full | Player profile "Similar" | High | One-way — no player-vs-player comparison view. | MEDIUM |
| **Player league ranks** (`fact_player_season_ranks`) | ❌ None | — | None | Could be a rank badge on the bio header + per-stat column on season stats. | MEDIUM |
| **Player headline stats** (`fact_player_headline_stats`) | ❌ None | — | None | "Hot/cold player this week" widget on Home. | LOW |
| **Player estimated advanced** (`fact_player_estimated_metrics`) | ❌ None | — | None | Distinct from `agg_player_season_advanced` — Duck's "Estimated" pre-tracking metrics. Could surface for pre-tracking-era accuracy. | LOW |
| **Player fantasy profile** (`fact_player_fantasy_profile_season_avg`) | ❌ None | — | None | FanDuel/NBA Fantasy pts as a tab on player profile. | LOW |
| **Player career** (`fact_player_career` BBR canonical) | ⚠️ Partial | Implicit via `agg_player_career` (corrupt) | Low | Replace `agg_player_career` with `fact_player_career` to fix the documented Wes-Unseld-style bug. | HIGH |
| **Player BBR-proprietary** (`fact_bref_player_season_advanced`/`shooting`/`per100`/`per36`/`totals`/`play_by_play`) | ⚠️ Partial | Resolved into `fact_player_season_stat_resolved` for season stats | Medium | Distinct BBR-proprietary metrics (PER, WS, VORP) currently surface, but the **shooting** BBR table (`fact_bref_player_season_shooting`, 18,254 rows) is unused — only `agg_shot_zones` is exposed. | LOW |
| **Team season stats** (`fact_team_season_summary`/`analytics_team_season_summary`/`agg_team_season`) | ✅ Full | Team profile "Season by season" | High | None. | — |
| **Team recent games** (`fact_team_game_log`) | ✅ Full | Team profile "Recent games" | High | No "by opponent" breakdown. | LOW |
| **Team roster** (`bridge_player_team_season`/`dim_player`) | ✅ Full | Team profile "Roster" | High | Per AGENTS.md known limitation: current-roster only. | — |
| **Team coaches** (BBR-scraped + `dim_coach`) | ✅ Full | Team profile "Coaching history" | High | No coach detail page; no "coaching tree" view. | MEDIUM |
| **Team playoff series** (`fact_playoff_series`) | ✅ Full | Team profile "Playoffs" | High | Per AGENTS.md the `wins`/`losses` columns are unreliable and re-derived from `game.wl_home`. | — |
| **Team lineups** (`agg_lineup_efficiency`) | ✅ Full | Team profile "Most-used lineup outings" | High | Could add "by net rating" sort and per-lineup shot-zone splits. | LOW |
| **Team league ranks** (`fact_team_season_ranks`) | ✅ Full | Team profile "League ranks" | High | None. | — |
| **Team opponent four factors** (`fact_box_score_four_factors_team`) | ✅ Full | Team profile "Opponent four-factors" | High | Could pair with the team's own four factors for net view. | LOW |
| **Team head-to-head** (`analytics_head_to_head`) | ❌ None | — | None | Dedicated tab on team profile. | MEDIUM |
| **Team splits** (`analytics_team_general_splits`) | ❌ None | — | None | Home/Away only, but a team-level Home/Away panel is still missing. | MEDIUM |
| **Team franchise history** (`agg_team_franchise`, `fact_franchise_players`) | ❌ None | — | None | "Franchise History" tab: championships, titles, all-time wins. | HIGH |
| **Franchise leaders** (`fact_franchise_leaders`) | ❌ None | — | None | "All-Time Franchise Leaders" panel: PTS/AST/REB/BLK/STL leaders with career totals. | HIGH |
| **All-time career leaders** (`agg_all_time_leaders`) | ❌ None | — | None | "All-Time Leaders" tab: PTS, AST, REB, BLK, STL career totals. | HIGH |
| **Season league leaders** (`fact_season_leader`) | ❌ None | — | None | **Single biggest miss.** 60+ stat_keys × 79 seasons, perfectly tabulated. League Leaders view (BBRef-style) is the canonical first-page view of any NBA stats site. | HIGH |
| **Standings** (`fact_standings`) | ✅ Full | Standings tab | High | Could surface "Cup" standings view (30 rows present). | LOW |
| **Playoff picture** (`fact_playoff_picture`) | ❌ None | — | None | Conference bracket with clinch flags, games-behind, magic numbers. | MEDIUM |
| **Draft** (`fact_draft_history`/`fact_draft`) | ✅ Full | Draft tab | High | No "draft value" / "career success by pick" view (data: `analytics_draft_value`). | MEDIUM |
| **Draft combine** (`fact_draft_combine_*`) | ⚠️ Partial | Player profile "Combine" (own player only) | Medium | No combine leaderboards (best vertical, sprint, etc.). | LOW |
| **Awards** (`fact_player_awards`) | ✅ Full | Awards tab + Player profile | High | None. | — |
| **All-Star selections** (`stg_bref_all_star_selections`/`stg_bref_end_of_season_teams`) | ❌ None | — | None | All-Star game history view. | LOW |
| **League game finder** (`fact_league_game_finder`, 30,000 rows) | ❌ None | — | None | "Find any NBA game" query box by date / team / player. | LOW |
| **Betting lines / odds** (`fact_game_betting_lines`, `fact_live_odds`, `nba_main_lines`, `nba_detailed_odds`) | ❌ None | — | None | Pre-game + live odds view. | LOW |
| **PBP events** (`fact_pbp_events`, `fact_play_by_play`, `play_by_play`) | ❌ None | — | None | Play-by-play play viewer / event timeline. | LOW |
| **Defunct teams** (`dim_defunct_team`, 15 rows) | ❌ None | — | None | "Defunct teams" view. | LOW |
| **Coach detail** (`dim_coach`, 27 rows) | ❌ None | — | None | Clickable coach name → coaching record across teams. | MEDIUM |
| **Player-index search** (`fact_player_index`, 3,632 rows) | ⚠️ Partial | (used internally for some lookups) | Medium | Currently uses `dim_player` + BBR scrapers for `searchPlayers`/`searchTeams`. `fact_player_index` is unused. | LOW |
| **Rolling averages** (`agg_player_rolling`, 1,557,218 rows) | ❌ None | — | None | Pre-computed pts/reb/ast rolling-5/10/20 on player profile. | LOW |
| **Shot chart heat map** (`fact_shot_chart` 6.5M + `analytics_shooting_efficiency` 6.5M) | ❌ None | — | None | Visual heat map (x/y) on player profile. **Highest-impact missing visualization.** | HIGH |
| **Player draft-year / draft-pick splits** (`analytics_draft_value` group by round/pick) | ❌ None | — | None | "Best draft picks of all time" ranked by career stats. | MEDIUM |
| **Advanced analytics: estimated metrics** (`fact_player_estimated_metrics`) | ❌ None | — | None | Duck's "Estimated Advanced" — pre-tracking-era equivalent. | LOW |
| **Empty warehouse tables** (`fact_team_splits`, `fact_team_matchups`, etc.) | n/a | n/a | n/a | Per AGENTS.md, not fixable from this repo. | — |

---

## Missing Data and Views

### Missing views (high impact)

1. **League Leaders** — `fact_season_leader` (11,154 rows × 60+ stat_keys × 79 seasons) is purpose-built for this view. The user's first instinct on any NBA stats site is "who leads the league in PPG this year?" — currently they can't ask that.
2. **All-Time Career Leaders** — `agg_all_time_leaders` (4,888 players) covers career PTS/AST/REB ranks. A classic "Top 50 scorers of all time" page.
3. **Franchise Leaders** — `fact_franchise_leaders` (43 franchises × 5 leaders) plus `fact_franchise_players` (19,275 player-team career rows) covers "Lakers all-time scoring leaders" perfectly.
4. **Franchise History** — `agg_team_franchise` covers championships, division/conference titles, total wins.
5. **Shot Chart Heat Map** — `fact_shot_chart` (6.5M) and `analytics_shooting_efficiency` (6.5M with `loc_x`/`loc_y`) provide everything needed. Current player "Shooting" tab is tabular only.
6. **Head-to-Head Matchups** — `analytics_head_to_head` (77,552 matchup-seasons) covers "Lakers vs Celtics all-time".
7. **Draft Value** — `analytics_draft_value` (8,658 picks) covers "best career value by draft position".
8. **League Rank Badges** — `fact_player_season_ranks` (39,807 season-rows) for rank-in-league glyphs on player profile.
9. **Player-Player Comparison** — `getSimilarPlayers` returns one-way list; a side-by-side compare view (Luka vs Trae) is the obvious next step.
10. **Coach Detail Page** — `dim_coach` (27 coaches) supports a clickable-coach profile with career record.
11. **Playoff Picture** — `fact_playoff_picture` (6,949 rows) with conference bracket, clinch flags, games-behind.
12. **Defunct Teams** — `dim_defunct_team` (15 rows) — "Team history" view.

### Missing views (lower impact)

13. **Betting / Odds** — `fact_game_betting_lines` (29,522), `fact_live_odds` (513,913), `nba_main_lines` (8,037), `nba_detailed_odds` (148,042).
14. **All-Star Selections** — `stg_bref_all_star_selections` (2,058), `stg_bref_end_of_season_teams` (2,222), `stg_bref_end_of_season_teams_voting` (4,484).
15. **Game Finder** — `fact_league_game_finder` (30,000 games) for "show me all games where Player X scored 40+".
16. **Defunct franchises view** — see row 12 above.
17. **Estimated Advanced metrics on player profile** — `fact_player_estimated_metrics`.
18. **Fantasy profile on player profile** — `fact_player_fantasy_profile_season_avg`.
19. **Rolling 5/10/20 on player profile** — `agg_player_rolling`.
20. **Headline stats widget on Home** — `fact_player_headline_stats`.

### Missing data filters

- **Position filter on Players list** — `dim_player.position` exists (G/F/C) but Players list view doesn't filter by it.
- **Conference filter on Teams list** — same: `dim_team.conference` is NULL; teams ARE grouped by conference on Home but Players tab isn't filterable by conference.
- **Active vs. all-time** — the players list uses `searchPlayers('')` which presumably returns active; there's no toggle for retired players in the curated list.
- **Era filter** — no way to constrain player search to a season/year range.

---

## API/UI Gaps

1. **No `/api/leaders/*` endpoints** despite `fact_season_leader` being one of the most natural warehouse queries.
2. **No `/api/players/:id/rank`** — `fact_player_season_ranks` is the source.
3. **No `/api/players/:id/estimated`** — `fact_player_estimated_metrics` is the source.
4. **No `/api/players/:id/fantasy`** — `fact_player_fantasy_profile_season_avg` is the source.
5. **No `/api/players/:id/rolling`** — `agg_player_rolling` is the source.
6. **No `/api/players/:id/comparison?other=:id`** — only one-way `similar` exists.
7. **No `/api/teams/:id/head-to-head?opponent=:id`** — `analytics_head_to_head` is the source.
8. **No `/api/teams/:id/franchise`** — `agg_team_franchise` + `fact_franchise_leaders` + `fact_franchise_players` are the sources.
9. **No `/api/teams/:id/splits`** — `analytics_team_general_splits` is the source (Home/Away only).
10. **No `/api/coaches/:id`** — `dim_coach` is the source.
11. **No `/api/draft/value`** — `analytics_draft_value` is the source.
12. **No `/api/standings/playoff-picture`** — `fact_playoff_picture` is the source.
13. **No `/api/odds/*`** — `fact_game_betting_lines` is the source.
14. **No `/api/players/:id/shot-chart?season=&zone=`** — `analytics_shooting_efficiency` is the source.
15. **No `/api/players/:id/shooting/on-off`** — split shooting by on/off court state (the data may not exist; would need to derive from `fact_player_game_advanced` joined to `fact_shot_chart`).
16. **No league leaderboards endpoint** — `fact_season_leader` and `agg_all_time_leaders` are sources.
17. **`dim_team.conference/division` are NULL** — the UI sources these from `analytics_team_season_summary` instead. Worth flagging because if a future feature needs `dim_team.conference` directly, it will silently fail.
18. **No per-tab search on Players/Teams** — per AGENTS.md design decision, but the gap means the global search is the only way to discover non-curated players/teams. Note: `GET /api/players?q=` and `GET /api/teams?q=` DO support a search string, but the Players/Teams list views do not pass one — they call with `q=""` which triggers the curated-default cap.

### Frontend (rendering) gaps

- **No chart/visualization library** — no D3, Chart.js, or SVG-rendering of shot charts. The "All Seasons Chart" tab for shooting is presumably a table despite the label.
- **No image-rendered shot map** — `loc_x`/`loc_y` from `analytics_shooting_efficiency` would render natively with a simple SVG `<circle>` overlay.
- **No player-vs-player side-by-side** — the data structure for "seasons" returned by `getPlayerProfile` would support it.
- **No coach name link** in team "Coaching history" — coaches are not clickable.
- **No "Back to roster" navigation** after clicking a player from a team roster — the user lands on the player profile and would need to manually navigate.

---

## Web Research Inspiration

Sources scraped via Firecrawl (`firecrawl_search` + `firecrawl_scrape`) for the `librarian` subagent (`ses_0de679043ffe87Km8bR1in8MAj`). Each source returned a concrete "applicable / not applicable" verdict that fed the recommendations in §10.

| Source | URLs scraped | Top patterns found | Applicability to current DB |
|---|---|---|---|
| **Basketball-Reference** (`basketball-reference.com`) | Homepage, 2025-26 Per-Game, 2025-26 Advanced, 2025-26 League Summary (Four Factors), Nikola Jokić profile, Glossary | Per-game/per-36/per-100 toggleable table; Advanced stats table (PER/WS/BPM/VORP); Four Factors team table; Player profile with season-stats timeline; League Leaders | **Fully applicable.** The warehouse already has all columns needed via `fact_player_season_stat_resolved` + `agg_player_season_advanced` + `fact_box_score_four_factors_team`. |
| **StatMuse** (`statmuse.com/nba`) | Homepage, "Most Points in a Triple Double" results page, Examples, Player stats search | Natural-language search; rich answer cards with team colors + headshot; related-search suggestions; data glossary | **Limited.** NL-to-SQL is out of scope; "pre-baked query buttons" + glossary cards are easy wins. |
| **Cleaning the Glass** (`cleaningtheglass.com`) | Homepage, Stats landing (paywalled), public blog posts | Garbage-time filtering; Four Factors with percentile rankings; transition vs. halfcourt splits; estimated-position filtering; on/off impact | **Partially applicable.** Percentile ranking via `PERCENT_RANK()` window is trivial. Garbage-time/transition/halfcourt splits require PBP-grain data the UI does not currently query. On/off impact is already surfaced via `agg_on_off_splits`. |
| **Crafted NBA** (`craftednba.com`) | Homepage, Player Stats, Player Traits, Similar Season Finder | Multi-tab stat-type columns (Traditional/Advanced/Plus-Minus/Defense); compound sidebar filters; Player Traits cards; Doppelgänger / Similarity Finder; Roster Builder | **Fully applicable for: multi-tab columns, sidebar filters, traits, similarity.** Roster Builder is a stretch. The existing `getSimilarPlayers` is exactly the Doppelgänger Finder. |
| **PBP Stats** (`pbpstats.com`) | Homepage, API Swagger | Wowy stats; Four Factors on-off; Possession Finder; Assist Networks; Shot Zone Detail; Possession Length Distribution | **Limited.** Most PBP-grain patterns need possession data not exposed by the current app. The on/off pattern IS already covered. |

### Pattern → DB mapping (top 10 applicable)

| # | Pattern | Source | Warehouse Readiness |
|---|---|---|---|
| 1 | Stat-type toggle bar (Per Game / Per 36 / Per 100 / Advanced / Totals) | BBRef | ✅ `fact_player_season_stat_resolved` + `fact_player_game_advanced` + `agg_player_season_per36`/`per48` |
| 2 | Multi-tab stat columns (Traditional / Advanced / Plus-Minus / Defense) | Crafted NBA | ✅ Same as #1 |
| 3 | Percentile ranks on every stat (0-100 context) | CTG | ✅ `PERCENT_RANK()` window function in DuckDB |
| 4 | Season stat timeline (player profile with all years + advanced) | BBRef | ✅ Already partially shown; could surface more columns |
| 5 | Player Traits / Category buttons (pre-sorted: scorers, rebounders, passers, defenders) | Crafted NBA | ✅ Use `agg_player_season` + `agg_player_season_advanced` with composite sorts |
| 6 | Player Doppelgänger / Similarity Finder | Crafted NBA | ✅ `getSimilarPlayers` already exists; could surface in dedicated tab |
| 7 | Four Factors team table (team + opponent, with percentiles) | BBRef / CTG | ✅ `agg_team_season` + `agg_team_defense` + `fact_box_score_four_factors_team` |
| 8 | On/Off impact cards (ORtg/DRtg/NetRtg bar comparison) | PBP Stats | ✅ Already exposed via `getPlayerOnOffSplits` |
| 9 | Shot zone split table with league-average join | BBRef | ✅ `agg_shot_zones` + `fact_shot_chart_league` (already exposed); raw heat map pending |
| 10 | Pre-baked query buttons ("Best 3P%", "Triple-double leaders") | StatMuse | ✅ Wrap existing queries in clickable tiles |

### Patterns deferred (require PBP / external telemetry)

| Pattern | Source | Missing data |
|---|---|---|
| Transition vs. halfcourt splits | CTG | Possession-level PBP |
| Garbage-time filtering | CTG | PBP grain with clock context |
| Possession Finder | PBP Stats | PBP grain |
| Assist Networks | PBP Stats | Passer↔Scorer pair tracking (not in schema) |
| Multi-player "Wowy" combinations | PBP Stats | Multi-player on/off not in current schema |
| Win probability / leverage | PBP Stats | Win-probability model not in warehouse |

---

## Recommended New Views and Queries

### Basic (low complexity, high visibility)

**B1. League Leaders (Season + All-Time)**
```sql
-- Season leaders
SELECT season, stat_key, stat_value, person_id, player_name
FROM fact_season_leader
WHERE season = '2025-26' AND stat_key = 'pts'
ORDER BY stat_value DESC LIMIT 25;

-- All-time career leaders
SELECT player_name, pts, ast, reb, pts_rank, ast_rank, reb_rank
FROM agg_all_time_leaders
WHERE pts_rank <= 50 ORDER BY pts_rank;
```

**B2. All-Time Franchise Leaders**
```sql
SELECT team_id, pts_player AS pts_leader, pts, ast_player AS ast_leader, ast,
       reb_player AS reb_leader, reb, blk_player AS blk_leader, blk,
       stl_player AS stl_leader, stl
FROM fact_franchise_leaders
WHERE team_id = ? ORDER BY team_id;
```

**B3. League Rank badges on Player Profile**
```sql
SELECT season_id, rank_pts, rank_ast, rank_reb, rank_stl, rank_blk, rank_fg_pct,
       rank_fg3_pct, rank_ft_pct, rank_eff
FROM fact_player_season_ranks
WHERE player_id = ? ORDER BY season_id DESC LIMIT 10;
```

**B4. Draft Value — Career Success by Pick**
```sql
SELECT round_number, round_pick, overall_pick, player_name, position, country,
       career_gp, career_ppg, career_rpg, career_apg, career_fg_pct, career_fg3_pct,
       seasons_played
FROM analytics_draft_value
WHERE round_number = 1
ORDER BY career_ppg DESC NULLS LAST LIMIT 50;
```

**B5. Franchise History tab**
```sql
SELECT team_city, team_name, years, games, wins, losses, win_pct,
       po_appearances, div_titles, conf_titles, league_titles,
       franchise_age_years
FROM agg_team_franchise ORDER BY league_titles DESC, wins DESC;
```

### Intermediate (medium complexity, requires design)

**I1. Head-to-Head Team Matchups**
```sql
SELECT opponent_abbr, season_year, games_played, wins, losses,
       avg_pts_scored, avg_pts_allowed, avg_margin
FROM analytics_head_to_head
WHERE team_id = ? ORDER BY season_year DESC, opponent_abbr;
```

**I2. Home/Away Splits (Player & Team)**
```sql
-- Player
SELECT group_value AS location, gp, w, l, w_pct, min, pts, reb, ast,
       fg_pct, fg3_pct, ft_pct, plus_minus, w_pct_delta, pts_delta
FROM analytics_player_general_splits
WHERE player_id = ? AND split_type = 'Location'
ORDER BY season_year DESC, season_type;

-- Team
SELECT group_value AS location, gp, w, l, w_pct, pts, reb, ast,
       fg_pct, fg3_pct, plus_minus
FROM analytics_team_general_splits
WHERE team_id = ? AND split_type = 'Location'
ORDER BY season_year DESC;
```

**I3. Player-vs-Player Comparison (side-by-side)**
```sql
-- Use existing getPlayerProfile + a "compare" endpoint that joins two profiles
-- for season stats, advanced, per36, per100, on/off, shot splits, similar.
```

**I4. Coach Detail Page**
```sql
SELECT coach_id, team_id, season_year, first_name, last_name,
       coach_type, is_assistant
FROM dim_coach
WHERE coach_id = ? ORDER BY season_year;

-- Career record aggregation
SELECT COUNT(DISTINCT season_year) AS seasons,
       SUM(CASE WHEN coach_type = 'Head' THEN 1 ELSE 0 END) AS head_seasons
FROM dim_coach WHERE coach_id = ?;
```

**I5. Estimated Advanced Metrics (pre-tracking-era accuracy)**
```sql
SELECT season_year, e_off_rating, e_def_rating, e_net_rating, e_pace,
       e_ast_ratio, e_oreb_pct, e_dreb_pct, e_reb_pct, e_tov_pct, e_usg_pct
FROM fact_player_estimated_metrics
WHERE player_id = ? ORDER BY season_year;
```

**I6. Player Traits / Category Buttons**
```sql
-- "Best scorers" tile: pre-sorted by career PPG
SELECT p.full_name, aps.career_pts, aps.career_ppg, aps.career_gp
FROM agg_player_career aps
JOIN dim_player p USING (player_id)
WHERE aps.career_gp >= 200
ORDER BY aps.career_ppg DESC LIMIT 25;

-- "Best 3-point shooters" tile: pre-sorted by career 3P%
-- (joins to BBR tables or fact_bref_player_season_totals summed)
SELECT player_name, SUM(fg3m)::DOUBLE / NULLIF(SUM(fg3a), 0) AS fg3_pct,
       SUM(fg3m) AS total_3pm, SUM(fg3a) AS total_3pa
FROM fact_bref_player_season_totals
GROUP BY player_name
HAVING SUM(fg3a) >= 1000
ORDER BY fg3_pct DESC LIMIT 25;
```

### Advanced (high complexity, requires novel visualization)

**A1. Shot Chart Heat Map**
```sql
-- Raw points (loc_x, loc_y in NBA-coords with origin at hoop)
SELECT loc_x, loc_y, shot_zone_basic, shot_zone_area, shot_zone_range,
       shot_distance, shot_made_flag, league_avg_fg_pct
FROM analytics_shooting_efficiency
WHERE player_id = ? AND season_year = '2025-26'
LIMIT 2000;

-- Aggregate bins for heat map (e.g. 5ft × 5ft grid)
SELECT
  FLOOR(loc_x / 5) * 5 AS bin_x,
  FLOOR(loc_y / 5) * 5 AS bin_y,
  COUNT(*) AS attempts,
  SUM(shot_made_flag) AS makes,
  AVG(shot_made_flag) AS fg_pct
FROM analytics_shooting_efficiency
WHERE player_id = ? AND season_year = ?
GROUP BY bin_x, bin_y ORDER BY bin_y, bin_x;
```

**A2. Play-by-Play Event Viewer** (if/when frontend is enhanced)
```sql
SELECT game_id, period, clock, home_team_abbreviation, visitor_team_abbreviation,
       home_score, visitor_score, neutral_description, score_margin
FROM fact_pbp_events
WHERE game_id = ? ORDER BY event_num;
```

**A3. Possession Finder** (requires PBP-derived possessions — currently unavailable)
```sql
-- Not implementable until warehouse adds a `possession` table.
-- Would need: start_type, end_type, duration, points_scored, lineup_on_floor
```

**A4. Game-by-Game Advanced Splits** (clutch, by quarter)
```sql
-- Clutch splits would need a `clutch_flag` column on fact_player_game_log
-- (rows where period >= 4 AND abs(score_margin) <= 5).
-- Current schema lacks this flag — would need to derive via JOIN.
```

**A5. Lineup Optimizer / Simulator** (uses `agg_lineup_efficiency`)
```sql
-- Given 5 player_ids, sum (5-man lineup efficiency) where all 5 are present.
-- Currently group_id is opaque; would need to know the lineup encoding.
SELECT * FROM agg_lineup_efficiency WHERE group_id IN (?, ?, ?) LIMIT 50;
```

**A6. Betting Lines / Odds**
```sql
SELECT game_id, game_date, sportsbook, spread, over_under, moneyline_home, moneyline_away
FROM fact_game_betting_lines
WHERE game_id = ? ORDER BY game_date DESC LIMIT 50;
```

**A7. Game Finder**
```sql
-- "Show me all games where Player X scored 40+"
SELECT g.game_id, g.game_date, g.matchup, pgs.pts
FROM game g
JOIN fact_player_game_log pgs USING (game_id)
WHERE pgs.player_id = ? AND pgs.pts >= 40
ORDER BY g.game_date DESC;
```

---

## Prioritized Roadmap

### Quick Wins (≤ 1 day each)

1. **League Leaders tab** — Read `fact_season_leader` and `agg_all_time_leaders`, add a new top-level tab "Leaders". **HIGH impact, LOW cost.** (See B1.)
2. **League Rank badges on Player Profile** — Surface `fact_player_season_ranks` as a small column on the existing season-stats table. **MEDIUM impact, LOW cost.** (See B3.)
3. **All-Time Franchise Leaders panel on Team Profile** — Surface `fact_franchise_leaders` + `agg_team_franchise`. **MEDIUM impact, LOW cost.** (See B2 + B5.)
4. **All-Time Career Leaders page** — Surface `agg_all_time_leaders`. **MEDIUM impact, LOW cost.** (See B1.)
5. **Head-to-Head tab on Team Profile** — Read `analytics_head_to_head`. **MEDIUM impact, LOW cost.** (See I1.)
6. **Home/Away Splits panel on Player Profile** — Read `analytics_player_general_splits`. **MEDIUM impact, LOW cost.** (See I2.)
7. **Estimated Advanced Metrics card on Player Profile** — Read `fact_player_estimated_metrics`. **LOW impact, LOW cost.** (See I5.)
8. **Replace `agg_player_career` with `fact_player_career`** in `getPlayerProfile` to fix the documented Wes-Unseld-style corruption. **HIGH impact, LOW cost.**
9. **Standings dropdown: surface all 30 seasons + Cup type.** **LOW impact, LOW cost.**

### Medium-Effort (1-3 days each)

10. **Draft Value / Career Success view** — New page reading `analytics_draft_value`. **MEDIUM impact.** (See B4.)
11. **Player-vs-Player comparison page** — Reuse existing endpoints, new layout. **MEDIUM impact.** (See I3.)
12. **Shot Chart Heat Map** — New endpoint serving binned `loc_x`/`loc_y` data from `analytics_shooting_efficiency`; simple SVG heat map component. **HIGH impact, MEDIUM cost.** (See A1.)
13. **Coach Detail page** — Clickable coach names from team "Coaching history"; new `/api/coaches/:id` endpoint reading `dim_coach`. **MEDIUM impact, MEDIUM cost.** (See I4.)
14. **Player Traits / Category tiles on Home** — Pre-sorted list of "Best Scorers", "Best Shooters", "Best Passers", etc. **MEDIUM impact, MEDIUM cost.** (See I6.)
15. **Fantasy Points / Rolling Averages on Player Profile** — Read `fact_player_fantasy_profile_season_avg` + `agg_player_rolling`. **LOW impact, MEDIUM cost.**
16. **Percentile ranks on every stat** — Wrap existing queries with `PERCENT_RANK() OVER (ORDER BY ...)`. **MEDIUM impact, MEDIUM cost.**

### Strategic Additions (3+ days each)

17. **Playoff Picture page** — `fact_playoff_picture` drives a live bracket view. **MEDIUM impact.**
18. **Defunct Teams page** — `dim_defunct_team` list + defunct-team records. **LOW impact, LOW cost.**
19. **All-Star Selections page** — `stg_bref_all_star_selections` + `stg_bref_end_of_season_teams`. **LOW impact.**
20. **Betting Lines view** — `fact_game_betting_lines` + `fact_live_odds`. **LOW impact (no live data refresh; static historical).**
21. **PBP event viewer** — Read-only viewer for `fact_pbp_events`. **LOW impact, HIGH cost** (requires timeline UI).
22. **Wowy multi-player combinations** — Would require deriving multi-player on/off joins (not currently in schema). **MEDIUM impact, HIGH cost.**
23. **Position filter on Players list** — Add a position dropdown (G/F/C). **LOW impact, LOW cost.**

---

## Appendix

### A. Inspection SQL used

```sql
-- Table inventory
SELECT table_name, table_type FROM information_schema.tables
WHERE table_schema = 'main' AND table_name NOT LIKE 'stg\_%' AND table_name NOT LIKE '\_%'
ORDER BY table_name;

-- Row counts
SELECT table_name, estimated_size FROM duckdb_tables()
WHERE schema_name='main' AND table_name NOT LIKE 'stg\_%' AND table_name NOT LIKE '\_%'
ORDER BY estimated_size DESC;

-- Columns for key tables
SELECT table_name, column_name, data_type FROM information_schema.columns
WHERE table_schema='main' AND table_name IN ('dim_player','agg_player_season_advanced',
  'agg_on_off_splits','fact_season_leader','fact_franchise_leaders',
  'analytics_player_general_splits','fact_player_season_ranks') ORDER BY table_name, ordinal_position;

-- Coverage (date ranges and row counts)
SELECT
  'game' AS t, COUNT(*) AS rows, MIN(game_date), MAX(game_date), COUNT(DISTINCT season_id)
  FROM game
UNION ALL SELECT 'fact_player_game_log', COUNT(*), NULL, NULL, NULL FROM fact_player_game_log
UNION ALL SELECT 'fact_standings', COUNT(*), NULL, NULL, NULL FROM fact_standings
-- ... etc per the DB Inventory table above
```

### B. Routes visited (chrome-devtools MCP)

| URL | Tab | API calls |
|---|---|---|
| `http://localhost:5173/` | Home | `/api/players/featured`, `/api/teams/by-conference` |
| `http://localhost:5173/` | Players list | `/api/players`, `/api/players/:id/photo` × 12 |
| `http://localhost:5173/` | Player profile (AJ Green, id 1631260) | `/api/players/:id` + 9 sub-resources |
| `http://localhost:5173/` | Teams list | `/api/teams`, `/api/teams/by-conference` |
| `http://localhost:5173/` | Team profile (Atlanta Hawks, id 1610612737) | `/api/teams/:id` + 7 sub-resources + `https://cdn.nba.com/logos/nba/1610612737/global/L/logo.svg` |
| `http://localhost:5173/` | Standings | `/api/standings/seasons`, `/api/standings?season=2025-26&type=Regular` |
| `http://localhost:5173/` | Draft & Awards | `/api/draft/years`, `/api/draft?season=2023`, `/api/awards/seasons`, `/api/awards/types`, `/api/awards?season=2026` |

### C. Network errors observed

- 7 × `GET /api/players/:id/photo` → 404 (historical players with no stored headshot).
- 0 × other 4xx/5xx.
- 1 × external request: `cdn.nba.com/logos/nba/{team_id}/global/L/logo.svg` → 200.

### D. Limitations of the audit

- **Read-only.** No schema exploration beyond `SELECT` against `information_schema` + `duckdb_tables()`. No DDL/DML.
- **Static queries only.** The audit reads the current DuckDB snapshot; it does not re-derive aggregates or compute fresh stats.
- **Web research via firecrawl** was performed by the `librarian` subagent (`ses_0de679043ffe87Km8bR1in8MAj`); the JSON output is the result of 10 successful scrapes across 5 source sites. Token and credit budgets were kept modest.
- **Header search / hidden Search Results tab** was not exercised because the global header search debounces on user typing and would require a scripted input flow; it is documented by `AGENTS.md` to dispatch a `nba:navigate` event with `tab: 'search'`.
- **The `/api/admin/query` SQL box** was not exercised (developer escape hatch; out of UI surface area).
- **The DB tables classified as "empty (warehouse gap)"** are present in schema with 0 rows; this is documented in `AGENTS.md` as a known warehouse-side gap and is not in scope for this app.
- **Photo fallback for historical players** — `web/server/photos.ts` returns 404 when no headshot PNG is on disk; this was not investigated further.

### E. Out of scope for follow-up audits

- Performance audit of queries (slow DuckDB SQL, missing indexes, etc.).
- Frontend accessibility audit (ARIA, keyboard nav, focus management).
- Visual design review (typography, spacing, motion).
- CI / lefthook / lint pipeline health.
- ETL/warehouse-build pipeline (`data/audit/*.sql` scripts).