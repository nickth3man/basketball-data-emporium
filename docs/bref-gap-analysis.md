# Gap Analysis: NBA Data Explorer vs. Basketball-Reference

## Methodology

Every observation below comes from live Chrome DevTools navigation of both sites.

- **Our site**: `http://localhost:5173` (Vite dev server running the repo's Express API + vanilla TS frontend).
- **Basketball-Reference**: `https://www.basketball-reference.com`.

Sampled pages:

- **Our player pages** (7): Jayson Tatum, LeBron James, Derrick White, Alex Caruso, Michael Jordan, Larry Bird, Kobe Bryant.
- **Our team pages** (2): Boston Celtics, Los Angeles Lakers.
- **Basketball-Reference player pages** (3): Jayson Tatum, LeBron James, Derrick White, Michael Jordan.
- **Basketball-Reference team pages** (3): Boston Celtics franchise index, Los Angeles Lakers franchise index, 2025-26 Lakers season page.

## 1. Scope of Our Site Today

### Player index

The Players tab is **search-only**. It shows an empty result list until the user types a name; there is no default alphabetical or browseable player directory. Search returns matching players with a small stat snippet (e.g., "24.3 PPG, 10.0 RPG, 6.3 APG").

### Team index

The Teams tab lists exactly the **30 current NBA franchises** with abbreviations (e.g., "Boston Celtics BOS"). No defunct teams, no ABA/NBA historical franchises, no browse-by-division beyond the filter box.

### Standings tab

A season + season-type selector plus East/West conference tables with W, L, PCT, GB, Home, Road.

### Draft & Awards tab

- Draft picks by year (Pick, Rd, Player, Team, From).
- Awards by season with an award-type filter. The filter contains both human labels ("All-Defense", "All-NBA") and raw warehouse codes ("all_defense", "all_nba", "nba dpoy", "nba mip", "nba mvp", "nba roy", "nba smoy").

## 2. Basketball-Reference Data Categories Catalog

### Player-page categories observed

From Tatum, LeBron, White, and Jordan pages, B-Ref consistently provides:

- Bio/vitals: full name, nicknames, positions, shooting hand, height/weight, birth date/place, draft info, college/high school.
- Photo/headshot.
- Last 5 Games (active players only).
- Per Game stats (season + career).
- Totals stats.
- Per 36 Minutes stats.
- Per 100 Possessions stats.
- Advanced stats: PER, TS%, 3PAr, FTr, ORB%, DRB%, TRB%, AST%, STL%, BLK%, TOV%, USG%, OWS, DWS, WS, WS/48, OBPM, DBPM, BPM, VORP.
- Adjusted Shooting: FG%, 2P%, 3P%, eFG%, FT%, TS% with league-adjusted values.
- Play-by-Play stats: position estimates, on-court +/-.
- Shooting splits: by distance, by zone, assisted%, dunk/layup counts.
- Game Highs (career and season).
- Playoffs Series log.
- All-Star Games.
- Similarity Scores.
- College Stats (when applicable).
- Salaries (career contract table).
- Awards & honors: All-Star, Championships, Weekly Awards, Monthly Awards, All-League, MVP Award Shares, All-NBA/All-Defensive/All-Rookie Voting Shares, Slam Dunk Contest, Three Point Shootout, Amateur Honors.
- Leaderboards: career/season ranks in points, rebounds, assists, etc.

### Team-page categories observed

From the Celtics/Lakers franchise pages and Lakers 2025-26 season page:

- Franchise logo.
- Franchise vitals: location, all team names (e.g., "Los Angeles Lakers, Minneapolis Lakers"), seasons span, all-time W-L record, playoff appearances, championships.
- All-Time Top 12 Players with photos.
- Year-by-Year table: W, L, W/L%, Finish, SRS, Pace, Rel Pace, ORtg, Rel ORtg, DRtg, Rel DRtg, Playoffs, Coaches, Top WS.
- Sub-pages: Team Stats, Team Stats Per Game, Opponent Stats, Opponent Stats Per Game, League Ranks, Year-over-Year, Season Leaders, Career Leaders, Players, Coaches, Executives, Contracts, All-Star Game, Hall of Fame, Draft, Uniform Numbers, Head-to-Head.
- Season page: Game Results, Roster, Assistant Coaches and Staff, Team and Opponent Stats, Team Misc, Per Game/Totals/Per 36/Per 100/Advanced/Adjusted Shooting/Shooting/Play-by-Play tables, Salaries, Players on League Leaderboards.

## 3. Page-by-Page Comparison

### Player pages

| Category | Our Site | B-Ref | Notes |
|---|---|---|---|
| Photo/headshot | Y | Y | Both show player photo. |
| Bio/vitals (position, Ht/Wt, DOB, country, school, draft) | Y | Y | B-Ref adds nicknames, shooting hand, birth place, more draft detail. |
| Career accolades summary | Y | Y | Our site lists badges; B-Ref has structured awards section. |
| Career summary stats | Y | Y | Both show G/PTS/TRB/AST/FG%/3P%/FT%/eFG%. |
| Season-by-season per-game | Y | Y | Our table is narrower (GP/PPG/RPG/APG/FG%). B-Ref has full per-game columns. |
| Season totals | **N** | Y | We only display per-game rates, not season totals. |
| Per 36 minutes | Y | Y | Both have it. |
| Per 48 minutes | Y | N/A | We show per 48; B-Ref uses per 100 possessions instead. |
| Per 100 possessions | **N** | Y | Modern advanced-rate view. |
| Advanced stats (PER, WS, BPM, VORP, etc.) | **N** | Y | Largest player-stat gap. |
| Adjusted shooting | **N** | Y | League-relative shooting metrics. |
| Play-by-play stats | **N** | Y | Position estimates, on/off splits. |
| Shooting by zone | Y (tracking-era players) | Y | Bird (pre-tracking) lacks this on both sites; ours omits some B-Ref distance splits. |
| On/off court splits | Y (tracking-era players) | Y | Same availability caveat as shooting zones. |
| Career/season game highs | Y | Y | Both have it. |
| Game log / last 5 games | **N** | Y | No per-game log on our site. |
| Playoffs series log | **N** | Y | Per-player playoff series results. |
| All-Star Games | **N** | Y | Our awards table mentions selections but not game stats. |
| Similar players | Y | Y | Both have it. |
| College stats | **N** | Y | Would need new data source. |
| Salaries / contracts | **N** | Y | No compensation data on our site. |
| Awards voting shares | **N** | Y | MVP/All-NBA/All-Defensive/All-Rookie voting shares. |
| Leaderboards (career/season ranks) | **N** | Y | No leaderboards on our site. |
| Transactions / injuries | **N** | Y | Not present. |

### Team pages

| Category | Our Site | B-Ref | Notes |
|---|---|---|---|
| Team logo | **N** | Y | We have no franchise logos. |
| Franchise vitals (location, names, record, titles) | Partial | Y | We show abbreviation, conference, division, arena, capacity, founded, owner, GM, coach. B-Ref adds all-time W-L%, playoff appearances, championships, all historical names. |
| Social media links | Y | N | We link Facebook/Instagram/Twitter; B-Ref does not. |
| Season-by-season team stats | Y | Y | Our table has GP/PPG/RPG/APG/FG%. B-Ref adds W/L, W/L%, Finish, SRS, Pace, ORtg, DRtg, Top WS. |
| Opponent stats | **N** | Y | No opponent perspective. |
| League ranks | **N** | Y | Not present. |
| Recent games | Y | Y | Our Celtics "Recent games" were dated 2023 (stale). |
| Current roster | Y | Y | Our Celtics roster appeared to contain players not on the current Celtics (data-quality issue). |
| Coaching history | Y | Y | Both extensive. |
| Playoff series by season | Y | Y | Both have it. |
| Lineup data | Y (single-game samples) | Y | We show "Most-used lineup outings"; B-Ref has richer lineup data. |
| All-Time Top Players | **N** | Y | No franchise all-time leader section. |
| Executives / owners | Partial | Y | We show owner/GM; B-Ref has dedicated executives page. |
| Contracts / salaries | **N** | Y | No salary data. |
| Draft history | **N** | Y | Draft tab has league-wide draft, not per-team draft history. |
| Uniform numbers / retired numbers | **N** | Y | Not present. |
| Head-to-head | **N** | Y | Not present. |
| Franchise history (e.g., Minneapolis Lakers) | **N** | Y | Our Lakers page begins in 1960; B-Ref includes Minneapolis era. |

## 4. Synthesized Gap List

### Missing entirely

1. **Player advanced statistics** — PER, WS, OWS/DWS, WS/48, BPM, OBPM/DBPM, VORP, ORB%/DRB%/TRB%, AST%, STL%, BLK%, TOV%, USG%.
2. **Per-100-possession player stats** — modern rate context.
3. **Player game logs / last 5 games** — per-game box scores.
4. **Player salaries / contracts** — career earnings table.
5. **College stats** — for players with NCAA careers.
6. **Playoff series player log** — per-player postseason series results.
7. **All-Star Game player stats** — game-by-game All-Star performances.
8. **Awards voting shares** — MVP/All-NBA/All-Defensive/All-Rookie shares.
9. **Leaderboards / career ranks** — where players rank all-time/seasonally.
10. **Team advanced stats** — SRS, Pace, ORtg, DRtg, Rel ORtg/DRtg, Team Misc.
11. **Opponent stats** — what opponents did against the team.
12. **Team league ranks** — ordinal league ranking per stat.
13. **Team contracts / salaries** — roster salaries and cap data.
14. **Team draft history** — picks by franchise.
15. **Uniform numbers / retired numbers**.
16. **Head-to-head records**.
17. **Team logos / franchise branding**.
18. **Full franchise history** — e.g., Minneapolis Lakers not represented on Lakers page.
19. **Executives page** — beyond owner/GM.
20. **Transactions / trades**.

### Present but incomplete

1. **Season-by-season tables** — only per-game rates; missing totals, W/L, finish, advanced team metrics.
2. **Awards display** — present but includes raw warehouse codes (`all_defense`, `nba mip`, etc.) and duplicate "Voting" rows that should be filtered or rolled into voting-share visuals.
3. **Player index** — search-only; no browseable directory.
4. **Team index** — only current 30 franchises; no historical/defunct teams.
5. **Shooting data** — present for tracking era but lacks B-Ref's distance/assisted/dunk-layup splits.

### Present but lower quality

1. **Recent games** — Celtics page showed 2023 dates; data is stale.
2. **Current roster** — Celtics roster contained players who are not Celtics (e.g., Anfernee Simons), indicating a join or scraper mapping issue.
3. **Awards table UX** — raw award slugs mixed with human labels; should be cleaned and deduplicated.
4. **Player search result snippets** — inconsistent formatting (sometimes stats, sometimes jersey numbers).
5. **Team vitals** — missing all-time record, playoff appearances, championships, historical names.

## 5. Prioritized Gap Summary

| Priority | Gap | User Value | Implementation Effort | Likely Data Source |
|---|---|---|---|---|
| **High** | Player advanced stats (PER, WS, BPM, VORP) | Very High | Medium | Warehouse `agg_player_season` likely already has many of these; verify schema and expose via API. |
| **High** | Per-100-possession player stats | High | Low-Medium | Can derive from existing per-game + team pace data. |
| **High** | Team advanced stats (SRS, ORtg, DRtg, Pace) | High | Medium | Warehouse team tables likely have these; expose. |
| **High** | Fix awards display (humanize codes, dedupe voting rows) | High | Low | Presentation-only fix in `web/src/views/players.ts`. |
| **High** | Fix stale/incorrect team recent games and roster | High | Low | Data-quality fix; check `bridge_player_team_season` and scraper alignment. |
| **High** | Add team logos | Medium | Low | Static logo assets or SportsLogos.net-style CDN URLs by abbreviation. |
| **Medium** | Player game logs / last 5 games | High | Medium-High | Requires `game` table query by player; new API + view. |
| **Medium** | Opponent stats and team league ranks | Medium | Medium | New queries against existing warehouse tables. |
| **Medium** | Player salaries / contracts | Medium-High | High | Likely need new feed/scrape (BBR contracts pages or Spotrac). |
| **Medium** | Team draft history | Medium | Medium | Existing `draft` table can be filtered by team. |
| **Medium** | Browseable player directory | Medium | Low | Add alphabetical or paginated list to Players tab. |
| **Medium** | Franchise all-time leaders / Top 12 players | Medium | Medium | Aggregate existing player stats by franchise. |
| **Low** | College stats | Low-Medium | High | New data source (sports-reference.com/cbb or NCAA feed). |
| **Low** | Uniform numbers / retired numbers | Low-Medium | Medium | Repo already has `bbr_jerseys.jsonl`; extend queries. |
| **Low** | Head-to-head records | Low-Medium | Medium | New query from `game` table. |
| **Low** | All-Star Game player stats | Low | Medium | New query + view. |
| **Low** | Awards voting shares | Low-Medium | Medium | Requires new award-vote data or BBR scrape. |
| **Low** | Full franchise history (e.g., Minneapolis Lakers) | Low | Medium | SCD/historical team mapping in warehouse. |

## 6. Proposed Phased Roadmap

### Phase 1: Quick Wins (Data Already in Warehouse)

1. **Clean awards rendering** — map `all_defense`, `nba mip`, etc. to human labels; hide or consolidate "Voting" rows; group by season.
2. **Fix team-page data quality** — investigate why Celtics roster included non-Celtics and why recent games are from 2023.
3. **Add per-100-possession player view** — derive from existing per-game stats + team pace.
4. **Expose player advanced stats** — query warehouse for PER, WS, BPM, VORP and add an "Advanced" section to player pages.
5. **Expose team advanced stats** — add SRS, Pace, ORtg, DRtg to season-by-season table.
6. **Add team logos** — serve static SVG/PNG by abbreviation.
7. **Add browseable player list** — show default alphabetical list or top players in the Players tab.

### Phase 2: Schema/API Additions

1. **Player game logs** — new `/api/players/:id/games` endpoint and a game-log view.
2. **Opponent stats and league ranks** — new team queries and tables.
3. **Team draft history** — filter existing draft table by `team_id`.
4. **Franchise all-time leaders** — aggregate career stats by team.
5. **Uniform/retired numbers** — leverage existing `bbr_jerseys.jsonl` and scraper sidecars.
6. **Head-to-head** — new `game` table query by opponent.

### Phase 3: Nice-to-Haves / New Data Sources

1. **Salaries/contracts** — integrate BBR contracts or Spotrac feed.
2. **College stats** — integrate Sports-Reference college basketball data.
3. **All-Star Game stats and awards voting shares** — new award/all-star data source.
4. **Full historical franchise mapping** — represent pre-relocation franchises (Minneapolis Lakers, etc.).
5. **Transactions/trades feed** — new external data source.

## 7. Conclusion

The NBA Data Explorer already covers the basics well: player vitals, career summary, season-by-season per-game stats, shooting zones for modern players, team season histories, coaching histories, and playoff series. The largest gaps versus Basketball-Reference are **advanced statistics** (player and team), **per-100-possession rates**, **game logs**, **salaries/contracts**, and **team branding/franchise metadata**. Most of the high-value gaps appear to be queryable from the existing warehouse schema; the priority should be exposing and cleaning that data before taking on new external feeds.
