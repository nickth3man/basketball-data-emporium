# NBA Warehouse Rebuild

`build_nba.py` builds a fresh DuckDB-only warehouse from a raw source
warehouse plus local staged/audit/anchor files. It does not preserve the
current Express app query contract.

**`data/nba.duckdb` is itself a built artifact of this script.** The original
raw warehouse it was built from has been archived outside this repo, so
there's no default `--source-db` -- pass the archived raw warehouse path
explicitly if you need to rebuild from scratch.

## Outputs

- `data/nba.duckdb` - generated local artifact, ignored by git.
- `src_*` - lossless source layer. In `copy` mode these are materialized
  tables with `_*` provenance columns (`_ingest_run_id`, `_source_system`,
  `_source_table`, `_source_record_hash`, `_normalized_game_id`); in `view`
  mode they are source views for smoke/canonical builds.
- `map_*` - standardized source-id maps for players, teams, games, BBR ids,
  and game-team sides.
- `dim_*` / `fact_*` - canonical dimensions and facts.
- `mart_*` / `analytics_*` - rebuilt convenience marts from canonical facts.
- `meta_*` - build runs, source row counts, table fates, lineage, quality
  checks, metric definitions, and known gaps.

## Commands

Fast canonical build, safe on low disk:

```sh
python data/audit/build_nba.py --source-db /path/to/raw.duckdb --replace --source-mode view --skip-source-hashes
```

Full portable source-copy build:

```sh
python data/audit/build_nba.py --source-db /path/to/raw.duckdb --replace
```

The full build needs substantial working space because it copies the raw
warehouse into `src_*`, adds row provenance/hash columns, and also
materializes canonical facts and marts. The builder refuses copy mode unless
free space is at least `max(60GB, 3x source DB size)`; override only when you
have verified temp/WAL headroom:

```sh
python data/audit/build_nba.py --source-db /path/to/raw.duckdb --replace --force-low-disk
```

## Current Local Run

On 2026-07-04, the full copy-mode build completed successfully (this is the
run that produced the database now living at `data/nba.duckdb`; it was built
as `data/nba_v2.duckdb` and later renamed once the raw source it replaced was
archived):

- Build run: `nba_v2_20260704T210042Z`.
- `source_mode = 'copy'`; `source_hashes = true`.
- 547 raw base tables cataloged with exact row counts and materialized as
  `src_*` tables.
- Local anchors/audit outputs loaded as source tables.
- 621 materialized tables created.
- Source row parity mismatches: 0.
- 24 quality checks passed, 0 failed (21 original + 3 added when the
  `map_player_source_id` unresolved-reason crosswalk fix landed).

An earlier copy-mode attempt exposed that `duckdb_tables().estimated_size` is
not an exact row count for every source table. The builder now uses exact
`count(*)` source counts before copying, so the source parity gate compares
real counts.

## Quality Gates

The builder records checks in `meta_quality_check`, including:

- source row parity
- source table fate classification
- bridge/source-map uniqueness
- Kaggle player/game resolution floors
- game row parity and 2025-26 playoff completeness
- PBP row parity
- shot-to-game joins
- no player-season fan-out
- standings parity with BBR W/L
- Luka Doncic award recovery
- Kobe/Harden jersey history checks
- draft duplicate slot classification
- team-era abbreviation correctness
- no reconcilable basketball_reference/json_slug rows left unresolved
  against `map_player_bbr` (`no_bbr_reconcilable_unresolved`)
- Kaggle unresolved rows carry a specific classified reason, not a generic
  fallback (`kaggle_unresolved_reason_specificity`)
- residual BBR coverage gap stays within its documented ceiling
  (`bbr_residual_gap_within_ceiling`)
