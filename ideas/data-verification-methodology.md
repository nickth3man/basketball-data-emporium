# NBA Data Verification Methodology

> **Purpose.** A thorough, domain-grounded methodology for verifying the accuracy
> of the NBA data in `data/nba.duckdb`, so that a verified ETL/audit run can be
> produced honestly (not by weakening the gate). This document is the research
> deliverable behind the "make the data verifiable" workstream.
>
> **Correctness mandate.** This project is meant to reflect **real NBA events and
> data**; the bar is **100% correct, rigorously tested**. "100%" is defined against
> the *official, settled* record (see [§8.5](#85-stat-corrections--accuracy-is-a-moving-target)
> on stat corrections) — not a preliminary or live feed. Reaching it requires both
> exhaustive internal checks (Layers 0–4, 6–8) **and** programmatic reconciliation
> against authoritative external NBA sources (Layer 5). Neither alone is sufficient.
>
> **Scope note.** The API serves `unified_star.*` and `api.v_*`. The `nbadb.*`
> SQLite mirror and `main.*` legacy star are *not* served, though the ETL validates
> them.
>
> **Grounding.** Every check in Layers 0–4 was run against the live 22 GB snapshot
> on 2026-06-29; measured results are in [§13](#13-measured-findings-served-layer).
> Domain rules are sourced from the Basketball-Reference glossary, the NBA.com stat
> glossary, the NCAA statisticians' manual, and the `nba_api` endpoint catalog; the
> framework from standard data-quality practice (see [§16 References](#16-references)).

---

## 1. The core problem

Verifying sports data is **not** "does it look reasonable" — it is a layered
program of falsifiable checks. There are two fundamentally different questions:

1. **Is the data internally and structurally sound?** (Can it be true at all?)
   Answerable *from the data alone* — box-score algebra, referential integrity,
   era availability, aggregation consistency. Cheap, deterministic, exhaustive.
2. **Is the data externally accurate?** (Is it *actually* true?)
   Answerable only against an **authoritative external source** — sampled,
   bounded, and capped by the quality of that source.

A trustworthy program does **all** of question 1 exhaustively and as much of
question 2 as authoritative sources allow. Internal soundness is necessary but not
sufficient: a season can satisfy every algebraic identity and still be wrong if a
digit was transposed at the source. The cross-source layer ([§8](#8-layer-5--cross-source-reconciliation-against-real-nba-data))
exists to attack exactly that residual — and is what makes "reflects real NBA
data" a testable claim rather than an aspiration.

### The decisive insight from this dataset

The snapshot's own DQ suite flags ~150k "critical" rows. **Measured, those
violations are overwhelmingly systematic era artifacts, not random corruption:**

| Headline violation | Count | What it actually is |
| --- | ---: | --- |
| `fgm > fga` | 48,377 | **99.95% have `fga = 0`**, concentrated 1946–1982 → FGA *not recorded* in early eras, stored as `0` instead of `NULL` |
| `oreb + dreb ≠ reb` | 200,773 | **99.96% have `oreb = dreb = 0`** with `reb > 0`, pre-1983 → ORB/DRB split *not recorded* (split exists only since 1973-74) |
| `season.team_id` unresolved | 31,730 | A single sentinel `team_id = 0` ("team unresolved / combined"), not corruption |

The *genuine* corruption residual is small and modern (12 negative-minute rows,
64 points-identity breaks, 25 impossible `fg_pct`). This reframes the effort: the
path to a verified snapshot is **(a)** classify the era artifacts as known
divergences or fix `0 → NULL` under the availability rules, **(b)** treat
`team_id = 0` as a sentinel, **(c)** clean the small genuine residual, and **(d)**
prove the rest against real NBA data with Layer 5 — not a ground-up rebuild.

---

## 2. Data-quality dimensions, mapped to basketball

The canonical dimensions (dbt Labs taxonomy) and how each lands on NBA data:

| Dimension | Generic meaning | NBA instantiation |
| --- | --- | --- |
| **Validity** | Value legal for its type/range | `0 ≤ fg_pct ≤ 1`; `min ≥ 0`; `fgm ≤ fga`; well-formed `game_id` |
| **Consistency** | Self-consistent & consistent across layers | `pts = 2·fgm + fg3m + ftm`; game logs sum to season totals |
| **Completeness** | All required records & fields present | Every game has two team rows; a stat is present **iff** the era recorded it; every scheduled game is present |
| **Accuracy** | Matches reality | A season's PTS equals the **official** NBA / Basketball-Reference value |
| **Uniqueness** | No unintended duplicates | One row per (player, game); one PK per `dim_team` |
| **Integrity** | FKs resolve | Every `game_id`/`player_id`/`team_id` resolves to its dimension |
| **Freshness** | Updated within SLA | `audit.pipeline_run_log.started_at` within the staleness window; re-verified after stat corrections settle |

The dbt generic tests (`not_null`, `unique`, `accepted_values`, `relationships`)
cover the cheap end; the domain invariants below cover what generic tooling
cannot express.

---

## 3. Layer 0 — Box-score internal-consistency invariants

Algebraic identities that **must hold for any valid box score**, derived from the
rules of basketball and the official statisticians' manual. No external source
needed; run exhaustively over every row.

### Per-player and per-team shooting algebra

| ID | Invariant | Rationale |
| --- | --- | --- |
| `fg_subset` | `fgm ≤ fga` | Can't make more than you attempt |
| `fg3_subset_make` | `fg3m ≤ fgm` | 3PT makes are a subset of FG makes |
| `fg3_subset_att` | `fg3a ≤ fga` | 3PT attempts are a subset of FG attempts |
| `ft_subset` | `ftm ≤ fta` | Same for free throws |
| `pts_identity` | `pts = 2·fgm + fg3m + ftm` | A made 3 = 2 (as a FG) + 1 bonus; FTs = 1 each |
| `reb_split` | `oreb + dreb = reb` | Offensive + defensive = total |
| `pct_range` | every `*_pct ∈ [0, 1]` | Percentages are fractions |
| `pct_consistency` | `fg_pct ≈ fgm/fga` (±rounding) | Stored pct must match its ratio |
| `nonneg` | all counting stats `≥ 0` | No negative tallies — **includes minutes** |

```sql
-- Every shooting-subset and identity violation in one pass
SELECT game_id, player_id
FROM unified_star.fact_player_game_boxscore
WHERE fgm > fga OR fg3m > fgm OR fg3a > fga OR ftm > fta
   OR (points IS NOT NULL AND points <> 2*fgm + fg3m + ftm)
   OR (reb IS NOT NULL AND oreb IS NOT NULL AND oreb + dreb <> reb)
   OR min < 0
   OR (fg_pct IS NOT NULL AND (fg_pct < 0 OR fg_pct > 1));
```

### Game-level balancing (NCAA statisticians' manual)

- **Rebound balance:** `TeamA.ORB + TeamB.DRB = TeamA missed shots`. A cross-team
  integrity check.
- **Team minutes:** `Σ player minutes per team per game = 240` (5 × 48), **+25 per
  overtime**. Gate by era — minutes exist only since 1951-52; DNP rows carry `NULL`.
- **Team = Σ players:** team box-score totals equal the sum of that team's player
  rows for each counting stat.
- **Two teams per game:** exactly two team rows per `game_id`; `is_home` XOR.

> **Caveat discovered here:** `min` is a `DOUBLE`; the naive "team sums to 240"
> check is unreliable because 169,694 player rows have `NULL` minutes (pre-1952 +
> DNPs) and a few carry corrupt negatives. Gate to `season_year ≥ 1952` and exclude
> `NULL`/`<0` before summing.

---

## 4. Layer 1 — Era-aware availability (the most important layer for *this* data)

The single largest source of false "errors" is treating a stat that **did not
exist yet** as if it should be populated. Before its tracking date the correct
value is `NULL`, **not `0`**; storing `0` corrupts every cross-era average and
trips the identities above.

### Authoritative availability cutoffs (Basketball-Reference glossary)

> Season denoted by its **ending year** (1979-80 → `1980`).

| Stat / field | Available since | Ending-year cutoff |
| --- | --- | ---: |
| Total rebounds (`TRB`) | 1950-51 | ≥ 1951 |
| Minutes played (`MP`) | 1951-52 | ≥ 1952 |
| Offensive/Defensive rebounds (`ORB`/`DRB`) | 1973-74 | ≥ 1974 |
| Steals (`STL`) | 1973-74 | ≥ 1974 |
| Blocks (`BLK`) | 1973-74 | ≥ 1974 |
| Turnovers (`TOV`) | **1977-78** | ≥ 1978 |
| 3-pointers (`3P`/`3PA`/`3P%`) | **1979-80** | ≥ 1980 |
| Games started (`GS`) | 1982 | ≥ 1982 |
| Usage%, TOV%, ORtg | 1977-78 | ≥ 1978 |
| BPM, VORP, DRtg, Pace, Poss | 1973-74 | ≥ 1974 |

**Two checks per era-gated column:** (1) pre-cutoff rows must be `NULL` (or a
flagged `0`-instead-of-`NULL` divergence); (2) post-cutoff rows should generally be
non-`NULL` (allowing legitimate per-game zeros).

```sql
-- Steals must be NULL before 1973-74 (ending year 1974)
SELECT count(*) FROM unified_star.fact_player_season_stats
WHERE CAST(left(CAST(season_year AS VARCHAR), 4) AS INT) < 1974
  AND stl IS NOT NULL;          -- expect 0; non-zero ⇒ 0-as-NULL divergence
```

> **Gap:** the existing audit check `hist_blk_stl_pre_1973...` only covers
> steals/blocks. Turnovers (cutoff 1978) and three-pointers (cutoff 1980) have
> **later** cutoffs and need their own era checks.

---

## 5. Layer 2 — Aggregation & grain consistency

Roll-ups must reconcile across grains — catches double-counting and join fan-out.

| Check | Invariant |
| --- | --- |
| **Game → season** | `Σ player game logs (one season) = player_season_stats` |
| **Season → career** | `Σ season rows = career totals` |
| **Trade splits (2TM/TOT)** | Combined "total" row = `Σ` per-team rows (Harden 2021-22: BRK 990 + PHI 442 = 1432). Counting per-team **and** the total double-counts |
| **Team season → game** | `Σ team game logs = team_season_summary` (W/L, PTS) |
| **Standings** | `Σ wins = Σ losses` league-wide per season; `win_pct = w/(w+l)` |

> **`team_id = 0` sentinel:** 31,730 player-season rows use `team_id = 0` for
> "team unresolved / combined." Aggregations and franchise/team joins **must**
> exclude or special-case it. This is the server-side analogue of BBR's `TOT` row.

---

## 6. Layer 3 — Referential integrity

Every fact FK must resolve to its dimension — exactly what the failing
`validate-staging-fk` stage enforces.

```sql
SELECT count(*) FROM unified_star.fact_player_game_boxscore b
LEFT JOIN unified_star.dim_player p USING (player_id)
WHERE p.player_id IS NULL;   -- orphaned player-game rows
```

Checks: `fact_*.game_id → dim_game`, `fact_*.player_id → dim_player`,
`fact_*.team_id → dim_team`, `bridge_*` resolution, and **uniqueness** of each
dimension PK (the audit found 14 duplicate `dim_team` PK groups in the mirror).

---

## 7. Layer 4 — Statistical & distributional checks

Profile distributions to surface *plausible-but-wrong* values no identity catches.

- **Range / outlier bounds:** single-game `pts ≤ 100` (Wilt's record), `min ≤ 65`,
  season `gp ≤ 82`. Flag, don't fail — records exist to be approached.
- **Null-rate monitoring:** track per-column null rate by era; a jump in a
  post-cutoff era signals an ingestion gap (`nbadb_critical_null_rate` flagged
  9.58% null `min`).
- **Duplicate detection:** `(player_id, game_id)` and `(player_id, season_year,
  team_id, is_playoffs)` must be unique.
- **Benford / digit checks** on large aggregates can flag fabricated/mis-scaled
  data (research-grade, optional).
- **Cross-table profile drift:** `audit.column_profile` / `checksum_snapshot`
  already snapshot row counts and checksums — diff them run-over-run.

---

## 8. Layer 5 — Cross-source reconciliation against real NBA data

Internal checks prove *self-consistency*; only authoritative external sources prove
*accuracy*. This is the layer that makes "reflects real NBA events" testable, and
the one the correctness mandate leans on hardest.

**Verification principle — agreement of independents.** A value is *accuracy-
verified* when **≥ 2 independent authoritative sources agree** with the warehouse
(e.g., stats.nba.com **and** Basketball-Reference). Where they disagree, that
disagreement is itself a recorded finding, not a silent pick.

### 8.1 The source catalog (tiered by authority)

| Tier | Source | Authoritative for | Access |
| --- | --- | --- | --- |
| **1 — Official league** | **stats.nba.com** (via `nba_api`) | Box scores, PBP, shot charts, tracking, standings (1946–, richest from 1996‑97) | `nba_api` Python client; JSON |
| 1 | **official.nba.com** | **Stat corrections**, Last‑Two‑Minute (L2M) reports, Coach's Challenge, rulebook | Web / L2M PDFs |
| 1 | **pr.nba.com** (NBA Communications) | Transactions, awards, milestones, official records | Web |
| **2 — Authoritative secondary** | **Basketball‑Reference** (anchor corpus + live) | Deepest history (1946–), career/franchise/season totals, advanced metrics | Local anchors `data/anchors/`; live HTML; `basketball_reference_web_scraper` |
| 2 | **ESPN NBA** | Independent box scores (third corroborating source) | `hoopR` / web |
| 2 | **balldontlie API** | Quick free JSON cross‑check (games, players, box scores) | REST; not 100% complete on tracking |
| **3 — Specialist / analytics** | PBP Stats / `pbpstats`, Cleaning the Glass, Dunks&Threes | Possession & derived metrics **only** (not raw counting) | APIs / subscription |
| **4 — Community datasets** | Kaggle (e.g. *NBA Box Scores 1947–Today*), `hoopR` bulk | Bulk corroboration; **provenance varies** — never the sole source | CSV / packages |

> **Tier discipline:** raw counting stats (PTS, FG, FT, REB, AST, STL, BLK, TOV) must
> agree across Tier 1 + Tier 2. Derived metrics (PER, BPM, RAPTOR…) **legitimately
> differ** between providers (proprietary inputs, different possession estimators) —
> do **not** cross-source them; verify those by **recomputation** ([§11](#11-layer-8--advanced-metric-recomputation)).

### 8.2 Official endpoint → warehouse table reconciliation matrix

The `nba_api` client mirrors the official stats.nba.com endpoints. Map each
warehouse object to the endpoint that is its system-of-record:

| Warehouse object | `nba_api` endpoint(s) | Grain | Key fields to diff |
| --- | --- | --- | --- |
| `dim_player` | `CommonAllPlayers`, `CommonPlayerInfo` | player | name, birth_date, draft yr/rnd/#, from/to year, active |
| `dim_team` | `CommonTeamYears`, `TeamDetails`, `TeamInfoCommon` | team | city, name, abbrev, founded |
| `dim_game` / schedule | `LeagueGameLog`, `LeagueGameFinder`, `ScoreboardV2` | game | date, home/away, season_type, final score |
| `fact_player_game_boxscore` | `BoxScoreTraditionalV2` (PlayerStats) + `…AdvancedV2`/`…MiscV2`/`…ScoringV2`/`HustleStatsBoxScore` | player‑game | min, pts, fgm/fga, fg3m/fg3a, ftm/fta, oreb/dreb/reb, ast, stl, blk, tov, pf, +/− |
| `fact_team_game_boxscore` | `BoxScoreTraditionalV2` (TeamStats), `BoxScoreSummaryV2` | team‑game | counting totals + poss/pace |
| `fact_game_quarter_scores` | `BoxScoreSummaryV2` (LineScore) | team‑game‑period | per‑quarter points |
| `fact_player_season_stats` | `LeagueDashPlayerStats`, `PlayerCareerStats`, `PlayerProfileV2` | player‑season | gp, gs, min, pts, reb, ast, stl, blk (+ adv) |
| `fact_team_season_summary` | `LeagueDashTeamStats`, `TeamYearByYearStats` | team‑season | w, l, win_pct, pts, ratings |
| `v_team_standings` | `LeagueStandingsV3` | team‑season | w, l, win_pct, seed, conf/div |
| `v_franchise_leaders` | `FranchiseLeaders`, `TeamHistoricalLeaders` | franchise | career leaders per stat |
| `fact_pbp_events` | `PlayByPlayV2` | event | sequence, clock, score, descriptions |
| `v_shot_chart` | `ShotChartDetail` | shot | x/y, made/miss, type, distance, value |
| `fact_draft_combine` | `DraftCombineStats`, `DraftHistory` | player | measurements, pick |
| `fact_player_awards` | `PlayerAwards` | player‑award | award, season |

### 8.3 The reconciliation procedure

For a target table and a sampled key set:

1. **Fetch** the official record from the mapped endpoint (`nba_api`), using the
   correct `GameID` / `Season` / `PlayerID`. Resolve identity through the warehouse
   bridge (`bref_player_id` ↔ NBA `person_id`; `team_abbrev` ↔ 10‑digit `team_id`).
2. **Normalize** before comparing — the known gotchas:
   - **Column case/name:** `nba_api` is UPPERCASE (`PTS`, `FGM`, `REB`); warehouse
     is lowercase. Map explicitly.
   - **Minutes:** `nba_api` returns `"MM:SS"` strings; warehouse `min` is `DOUBLE`.
     Convert before diffing.
   - **Season encoding:** `nba_api` uses `"2023-24"`; warehouse mixes `YYYY` and
     `YYYY-YY` — canonicalize to ending year.
   - **Team identity:** map to 10‑digit `team_id`, never abbreviation (Brooklyn
     `NJN`, Philly `SYR`, ambiguous `PHI` — see ETL divergence #6).
   - **Era gating:** skip columns that did not exist in that season (Layer 1).
3. **Diff** with tolerances:
   | Field class | Tolerance |
   | --- | --- |
   | Counting stats (PTS, FGM, REB, AST…) | **exact** (modern era); pre‑1985 expect source gaps |
   | Percentages | ±0.001 (rounding) |
   | Minutes | exact after `MM:SS → decimal` |
   | Derived (PER, BPM, ratings) | **do not cross‑source** — recompute (§11) |
4. **Record** each mismatch to `audit.metric_discrepancy` /
   `audit.cross_table_discrepancy` (tables already exist) with source, expected,
   actual, and severity — feeding the same DQ verdict that gates `/api/status`.

### 8.4 Coverage strategy (you cannot fetch 1.6M games from a rate-limited API)

Stratify by cost and value:

- **Dimensions — 100%.** Players, teams, games are small and cheap; verify every row.
- **Season aggregates — 100% (modern).** `LeagueDashPlayerStats` /
  `LeagueDashTeamStats` return a whole season per call — ~1 call/season fully
  reconciles season totals.
- **Player‑games — stratified sample + census of the notable.** Random sample
  across eras (so accuracy is estimated, not assumed), **plus** a census of
  record/milestone games and every `golden.csv` fact.
- **PBP / shot — internal derivation (no API).** Reconcile via [§9](#9-layer-6--play-by-play--shot-level-derivation)
  for a game sample; cross-check the PBP itself against `PlayByPlayV2`.
- **Operational hygiene:** `nba_api` needs browser-like headers and ~0.6 s between
  calls. **Cache every response as a new immutable anchor** under
  `data/anchors/` — the repo already uses this pattern; it grows permanent,
  offline ground truth and removes future network dependence.

### 8.5 Stat corrections — accuracy is a moving target

The NBA issues **official stat corrections** after games: base stats post in real
time, advanced stats 10–15 min later, and corrections can land **hours to days**
afterward (confirmed by ESPN/Yahoo fantasy stat-correction feeds and the NBA.com
FAQ). Implications for a "100% correct" bar:

- **Define ground truth as the *settled* official record** — e.g. the official box
  score ≥ 72 h post‑game, or end‑of‑season finalized totals. Pin `golden.csv` to
  settled values only.
- **A divergence from a live/preliminary number is not necessarily an error.**
  Re‑verify current-season data after finalization.
- **`official.nba.com` L2M reports** and the corrections feed are the authoritative
  record for late officiating/scoring changes — use them when a sampled game
  disagrees with a third-party source.

### 8.6 Anchors & golden facts (already in this repo)

1. **Local anchor corpus** (`data/anchors/bbref-pages/`, 441 immutable BBR HTML
   snapshots) — the **primary** ground truth where it exists; immune to BBR
   republishing and bot-mitigation. Extend it with cached `nba_api` JSON (§8.4).
2. **`golden.csv`** (44 pinned facts) + parametrized harness — assert each fact's
   SQL reproduces the canonical value. **Extending this is the single highest-value
   accuracy lever**; promote anchored pages into golden facts at zero fetch cost.

**Honest ceiling.** Golden rows prove *those* facts; sampling *estimates* the rest
with a known confidence — it does not prove all 6,000+ players exhaustively. Layer 1
(era-aware lineage) is the structural defense; Layers 5/6 are the accuracy defense.
If BBR re-scores a historical stat (1970s assists were re-scored), a pinned value
becomes "wrong but stable" until a human refreshes it — record the source snapshot
date with every pin.

---

## 9. Layer 6 — Play-by-play & shot-level derivation

Reconstruct box-score aggregates from the event stream and compare to the stored
box score — an *independent derivation path inside the same database* that catches
aggregation/ETL bugs no row-level identity can. Derive from **`fact_pbp_events`**,
which carries the join keys and outcome fields (`game_id`, `player_id`,
`is_field_goal`, `shot_result` ∈ {`Made`,`Missed`}, `shot_value`, `points_total`,
running `score_home`/`score_away`). Note: `api.v_shot_chart` is a **display view
without `game_id`/`player_id` keys**, so it cannot be the derivation source — use
the PBP fact.

| Derivation check | Invariant |
| --- | --- |
| Shots → makes | `Σ events (is_field_goal, shot_result='Made') = fgm`; `Σ is_field_goal = fga` |
| Shots → threes | `Σ made events with shot_value=3 = fg3m`; `Σ shot_value=3 = fg3a` |
| Scoring → points | PBP running score is monotone and ends at the final |
| PBP → quarters | `Σ scoring per period = fact_game_quarter_scores` |
| Quarters → final | `Σ quarter points = fact_team_game_boxscore.pts` (already **0 violations** — §13) |
| PBP → team total | `max(score_home/score_away) = team box-score pts` |

```sql
-- VALIDATED 2026-06-29 (game 0022501193): PBP final score = team box pts.
-- PBP made FGs must equal stored FGM (per player-game), modern era.
SELECT e.game_id, e.player_id,
       count(*) FILTER (WHERE e.is_field_goal AND e.shot_result = 'Made') AS pbp_fgm,
       b.fgm
FROM unified_star.fact_pbp_events e
JOIN unified_star.fact_player_game_boxscore b USING (game_id, player_id)
JOIN unified_star.dim_game g USING (game_id)
WHERE CAST(left(CAST(g.season_year AS VARCHAR), 4) AS INT) >= 1997
GROUP BY 1, 2, b.fgm
HAVING count(*) FILTER (WHERE e.is_field_goal AND e.shot_result = 'Made') <> b.fgm;
```

> **Era gate:** event-level PBP is reliable only from **1996‑97** onward; gate
> derivation checks to that range (older games legitimately lack events). This makes
> PBP derivation a *modern-era* accuracy weapon — precisely where the user-facing
> data matters most.

---

## 10. Layer 7 — Game-ID & schedule structural validation

NBA `game_id`s are not opaque — the 10-digit stats.nba.com id encodes meaning:

```
0 0 [T] [YY] [SSSSS]      e.g. 0021900001
│ │  │   │      └── game sequence (00001…)
│ │  │   └───────── season START year, 2 digits (19 → 2019-20). Map: 1900+YY if YY≥46 else 2000+YY
│ │  └───────────── season type: 1 preseason · 2 regular · 3 all-star · 4 playoffs · 5 play-in · 6 NBA Cup final (2023-24+)
└─┴──────────────── literal "00" prefix
```

(`0024600078` in this DB → type `2` regular, season `46` → 1946‑47, game `78` — and
the `fgm>fga` violations cluster exactly in those early ids.)

**Checks** (counts below VALIDATED against the snapshot 2026-06-29):

- **Format:** `game_id ~ '^00[1-6]\d{7}$'`. *Measured:* only **3** non-conforming —
  all type-`6` **NBA Cup** finals (`0062500001`, `0062400001`, `0062300001`), i.e. a
  legitimate game type, **not** corruption. The regex must allow `6` (this is why the
  type→label mapping must be confirmed against the data before enforcing).
- **Self-consistency:** the embedded 2-digit **start** year matches
  `dim_game.season_year`, and the type digit matches `dim_game.season_type`.
  *Measured:* **33** rows where the embedded season disagrees with `season_year` —
  bounded, worth investigating (likely cross-year/Cup edge cases).
- **Uniqueness:** `game_id` unique in `dim_game` (*measured:* **0** duplicates);
  exactly two team rows per game (*measured:* **0** violations). ✅
- **Schedule completeness:** each team plays the expected games per season —
  **82 standard**, with known exceptions (1998‑99 = 50, 2011‑12 = 66, 2019‑20
  irregular/COVID, 2020‑21 = 72, varied pre‑1967). A team short of its expected
  count signals missing games.
- **Game census vs official:** games per season matches `LeagueGameLog` /
  `LeagueGameFinder` for that season (Layer 5).

---

## 11. Layer 8 — Advanced-metric recomputation

For every *derived* metric the warehouse stores, **recompute from raw inputs and
compare** (rounding tolerance). This is how derived metrics are verified — they are
**not** cross-sourced (§8.1). Authoritative formulas (BBR glossary):

| Metric | Formula |
| --- | --- |
| `eFG%` | `(FG + 0.5·3P) / FGA` |
| `TS%` | `PTS / (2·TSA)`, where `TSA = FGA + 0.44·FTA` |
| `FG% / 3P% / FT%` | `FGM/FGA`, `3PM/3PA`, `FTM/FTA` |
| `GmSc` | `PTS + 0.4·FG − 0.7·FGA − 0.4·(FTA−FT) + 0.7·ORB + 0.3·DRB + STL + 0.7·AST + 0.7·BLK − 0.4·PF − TOV` |
| `Poss` (team) | `0.5·((FGA + 0.4·FTA − 1.07·(ORB/(ORB+Opp DRB))·(FGA−FG) + TOV) + (opp symmetric))` |
| `Pace` | `48·((Tm Poss + Opp Poss) / (2·(Tm MP / 5)))` |
| `win_pct` | `W / (W + L)` — recompute server-side; never trust a stored copy |

```sql
-- Stored TS% must match recomputed TS% (tolerance 0.005)
SELECT player_id, season_year, ts_pct,
       pts / NULLIF(2*(fga + 0.44*fta), 0) AS ts_recomputed
FROM <view with fga,fta,pts,ts_pct>
WHERE abs(ts_pct - pts/NULLIF(2*(fga + 0.44*fta),0)) > 0.005;
```

`audit.advanced_stat_recompute` already exists for exactly this — populate and diff
it.

---

## 12. The verification stack at a glance

| Layer | Question | Source needed | Cost | Coverage |
| --- | --- | --- | --- | --- |
| 0 Box-score invariants | Can it be true? | none | low | 100% |
| 1 Era availability | Right era? | BBR cutoffs | low | 100% |
| 2 Aggregation/grain | Roll-ups reconcile? | none | low | 100% |
| 3 Referential integrity | FKs resolve? | none | low | 100% |
| 4 Distributional | Plausible? | none | low | 100% |
| **5 Cross-source** | **Actually true?** | **nba_api + BBR + ESPN** | **high** | **sampled + census** |
| 6 PBP/shot derivation | Aggregates match events? | internal (PBP) | medium | 1996‑97+ sample |
| 7 Game-ID/schedule | Structurally & calendar-complete? | nba_api (census) | low | 100% |
| 8 Advanced recompute | Derived metrics correct? | formulas | low | 100% |

Layers 0–4, 7, 8 are exhaustive and deterministic → run in CI on every change.
Layer 5 (and the API-dependent part of 7) is rate-limited and sampled → run as a
scheduled reconciliation job that writes `audit.*` and feeds the data-status gate.

---

## 13. Measured findings (served layer)

Run 2026-06-29 against `data/nba.duckdb`. **Served tables only.**

### `unified_star.fact_player_game_boxscore` — 1,667,844 rows

| Check | Violations | Verdict |
| --- | ---: | --- |
| `fgm > fga` | 48,377 | **Era artifact** — 48,352 have `fga = 0` (1946–1982) |
| `oreb+dreb ≠ reb` | 200,773 | **Era artifact** — 200,686 have `oreb = dreb = 0` (pre-1983) |
| `ftm > fta` | 493 | Mostly early-era `fta = 0` |
| `pts ≠ 2·fgm + fg3m + ftm` | 64 | **Genuine** — investigate |
| `fg_pct ∉ [0,1]` | 25 | **Genuine** — investigate |
| `fg3m > fgm` / `fg3a > fga` | 1 / 6 | **Genuine** — trivial residual |
| `min < 0` | 12 | **Genuine corruption** (a few in 2008) |
| negative counting stats | 0 | ✅ clean |

### `unified_star.fact_team_game_boxscore` — 75,980 rows

All shooting-subset, rebound-split, and points-identity checks: **0 violations.** ✅

### `unified_star.fact_player_season_stats` — 66,421 rows (Player Hub season source)

| Check | Violations |
| --- | ---: |
| `gs > gp` | 1 |
| negative pts/reb/ast | 0 |
| `ts_pct` out of range | 30 |
| pre-1974 non-null `stl` | 24 |
| `team_id = 0` sentinel | 31,730 (30,232 reg / 1,498 playoff) |

### Referential integrity (served layer)

| Check | Orphans |
| --- | ---: |
| `season.player_id → dim_player` | 0 ✅ |
| `season.team_id → dim_team` | 31,730 (all `team_id = 0` sentinel) |
| `pgame.player_id → dim_player` | 189 |
| `pgame.game_id → dim_game` | 4,861 |

### Bottom line

- **Modern era (post-~1983) served data is essentially clean.** Team-level data is
  clean across all eras; quarter scores already reconcile to team points exactly.
- **The "150k critical rows" are dominated by two explainable era-completeness
  artifacts plus one sentinel** — classifiable, not catastrophic.
- **`validate-staging-fk` fails on real but bounded orphans** (4,861 game / 189
  player on the served player-game fact; more in the mirror).
- **Genuine modern corruption is a tiny residual** (~100 rows) — the highest-value
  thing to fix outright, and the part Layer 5 must confirm against official data.

---

## 14. Path to an honest verified run

The README forbids marking failed data as verified. The legitimate path:

1. **Fix `0 → NULL` under the availability rules (Layer 1)**, or **classify the era
   artifacts** into `audit.discrepancy_known_divergence` (FGA-as-0, ORB/DRB-split-
   as-0) as accepted, documented divergences.
2. **Formalize `team_id = 0` as a sentinel** — exclude from FK validation and from
   franchise/team aggregations; document it.
3. **Fix the genuine residual** (negative minutes, 64 points-identity breaks, 25
   bad `fg_pct`, the `gs>gp` row).
4. **Resolve or accept the bounded FK orphans** (4,861 game / 189 player) so
   `validate-staging-fk` can pass.
5. **Run Layer 5 reconciliation** against `nba_api` + BBR for the dimension census,
   modern season aggregates, and a stratified game sample; record results to
   `audit.metric_discrepancy`.
6. **Re-run the ETL** so `audit.pipeline_run_log` records a `success` and
   `audit.dq_results` carries no *unaccepted* CRITICAL — then `/api/status` reports
   `data_verified = true` **on its own merits**.

> **Latent gate bug:** `read_audit_status` treats `audit.dq_results` as
> "present = passing" merely because rows exist — even 248 CRITICAL rows. Once the
> ETL writes a `success` run, the gate would flip to verified **without** evaluating
> DQ severity. The gate should also fail on unaccepted CRITICAL/HIGH `dq_results`
> before declaring `verified`.

---

## 15. Operationalizing in this repo

| Method | Where it lives / should live |
| --- | --- |
| Box-score, era, grain, game-id invariants (Layers 0–4, 7) | `backend/tests/schema/` + `backend/tests/invariant/` (pytest over read-only DuckDB) — run in CI |
| PBP/shot derivation (Layer 6) | `backend/tests/invariant/` gated to 1996‑97+ |
| Advanced-metric recompute (Layer 8) | `audit.advanced_stat_recompute` (populate + diff) |
| Cross-source reconciliation (Layer 5) | new scheduled job using `nba_api`; cache responses as anchors; write `audit.metric_discrepancy` |
| Golden cross-source facts | `backend/tests/golden/golden.csv` (+ harness) — **extend** |
| Known divergences | `audit.discrepancy_known_divergence` (classify era artifacts) |
| Run / DQ verdict | `audit.pipeline_run_log`, `audit.dq_results` → `/api/status` gate |
| Release gate | `scripts/check-data-status.ps1` + the CI `data-status-gate` job |

Even without adopting dbt, use its model: express each invariant as a query that
**returns the offending rows** (empty = pass), tag it with a severity, and record
counts to `audit.dq_results`. That is exactly the shape the audit layer already uses.

---

## 16. References

- Basketball-Reference, *Glossary* — stat formulas + per-stat availability years:
  https://www.basketball-reference.com/about/glossary.html
- NBA.com, *Stat Glossary* / *FAQ* — official metric definitions; stat-update/correction
  timing: https://www.nba.com/stats/help/glossary · https://www.nba.com/stats/help/faq
- `nba_api` — Python client + full endpoint catalog for stats.nba.com:
  https://github.com/swar/nba_api · https://github.com/swar/nba_api/blob/master/docs/table_of_contents.md
- *Awesome NBA Data* — curated source/API/tool catalog (official, BBR, ESPN,
  balldontlie, pbpstats, hoopR, Kaggle): https://github.com/JovaniPink/awesome-nba-data
- NCAA, *Official Basketball Statistics Rules / Basic Interpretations* — box-score
  balancing: http://fs.ncaa.org/Docs/stats/Stats_Manuals/Basketball/
- ESPN Fan Support, *Stat corrections* — how/when official NBA stats are revised:
  https://support.espn.com/hc/en-us/articles/360056679592-Stat-corrections
- dbt Labs, *Data quality dimensions* — the 7-dimension framework + generic tests
  (`not_null`, `unique`, `accepted_values`, `relationships`):
  https://www.getdbt.com/blog/data-quality-dimensions
- Kaggle, *NBA Box Scores and Stats (1947–Today)* — bulk corroboration dataset
  (provenance-check before use): https://www.kaggle.com/datasets/eoinamoore/historical-nba-data-and-player-box-scores
