export const meta = {
  name: 'execute-verification-plan',
  description: 'Build the full data-verification test suite (Layers 0-8), the audit-gate severity fix, and the Layer-5 reconciliation scaffold from data-verification-methodology.md',
  phases: [
    { title: 'Build', detail: 'one agent per layer module + gate fix + reconciliation scaffold' },
    { title: 'Review', detail: 'adversarially verify each module: tight assertions, passes pytest, CI-green without DB' },
  ],
}

const CONTRACT = [
'PROJECT: basketball-data-emporium. Backend dir: backend/. Run python via the venv:',
'  cd backend && ./.venv/Scripts/python.exe -m pytest tests/invariant/<your_file> -q --no-header',
'The DuckDB snapshot data/nba.duckdb is READ-ONLY (22GB). Never write to it.',
'',
'SHARED FOUNDATION (already created, do NOT modify):',
'  backend/tests/invariant/conftest.py exposes pytest fixtures:',
'    db -> read-only duckdb connection (session-scoped; SKIPS if file absent)',
'    count -> fn(sql)->int ; rows -> fn(sql)->list',
'  backend/tests/invariant/known_divergences.py (import as: import known_divergences as kd) provides:',
'    kd.AVAILABLE_SINCE_END_YEAR {TRB:1951,MP:1952,ORB:1974,DRB:1974,STL:1974,BLK:1974,TOV:1978,FG3:1980,GS:1982}',
'    kd.SENTINEL_TEAM_ID = 0 ; kd.PBP_ERA_START_END_YEAR = 1997 ; kd.MODERN_BOXSCORE_CLEAN_FROM_END_YEAR = 1983',
'    kd.GAME_ID_SEASON_TYPE {1:Preseason,2:Regular,3:All-Star,4:Playoffs,5:Play-In,6:Cup}',
'    kd.GENUINE_RESIDUAL_BASELINE {pgame_fgm_gt_fga_genuine:25, pgame_ftm_gt_fta_genuine:71, pgame_reb_split_genuine:87,',
'      pgame_pts_identity:64, pgame_fg_pct_out_of_range:25, pgame_fg3m_gt_fgm:1, pgame_fg3a_gt_fga:6, pgame_min_negative:12,',
'      season_gs_gt_gp:1, season_ts_pct_out_of_range:30, pgame_player_orphan:189, pgame_game_orphan:4861, gameid_embedded_season_mismatch:33}',
'    kd.season_end_year(v)->int, kd.season_start_year(v)->int ; kd.SEASON_END_YEAR_SQL.format(col=NAME) -> ending-year SQL expr',
'',
'SCHEMA (exact columns):',
'  unified_star.fact_player_game_boxscore(game_id,player_id,team_id,opponent_team_id,is_home,is_win,starting_position,comment,',
'    min DOUBLE,points,assists,blocks,steals,turnovers,fga,fgm,fg_pct,fg3a,fg3m,fg3_pct,fta,ftm,ft_pct,oreb,dreb,reb,fouls_personal,plus_minus,...adv) n=1667844',
'  unified_star.fact_team_game_boxscore(game_id,team_id,is_home,is_win,min,pts,fgm,fga,fg_pct,fg3m,fg3a,fg3_pct,ftm,fta,ft_pct,oreb,dreb,reb,ast,tov,stl,blk,pf,plus_minus,off_rating,def_rating,net_rating,pace,poss,coach_id) n=75980',
'  unified_star.fact_player_season_stats(player_id,team_id,season_year,is_playoffs,gp,gs,min,pts,ast,reb,stl,blk,per,ts_pct,ows,dws,bpm,vorp,usg_pct) n=66421 (season_year mixed 1946-47 AND 1947)',
'  unified_star.fact_game_quarter_scores(game_id,team_id,period,pts,fgm,fga,fg3m,fg3a,ftm,fta,reb,ast,stl,tov,plus_minus) n=303920',
'  unified_star.fact_team_season_summary(team_id,season_year,w,l,o_rtg,d_rtg) n=1672',
'  unified_star.fact_pbp_events(game_id,action_number,period,clock,seconds_elapsed,team_id,player_id,action_type,sub_type,description,',
'    is_field_goal,shot_value,shot_distance,shot_result(Made/Missed),x,y,score_home,score_away,points_total,assist_player_id,steal_player_id,block_player_id,foul_drawn_player_id) n=18.7M',
'  unified_star.dim_player(player_id,bref_player_id,full_name,...,is_active) ; unified_star.dim_team(team_id,team_city,team_name,team_abbrev,...)',
'  unified_star.dim_game(game_id,game_date,season_year(2025-26),season_type(Regular/Playoffs/Preseason/Cup),home_team_id,away_team_id,is_overtime,...)',
'  api.v_canonical_player_season_totals(Age,AST,BLK,PLAYER_ID,DRB,QeFGQ,FG,QFGQpct,FGA,FT,QFTQpct,FTA,G,GS,LEAGUE,MP,ORB,PF,PLAYER_NAME,Pos,PTS,SEASON int ending-yr,TEAM_ABBR,STL,TEAM_ID,TOV,TRB,TD,Q2PQ,Q2PAQ,Q3PQ,Q3PAQ) n=31119 [QUOTE mixed-case cols; real names use double-quotes: FG%, 3P, eFG%, 2P, 3PA, etc.]',
'  api.v_team_standings(season_year,team,games_played,wins,losses,win_pct) n=2889',
'  api.v_canonical_team_season(TEAM_ABBR,Age,ARENA,DRtg,DRBpct,eFGpct,FTr,L,LEAGUE,MOV,NRtg,ORtg,...,Pace,SEASON,SOS,SRS,TOVpct,TSpct,W,3PAr) n=1770 [QUOTE the %-cols]',
'  api.v_franchise_leaders(team,pts,pts_player,pts_person_id,reb,...,ast,...,stl,...,blk,...) n=72',
'  AVOID api.v_player_game_log (103M rows) and api.v_player_advanced (9.3M) - use unified_star facts.',
'',
'TEST CONVENTIONS (the suite MUST be GREEN on the current snapshot AND skip cleanly without the DB):',
'  - File in backend/tests/invariant/. Use the count fixture. import known_divergences as kd.',
'  - MEASURE FIRST: run the query against the DB to get the real count, THEN write the assertion to match reality.',
'  - Three tight assertion kinds (NEVER assert True, NEVER >=0, NEVER vacuous):',
'      CLEAN invariant  -> assert count(...) == 0',
'      GENUINE residual -> assert count(...) <= kd.GENUINE_RESIDUAL_BASELINE[key] (or a measured module-level constant if no kd key fits)',
'      ERA ARTIFACT     -> assert the artifact is fully EXPLAINED (violations not matching the artifact signature are within the genuine baseline). Document the artifact count in a comment.',
'  - Need a baseline not in kd? Define a module-level constant in YOUR file with a comment that you measured it. DO NOT edit known_divergences.py.',
'  - Every test fn takes a fixture (e.g. count) so it auto-skips without the DB. Add a docstring per test citing the layer.',
'  - SELF-VERIFY before returning: run your module with pytest until fully green (0 failed). Confirm it SKIPS (not errors) when DB absent: env DUCKDB_PATH=/nonexistent/x.duckdb BASKETBALL_DATA_DB_PATH=/nonexistent/x.duckdb.',
].join('\n')

const BUILD_SCHEMA = {
  type: 'object',
  properties: {
    file: { type: 'string' },
    tests_added: { type: 'integer' },
    self_verify_passed: { type: 'boolean' },
    skips_without_db: { type: 'boolean' },
    summary: { type: 'string' },
    findings: { type: 'string' },
  },
  required: ['file', 'self_verify_passed', 'skips_without_db', 'summary'],
}

const REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    ok: { type: 'boolean' },
    assertions_tight: { type: 'boolean' },
    passes_pytest: { type: 'boolean' },
    skips_without_db: { type: 'boolean' },
    issues: { type: 'array', items: { type: 'string' } },
    fixes_applied: { type: 'boolean' },
    verdict: { type: 'string' },
  },
  required: ['ok', 'passes_pytest', 'verdict'],
}

const ITEMS = [
  { id: 'layer0', kind: 'test', file: 'backend/tests/invariant/test_layer0_boxscore_invariants.py', target: 'tests/invariant/test_layer0_boxscore_invariants.py', brief:
'Layer 0 box-score internal-consistency invariants over three tables. ' +
'(A) unified_star.fact_player_game_boxscore (raw, has era artifacts): GENUINE assert <= kd baseline: fgm>fga AND fga>0 [pgame_fgm_gt_fga_genuine]; ftm>fta AND fta>0 [pgame_ftm_gt_fta_genuine]; (oreb+dreb<>reb AND NOT(oreb=0 AND dreb=0)) [pgame_reb_split_genuine]; points<>2*fgm+fg3m+ftm [pgame_pts_identity]; fg_pct outside 0..1 [pgame_fg_pct_out_of_range]; fg3m>fgm [pgame_fg3m_gt_fgm]; fg3a>fga [pgame_fg3a_gt_fga]; min<0 [pgame_min_negative]. CLEAN assert ==0: any negative among fgm,fga,ftm,fta,reb,points,assists. ERA ARTIFACT: a test asserting count(fgm>fga) - count(fgm>fga AND fga=0) <= the genuine baseline (artifact ~48k rows have fga=0); same for rebound split (oreb=dreb=0, ~200k). ' +
'(B) unified_star.fact_team_game_boxscore: assert ==0 for fgm>fga, fg3m>fgm, ftm>fta, oreb+dreb<>reb, pts<>2*fgm+fg3m+ftm (all measured clean). ' +
'(C) api.v_canonical_player_season_totals (quote mixed-case cols): assert ==0 for FG>FGA, 3P>FG, FT>FTA, ORB+DRB<>TRB (all non-null), PTS<>2*FG+3P+FT (all clean).' },

  { id: 'layer1', kind: 'test', file: 'backend/tests/invariant/test_layer1_era_availability.py', target: 'tests/invariant/test_layer1_era_availability.py', brief:
'Layer 1 era-aware availability. Pre-cutoff values should be NULL (0 is the artifact). Use kd.AVAILABLE_SINCE_END_YEAR and kd.SEASON_END_YEAR_SQL. ' +
'Targets: api.v_canonical_player_season_totals (SEASON = integer ending year) for ORB/DRB(1974), STL/BLK(1974), TOV(1978), 3P/3PA(1980), GS(1982), TRB(1951), MP(1952); and unified_star.fact_player_season_stats (mixed encoding) for stl/blk(1974). ' +
'For each: MEASURE count(SEASON<cutoff AND col IS NOT NULL). If those are all 0-valued it is the 0-as-NULL artifact: assert count(pre-cutoff AND col IS NOT NULL AND col<>0) <= a measured module-level baseline (ideally 0); document artifact count. Also post-cutoff completeness: assert count(SEASON>=cutoff AND col IS NULL) <= measured baseline. Investigate and document the pre-1974 non-null steals anomaly in fact_player_season_stats (~24 rows): characterize 0 vs non-zero and assert accordingly. Keep GREEN.' },

  { id: 'layer2', kind: 'test', file: 'backend/tests/invariant/test_layer2_aggregation.py', target: 'tests/invariant/test_layer2_aggregation.py', brief:
'Layer 2 aggregation & grain consistency. Implement what is verifiable; where a known encoding issue prevents exactness, assert within a measured baseline and DOCUMENT (do not skip silently). ' +
'Checks: (1) api.v_team_standings: win_pct == wins/NULLIF(wins+losses,0) within 0.0005 (assert ==0 or measured baseline); games_played == wins+losses (measure). (2) Per-season SUM(wins)=SUM(losses) (measure; small baseline allowed). (3) Assert NO api.v_team_standings / fact_team_season_summary row references team_id=0. ' +
'(4) Trade splits: investigate whether unified_star.fact_player_season_stats team_id=0 rows are combined TOT totals; for a sample of multi-team player-seasons verify combined pts == SUM(per-team pts); measure, assert within baseline. (5) Game->season points MODERN era (season ending year >= 1997): SUM player game points per (player, season ending year) vs fact_player_season_stats pts; HARD due to mixed encoding + team_id=0; restrict scope, measure mismatch count, assert <= measured module-level baseline with a clear comment. If not meaningful, implement team-game->team-season instead and document. Prefer fewer correct checks. Keep GREEN.' },

  { id: 'layer3', kind: 'test', file: 'backend/tests/invariant/test_layer3_referential_integrity.py', target: 'tests/invariant/test_layer3_referential_integrity.py', brief:
'Layer 3 referential integrity + PK uniqueness. FK orphans (LEFT JOIN dim, dim IS NULL): ' +
'fact_player_game_boxscore.player_id->dim_player assert <= kd[pgame_player_orphan]=189; .game_id->dim_game assert <= kd[pgame_game_orphan]=4861; ' +
'fact_player_season_stats.player_id->dim_player assert ==0; .team_id->dim_team EXCLUDING team_id=kd.SENTINEL_TEAM_ID assert ==0 (the 31730 orphans are all the sentinel); ' +
'fact_team_game_boxscore.team_id->dim_team and .game_id->dim_game MEASURE assert ==0 or baseline; fact_game_quarter_scores.game_id->dim_game and .team_id->dim_team MEASURE. ' +
'PK uniqueness assert ==0 dup groups: dim_player.player_id, dim_team.team_id, dim_game.game_id (measure; baseline if dups exist). Measure first; pin to reality; GREEN.' },

  { id: 'layer4', kind: 'test', file: 'backend/tests/invariant/test_layer4_distributional.py', target: 'tests/invariant/test_layer4_distributional.py', brief:
'Layer 4 distributional + uniqueness. Outlier bounds (measure baseline of legit-extreme rows): single-game points<=100; single-game min<=65 (gate season ending year>=1952, exclude null/neg min); season gp<=110. ' +
'Duplicate detection: (player_id,game_id) unique in fact_player_game_boxscore -> assert ==0 dup groups or measured baseline. IMPORTANT: investigate fact_player_season_stats uniqueness of (player_id, normalized ending year via kd.SEASON_END_YEAR_SQL, team_id, is_playoffs) - the mixed 1946-47/1947 encoding likely creates DUPLICATE rows for the same player-season; MEASURE the dup count, assert <= measured module-level baseline, and DOCUMENT this prominently as a real uniqueness divergence. ' +
'Null-rate: assert null rate of fact_player_game_boxscore.min <= a measured baseline (~10%); document. Keep GREEN; surface the duplicate-encoding finding in findings.' },

  { id: 'layer6', kind: 'test', file: 'backend/tests/invariant/test_layer6_pbp_derivation.py', target: 'tests/invariant/test_layer6_pbp_derivation.py', brief:
'Layer 6 play-by-play derivation, gated to season ending year >= kd.PBP_ERA_START_END_YEAR (1997). Derive from unified_star.fact_pbp_events (NOT api.v_shot_chart - it lacks keys). ' +
'(1) Final score: for modern games max(score_home)/max(score_away) over PBP == the two fact_team_game_boxscore.pts for that game; MEASURE mismatched games, assert <= measured baseline. ' +
'(2) Made-FG: per (game_id,player_id) modern, count(PBP WHERE is_field_goal AND shot_result=Made) == fact_player_game_boxscore.fgm; MEASURE mismatched player-games, assert <= measured baseline (validated working on game 0022501193). ' +
'(3) Quarter->final: SUM(fact_game_quarter_scores.pts) per (game_id,team_id) == fact_team_game_boxscore.pts; MEASURE assert ==0 or baseline. ' +
'PBP scans are large: restrict modern checks to a bounded sample (a few hundred recent games via a season filter / id LIMIT) so tests run <30s. Document the sample scope. Keep GREEN.' },

  { id: 'layer7', kind: 'test', file: 'backend/tests/invariant/test_layer7_gameid_schedule.py', target: 'tests/invariant/test_layer7_gameid_schedule.py', brief:
'Layer 7 game-id and schedule structural validation on unified_star.dim_game (+ fact_team_game_boxscore for 2-rows). ' +
'Format: assert 0 game_id NOT matching the 10-digit pattern 00 + type-digit(1-6) + 7 digits (use regexp_matches; type 6 = NBA Cup). ' +
'Uniqueness: assert 0 duplicate game_id groups in dim_game. Two team rows: assert 0 games in fact_team_game_boxscore with team-row-count <> 2. ' +
'Embedded-season: the 2-digit code at substr(game_id,4,2) is the START year (1900+YY if YY>=46 else 2000+YY); assert mismatch vs dim_game.season_year start-year <= kd[gameid_embedded_season_mismatch]=33. ' +
'Season-type digit vs label: map substr(game_id,3,1) via kd.GAME_ID_SEASON_TYPE and compare to dim_game.season_type labels (2->Regular,4->Playoffs,1->Preseason,6->Cup; All-Star/Play-In may be absent); MEASURE mismatches, assert <= measured baseline, document. ' +
'Optional schedule completeness: for one modern season (2018-19) assert each team has 82 Regular games in fact_team_game_boxscore; measure, document exceptions. Keep GREEN.' },

  { id: 'layer8', kind: 'test', file: 'backend/tests/invariant/test_layer8_advanced_recompute.py', target: 'tests/invariant/test_layer8_advanced_recompute.py', brief:
'Layer 8 advanced-metric recomputation (stored == recomputed within tolerance; guard divide-by-zero with NULLIF). ' +
'On api.v_canonical_player_season_totals (quote mixed-case cols): eFG% == (FG+0.5*3P)/FGA tol 0.001 (clean ==0); FG%==FG/FGA; 3P%==3P/3PA; FT%==FT/FTA; 2P%==2P/2PA tol 0.001 - MEASURE each, assert ==0 or measured baseline. ' +
'On api.v_team_standings: win_pct == wins/NULLIF(wins+losses,0) tol 0.0005 -> assert ==0 or measured baseline. ' +
'On api.v_canonical_team_season if inputs exist: TS% sanity 0..1.5. Only count rows where inputs non-null and denominators>0. Only assert relationships whose inputs exist in the view. Keep GREEN; document any metric not recomputable.' },

  { id: 'gatefix', kind: 'source', file: 'backend/basketball_data_emporium/server/status_audit.py', target: '-q', brief:
'Fix the latent gate bug in backend/basketball_data_emporium/server/status_audit.py read_audit_status(). Currently when audit.dq_results has no status-like column it sets dq_status=present (treated as passing) merely because rows EXIST - even with 248 CRITICAL violations. ' +
'Real audit.dq_results schema: (check_name VARCHAR, table_name VARCHAR, severity VARCHAR, row_count BIGINT, details VARCHAR, checked_at TIMESTAMP). No status column but there IS a severity column (CRITICAL/HIGH/MEDIUM/LOW/INFO) and row_count. ' +
'REQUIRED: when no status-like column exists, evaluate severity. A row is BLOCKING if upper(severity) in (CRITICAL,HIGH) AND row_count>0. A blocking row is ACCEPTED if its check_name appears in audit.discrepancy_known_divergence (if that table exists; inspect its columns defensively like the existing _columns helper and match on a check_name/name/check-like column). dq_status = failed if any unaccepted blocking row exists, else passed (was present). Keep the existing status-column path and dq-missing path unchanged. ' +
'Keep is_verified = run_passed AND dq_passed AND not is_stale; dq failures map to state=failed reason=latest_dq_failed. Preserve the dataclass shape and ALL field names (frontend depends on them). Update the docstring. ' +
'THEN run the FULL backend suite (cd backend && ./.venv/Scripts/python.exe -m pytest -q) and FIX any regressions in tests/test_status.py, tests/contract/*, tests/audit/* so all stay green (the stub conn returns empty dq_results, so dq resolves to unknown/missing there, NOT failed). Do not weaken tests to hide failures. Report read_audit_status before/after behavior on the real snapshot (now correctly NOT verified due to CRITICAL dq).' },

  { id: 'recon', kind: 'source', file: 'backend/basketball_data_emporium/verification/reconcile_official.py', target: 'tests/verification', brief:
'Build the Layer 5 cross-source reconciliation SCAFFOLD (offline-testable; NOT run in CI as a live call; no network required). Create backend/basketball_data_emporium/verification/__init__.py and reconcile_official.py. ' +
'reconcile_official.py contains: ENDPOINT_MAP mapping each warehouse object to its official nba_api endpoint + key fields to diff per methodology 8.2 (dim_player->CommonAllPlayers/CommonPlayerInfo; dim_game->LeagueGameLog; fact_player_game_boxscore->BoxScoreTraditionalV2; fact_player_season_stats->LeagueDashPlayerStats; fact_team_game_boxscore->BoxScoreTraditionalV2; v_team_standings->LeagueStandingsV3; v_franchise_leaders->FranchiseLeaders; fact_pbp_events->PlayByPlayV2; shots->ShotChartDetail). ' +
'Pure-function normalization helpers (UNIT-TESTABLE, no network): minutes_to_decimal(MM:SS->float), normalize_season(2023-24 <-> ending year), uppercase->warehouse column maps for box score and season totals. ' +
'reconcile(fetcher, expected_rows, fields, tolerances) takes an INJECTED fetcher callable (tests pass a fake offline fetcher), compares official vs warehouse with per-field tolerances (exact counting, 0.001 pct), returns discrepancy records shaped like audit.metric_discrepancy (entity, field, expected, actual, severity). get_official_fetcher() lazily imports nba_api and raises a clear actionable error if missing (OPTIONAL dep). Module docstring marks this a SCAFFOLD pointing to ideas/data-verification-methodology.md section 8. ' +
'Add to backend/pyproject.toml under [project.optional-dependencies]: reconcile = [nba_api>=1.4]. ' +
'Write backend/tests/verification/test_reconcile_offline.py: a NO-DB NO-network pure-python unit test exercising minutes_to_decimal, normalize_season, the column maps, and reconcile() with a FAKE fetcher (assert it flags a planted discrepancy and passes a matching one). It MUST run in CI. Self-verify: cd backend && ./.venv/Scripts/python.exe -m pytest tests/verification -q.' },
]

phase('Build')
const results = await pipeline(
  ITEMS,
  (item) => agent(
    CONTRACT + '\n\n=== YOUR TASK (' + item.id + ') ===\nCreate/edit: ' + item.file + '\n' + item.brief +
    '\n\nWork end-to-end: measure against the DB, write the file(s), self-verify with pytest until fully green (0 failed) and skipping-without-DB. Return the structured summary.',
    { label: 'build:' + item.id, phase: 'Build', schema: BUILD_SCHEMA, effort: 'high' }
  ).then((r) => ({ item: item, build: r })),
  (prev) => agent(
    'Adversarially REVIEW task ' + prev.item.id + ' (file: ' + prev.item.file + '). Contract:\n' + CONTRACT +
    '\n\nBuilder reported: ' + JSON.stringify(prev.build) +
    '\n\nVerify rigorously: 1) Read the file(s); confirm assertions are TIGHT and MEANINGFUL (no assert True, no >=0, no vacuous checks). ' +
    '2) Confirm table/column names match the SCHEMA exactly (quoted mixed-case api cols; fouls_personal not pf on player boxscore; shot_result Made/Missed; mixed season_year via kd helpers). ' +
    '3) Re-run pytest: cd backend && ./.venv/Scripts/python.exe -m pytest ' + prev.item.target + ' -q --no-header ; confirm 0 failed. ' +
    '4) For test modules confirm it SKIPS (not errors) with env DUCKDB_PATH=/nonexistent/x.duckdb BASKETBALL_DATA_DB_PATH=/nonexistent/x.duckdb. ' +
    '5) For the gate fix confirm the FULL backend suite is green and severity logic is correct (CRITICAL dq -> not verified). ' +
    'If you find issues, FIX them yourself, re-run, and report. A loose/wrong check is a FAIL even if pytest is green. Return the structured verdict.',
    { label: 'review:' + prev.item.id, phase: 'Review', schema: REVIEW_SCHEMA, effort: 'high' }
  ).then((review) => ({ id: prev.item.id, file: prev.item.file, build: prev.build, review: review }))
)

return results.filter(Boolean).map((r) => ({
  id: r.id, file: r.file,
  built: r.build && r.build.self_verify_passed,
  review_ok: r.review && r.review.ok, passes: r.review && r.review.passes_pytest,
  tight: r.review && r.review.assertions_tight, issues: (r.review && r.review.issues) || [],
  verdict: r.review && r.review.verdict,
}))
