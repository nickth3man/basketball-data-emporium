# data/ingest — external source ingestion

Manifest-driven loader for pulling external data files into `data/nba.duckdb`
and wiring their ids into the warehouse crosswalks. Adding a new source is a
YAML file, not code.

## Running

From the repo root, with the web dev server **stopped** (DuckDB needs the
write lock):

```
python data/ingest/ingest.py <source>            # load + crosswalk + report
python data/ingest/ingest.py <source> --dry-run  # count rows only, no writes
python data/ingest/validate_bridges.py           # invariant checks on all bridges
python data/ingest/validate_bridges.py --fix     # also dedupe exact-duplicate rows
```

`<source>` names a manifest at `sources/<source>.yaml`. Reports land in
`reports/` (git-ignored artifacts of each run).

## What a run does

1. **Registers the source** in `dim_source_system` (name, description, id
   conventions, load timestamps).
2. **Stages every file** into `stg_<source>_<table>`, columns verbatim. If
   the manifest names a `game_id_column`, a normalized `game_id` column
   (VARCHAR, zero-padded to 10) is added — NBA game ids stored as integers
   lose their leading zeros.
3. **Resolves crosswalks.** Distinct source ids are collected from the
   columns the manifest declares and matched to warehouse ids:
   - players → `bridge_player_source_id` (exact `person_id` vs `dim_all_players`)
   - teams → `bridge_team_source_id` (exact `team_id` vs
     `dim_team ∪ dim_team_history ∪ dim_defunct_team`, then unique
     normalized-name fallback for sources that invent their own team ids)
   - games → `bridge_game_source_id` (zero-padded exact vs `dim_game`)

   Unmatched ids are kept as `is_unresolved` rows — coverage is auditable,
   never silently lossy. Re-running a source replaces only that source's
   bridge rows (idempotent).
4. **Logs and reports**: per-file row counts go to `meta_ingest_log`; a
   markdown report with resolution rates and unresolved samples goes to
   `reports/`.

## Adding a new source

1. Drop the files somewhere in the repo (convention: `data/external/`).
2. Write `sources/<name>.yaml` — copy `kaggle_nba.yaml` and edit:
   - `source`: the `source_system` key used in all bridge rows
   - `files`: one entry per file (`table` → staged as `stg_<source>_<table>`,
     `path`, optional `csv` overrides, optional `game_id_column`)
   - `entities`: where player/team/game ids live (`table`, `id` column,
     optional `name` SQL expression for fallback matching + readable reports)
3. `python data/ingest/ingest.py <name> --dry-run` to sanity-check, then run
   for real and read the report.
4. `python data/ingest/validate_bridges.py` to confirm the bridges still hold.

Joining staged data to the warehouse afterwards always goes through the
bridge, e.g.:

```sql
SELECT d.full_name, s.points
FROM stg_kaggle_nba_player_statistics s
JOIN bridge_player_source_id b
  ON b.source_system = 'kaggle_nba'
 AND b.source_player_id = CAST(s.personId AS VARCHAR)
JOIN dim_all_players d ON d.person_id = b.person_id
```

## Crosswalk contract (what "ironclad" means here)

`validate_bridges.py` enforces, for every `bridge_*_source_id` table:

- one row per `(source_system, source id)` — no duplicate mappings
- every resolved warehouse id exists in its dim
  (`dim_all_players` / team dims union / `dim_game`)
- unresolved rows carry `is_unresolved = true` and a reason (team/game)
- no exact-duplicate rows in any `bridge_*` table (the per-source ESPN
  bridges had literal duplicate rows before this suite existed)

Run it after any ingest or any hand edit to a bridge table.
