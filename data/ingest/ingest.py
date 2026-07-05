#!/usr/bin/env python
"""Manifest-driven ingestion of external data sources into data/nba.duckdb.

Run from the repo root with the app dev server STOPPED (needs the write lock):

    python data/ingest/ingest.py kaggle_nba
    python data/ingest/ingest.py kaggle_nba --dry-run   # profile only, no writes
    python data/ingest/ingest.py --reconcile-bbr        # fix stale bbr player rows, no manifest needed

Reads data/ingest/sources/<source>.yaml and then:

  1. registers the source in dim_source_system
  2. loads every file into stg_<source>_<table> (columns kept verbatim; a
     normalized 10-char game_id column is added when the manifest names a
     game-id column)
  3. resolves the source's ids to warehouse ids and records the result in the
     generic crosswalks:
        players -> bridge_player_source_id   (existing table, shape unchanged)
        teams   -> bridge_team_source_id     (created on first run)
        games   -> bridge_game_source_id     (created on first run)
     Re-running a source deletes and re-inserts only that source's rows.
  4. appends per-file row counts to meta_ingest_log and writes a markdown
     report to data/ingest/reports/.

Resolution strategies are fixed per entity type (see resolve_* below); the
manifest only declares WHERE the ids live. Unmatched ids are kept as
is_unresolved rows so coverage is auditable rather than silently lossy.
"""

import argparse
import datetime
import os
import sys

import duckdb
import yaml

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
DB_PATH = os.path.join(REPO_ROOT, "data", "nba.duckdb")
SOURCES_DIR = os.path.join(os.path.dirname(__file__), "sources")
REPORTS_DIR = os.path.join(os.path.dirname(__file__), "reports")

# Accent-fold + lowercase + strip punctuation/suffixes, same normalisation as
# data/audit/build_crosswalk.sql so name matches agree across tooling.
NORM_NAME_MACRO = r"""
CREATE OR REPLACE TEMP MACRO norm_name(s) AS trim(regexp_replace(
  regexp_replace(
    regexp_replace(lower(strip_accents(CAST(s AS VARCHAR))), '[.''`,-]', '', 'g'),
    '\s+(jr|sr|ii|iii|iv|v)$', ''),
  '\s+', ' ', 'g'));
"""


def load_manifest(source):
    path = os.path.join(SOURCES_DIR, f"{source}.yaml")
    if not os.path.exists(path):
        sys.exit(f"no manifest at {path}; see data/ingest/README.md")
    with open(path, encoding="utf-8") as f:
        m = yaml.safe_load(f)
    if m.get("source") != source:
        sys.exit(f"manifest 'source' ({m.get('source')}) != requested ({source})")
    return m


def reader_sql(file_spec, csv_defaults):
    """read_csv(...) / read_parquet(...) expression for a manifest file entry."""
    path = os.path.join(REPO_ROOT, file_spec["path"]).replace("\\", "/")
    if path.endswith(".parquet"):
        return f"read_parquet('{path}')"
    opts = dict(csv_defaults or {})
    opts.update(file_spec.get("csv", {}))
    rendered = ", ".join(
        f"{k}={v}" if isinstance(v, (int, float)) else f"{k}='{v}'"
        for k, v in opts.items()
    )
    return f"read_csv('{path}'" + (f", {rendered})" if rendered else ")")


def stage_tables(con, manifest, dry_run):
    source = manifest["source"]
    loaded = []
    for spec in manifest["files"]:
        table = f"stg_{source}_{spec['table']}"
        src = reader_sql(spec, manifest.get("csv_defaults"))
        gid = spec.get("game_id_column")
        # lpad restores leading zeros lost when gameId was parsed as BIGINT;
        # already-10-char varchar ids pass through unchanged.
        gid_col = (
            f", lpad(trim(CAST({gid} AS VARCHAR)), 10, '0') AS game_id" if gid else ""
        )
        if dry_run:
            n = con.execute(f"SELECT count(*) FROM {src}").fetchone()[0]
        else:
            con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT *{gid_col} FROM {src}")
            n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            con.execute(
                "INSERT INTO meta_ingest_log VALUES (?, ?, ?, ?, current_timestamp)",
                [source, table, spec["path"], n],
            )
        loaded.append((table, spec["path"], n))
        print(f"  {table:<50} {n:>12,} rows")
    return loaded


def ensure_infrastructure(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS dim_source_system (
            source_system   VARCHAR PRIMARY KEY,
            description     VARCHAR,
            id_notes        VARCHAR,
            first_loaded_at TIMESTAMP,
            last_loaded_at  TIMESTAMP
        )""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS meta_ingest_log (
            source_system VARCHAR,
            table_name    VARCHAR,
            file_path     VARCHAR,
            rows_loaded   BIGINT,
            loaded_at     TIMESTAMP
        )""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS bridge_team_source_id (
            team_id           BIGINT,
            source_system     VARCHAR,
            source_team_id    VARCHAR,
            source_team_name  VARCHAR,
            match_method      VARCHAR,
            match_confidence  DOUBLE,
            is_ambiguous      BOOLEAN,
            is_unresolved     BOOLEAN,
            unresolved_reason VARCHAR
        )""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS bridge_game_source_id (
            game_id           VARCHAR,
            source_system     VARCHAR,
            source_game_id    VARCHAR,
            match_method      VARCHAR,
            match_confidence  DOUBLE,
            is_ambiguous      BOOLEAN,
            is_unresolved     BOOLEAN,
            unresolved_reason VARCHAR
        )""")
    # bridge_player_source_id predates this pipeline (shape "unchanged" per the
    # module docstring) and lacks unresolved_reason, unlike the team/game
    # bridges above -- backfill it in place so unresolved player rows are
    # auditable the same way.
    has_reason_col = con.execute(
        """
        SELECT count(*) FROM information_schema.columns
        WHERE table_name = 'bridge_player_source_id' AND column_name = 'unresolved_reason'
        """
    ).fetchone()[0]
    if not has_reason_col:
        con.execute("ALTER TABLE bridge_player_source_id ADD COLUMN unresolved_reason VARCHAR")


def register_source(con, manifest):
    con.execute(
        """
        INSERT INTO dim_source_system
        VALUES (?, ?, ?, current_timestamp, current_timestamp)
        ON CONFLICT (source_system) DO UPDATE SET
            description = excluded.description,
            id_notes = excluded.id_notes,
            last_loaded_at = excluded.last_loaded_at
        """,
        [
            manifest["source"],
            manifest.get("description", "").strip(),
            manifest.get("id_notes", "").strip(),
        ],
    )


def collect_ids(con, manifest, entity):
    """Distinct (source_id, source_name) pairs across the manifest's columns."""
    parts = []
    for loc in manifest.get("entities", {}).get(entity, []):
        table = f"stg_{manifest['source']}_{loc['table']}"
        name = loc.get("name", "NULL")
        extra = f" AND ({loc['where']})" if loc.get("where") else ""
        parts.append(
            f"SELECT DISTINCT trim(CAST({loc['id']} AS VARCHAR)) AS source_id,"
            f" CAST({name} AS VARCHAR) AS source_name FROM {table}"
            f" WHERE {loc['id']} IS NOT NULL AND trim(CAST({loc['id']} AS VARCHAR)) <> ''{extra}"
        )
    if not parts:
        return False
    # one name per id: pick the longest non-null (most descriptive) variant
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _src_ids AS
        SELECT source_id,
               arg_max(source_name, length(source_name)) AS source_name
        FROM ({' UNION ALL '.join(parts)})
        GROUP BY 1""")
    return True


def resolve_players(con, source):
    con.execute("DELETE FROM bridge_player_source_id WHERE source_system = ?", [source])
    con.execute(
        """
        INSERT INTO bridge_player_source_id
        SELECT d.person_id, ?, s.source_id,
               CASE WHEN d.person_id IS NOT NULL THEN 'exact_person_id' END,
               CASE WHEN d.person_id IS NOT NULL THEN 1.0 END,
               false,
               d.person_id IS NULL,
               CASE WHEN d.person_id IS NULL THEN 'not_in_dim_all_players' END
        FROM _src_ids s
        LEFT JOIN (SELECT DISTINCT person_id FROM dim_all_players) d
          ON try_cast(s.source_id AS BIGINT) = d.person_id
        """,
        [source],
    )


def resolve_teams(con, source):
    """Exact NBA.com id first; unique name match against warehouse team dims
    (catches sources that invent their own ids for defunct/foreign teams);
    anything left is recorded unresolved."""
    con.execute("DELETE FROM bridge_team_source_id WHERE source_system = ?", [source])
    con.execute("""
        CREATE OR REPLACE TEMP TABLE _wh_teams AS
        SELECT team_id, norm_name(team_full) AS nname
        FROM (
            SELECT team_id, city || ' ' || nickname AS team_full FROM dim_team_era
            UNION
            SELECT team_id, team_city || ' ' || team_name FROM src_dim_defunct_team
            UNION
            SELECT team_id, full_name FROM dim_team
        )""")
    con.execute(
        """
        INSERT INTO bridge_team_source_id
        WITH exact AS (
            SELECT s.source_id, s.source_name, d.team_id
            FROM _src_ids s
            LEFT JOIN (SELECT DISTINCT team_id FROM _wh_teams) d
              ON try_cast(s.source_id AS BIGINT) = d.team_id
        ),
        by_name AS (
            SELECT e.source_id,
                   min(w.team_id) AS name_team_id,
                   count(DISTINCT w.team_id) AS n_candidates
            FROM exact e
            JOIN _wh_teams w ON norm_name(e.source_name) = w.nname
            WHERE e.team_id IS NULL AND e.source_name IS NOT NULL
            GROUP BY 1
        )
        SELECT coalesce(e.team_id, CASE WHEN n.n_candidates = 1 THEN n.name_team_id END),
               ?, e.source_id, e.source_name,
               CASE WHEN e.team_id IS NOT NULL THEN 'exact_team_id'
                    WHEN n.n_candidates = 1 THEN 'name_unique'
                    WHEN n.n_candidates > 1 THEN 'name_ambiguous' END,
               CASE WHEN e.team_id IS NOT NULL THEN 1.0
                    WHEN n.n_candidates = 1 THEN 0.9 END,
               coalesce(n.n_candidates, 0) > 1,
               e.team_id IS NULL AND coalesce(n.n_candidates, 0) <> 1,
               CASE WHEN e.team_id IS NULL AND coalesce(n.n_candidates, 0) = 0
                         THEN 'no_id_or_name_match'
                    WHEN coalesce(n.n_candidates, 0) > 1 THEN 'name_matches_multiple_teams' END
        FROM exact e
        LEFT JOIN by_name n USING (source_id)
        """,
        [source],
    )


def resolve_games(con, source):
    con.execute("DELETE FROM bridge_game_source_id WHERE source_system = ?", [source])
    con.execute(
        """
        INSERT INTO bridge_game_source_id
        SELECT d.game_id, ?, s.source_id,
               CASE WHEN d.game_id IS NOT NULL THEN 'exact_game_id_padded' END,
               CASE WHEN d.game_id IS NOT NULL THEN 1.0 END,
               false,
               d.game_id IS NULL,
               CASE WHEN d.game_id IS NULL THEN 'not_in_dim_game' END
        FROM (SELECT lpad(source_id, 10, '0') AS padded, * FROM _src_ids) s
        LEFT JOIN (SELECT DISTINCT game_id FROM dim_game) d ON s.padded = d.game_id
        """,
        [source],
    )


def reconcile_player_bbr_matches(con):
    """Fix up stale basketball_reference/json_slug rows in bridge_player_source_id
    using bridge_player_bbr, which already resolves these ids correctly (it
    tie-breaks phantom duplicate dim_all_players rows -- created by NBA teams'
    preseason exhibition games against European clubs -- by games played).
    bridge_player_source_id's own basketball_reference rows come from a
    name+birthdate matcher that isn't in this repo (lost/external ETL) and
    can't tell those phantom rows apart, so it correctly refused to guess;
    this reuses the answer bridge_player_bbr already has instead of
    re-deriving it. json_slug ids are ~all the same BBR slugs under a
    different source label, so both are reconciled the same way."""
    # map_player_bbr.is_preferred already carries this exact ranking,
    # computed once in build_nba.py's build_maps() -- read it directly
    # instead of re-deriving it here.
    con.execute(
        "CREATE OR REPLACE TEMP TABLE _preferred_bbr AS "
        "SELECT player_id AS nba_player_id, bbr_player_id FROM map_player_bbr WHERE is_preferred"
    )
    before = con.execute(
        """
        SELECT count(*) FROM bridge_player_source_id
        WHERE source_system IN ('basketball_reference', 'json_slug')
          AND (is_unresolved OR is_ambiguous)
        """
    ).fetchone()[0]
    con.execute(
        """
        UPDATE bridge_player_source_id b
        SET person_id = p.nba_player_id,
            match_method = 'reconciled_bridge_player_bbr',
            match_confidence = 0.95,
            is_ambiguous = false,
            is_unresolved = false,
            unresolved_reason = NULL
        FROM _preferred_bbr p
        WHERE b.source_system IN ('basketball_reference', 'json_slug')
          AND (b.is_unresolved OR b.is_ambiguous)
          AND b.source_player_id = p.bbr_player_id
        """
    )
    # rows this couldn't fix predate this pipeline (populated by a lost
    # external matcher) and never had a reason recorded at all -- backfill
    # one so "unresolved" stays auditable instead of silently NULL.
    con.execute(
        """
        UPDATE bridge_player_source_id
        SET unresolved_reason = 'no_bridge_player_bbr_candidate'
        WHERE source_system IN ('basketball_reference', 'json_slug')
          AND is_unresolved AND unresolved_reason IS NULL
        """
    )
    after = con.execute(
        """
        SELECT count(*) FROM bridge_player_source_id
        WHERE source_system IN ('basketball_reference', 'json_slug')
          AND (is_unresolved OR is_ambiguous)
        """
    ).fetchone()[0]
    print(f"reconcile_player_bbr_matches: {before - after:,} rows reconciled, {after:,} still unresolved/ambiguous "
          f"(no bridge_player_bbr match -- genuine bbr coverage gap)")


def classify_kaggle_unresolved_players(con):
    """Split kaggle_nba's generic unresolved reason into what's actually
    going on, using the same NBA-franchise-id-range convention already
    documented in data/ingest/sources/kaggle_nba.yaml (teamId 1610000000-
    1611000000 = real NBA franchise): a dominant teamId of '0' means an
    official/replay/unassigned-staff event; a real-franchise teamId means
    NBA coaching/support staff (real people, correctly not in the player
    dim); anything else is a genuine non-NBA opponent-club player from an
    international exhibition game (e.g. Real Madrid's Sergio Llull)."""
    con.execute(
        """
        UPDATE bridge_player_source_id b
        SET unresolved_reason = CASE
            WHEN dt.team_id IS NULL THEN 'not_in_dim_all_players_or_non_player_pbp_person'
            WHEN dt.team_id = '0' THEN 'non_player_official_or_unassigned_staff'
            WHEN try_cast(dt.team_id AS BIGINT) BETWEEN 1610000000 AND 1611000000 THEN 'non_player_nba_staff'
            ELSE 'exhibition_opponent_non_nba_player'
        END
        FROM (
            SELECT u.source_player_id AS pid, pbp.team_id
            FROM bridge_player_source_id u
            LEFT JOIN (
                SELECT pid, arg_max(teamId, cnt) AS team_id
                FROM (
                    SELECT trim(CAST(personId AS VARCHAR)) AS pid, teamId, count(*) AS cnt
                    FROM stg_kaggle_nba_play_by_play
                    GROUP BY 1, 2
                )
                GROUP BY 1
            ) pbp ON pbp.pid = u.source_player_id
            WHERE u.source_system = 'kaggle_nba' AND u.is_unresolved
        ) dt
        WHERE b.source_system = 'kaggle_nba' AND b.is_unresolved AND b.source_player_id = dt.pid
        """
    )
    counts = con.execute(
        """
        SELECT unresolved_reason, count(*) FROM bridge_player_source_id
        WHERE source_system = 'kaggle_nba' AND is_unresolved
        GROUP BY 1 ORDER BY 2 DESC
        """
    ).fetchall()
    print("classify_kaggle_unresolved_players:")
    for reason, n in counts:
        print(f"  {reason or '(unclassified)':<45} {n:>6,}")


RESOLVERS = {"player": resolve_players, "team": resolve_teams, "game": resolve_games}
BRIDGES = {
    "player": "bridge_player_source_id",
    "team": "bridge_team_source_id",
    "game": "bridge_game_source_id",
}


def crosswalk_stats(con, entity, source):
    bridge = BRIDGES[entity]
    total, resolved = con.execute(
        f"""SELECT count(*), count(*) FILTER (WHERE NOT is_unresolved)
            FROM {bridge} WHERE source_system = ?""",
        [source],
    ).fetchone()
    unresolved = con.execute(
        f"SELECT * FROM {bridge} WHERE source_system = ? AND is_unresolved LIMIT 15",
        [source],
    ).df()
    return total, resolved, unresolved


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", nargs="?", help="manifest name under data/ingest/sources/")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--dry-run", action="store_true", help="count rows only, no writes")
    ap.add_argument("--resolve-only", action="store_true",
                    help="skip staging; re-run crosswalk resolution + report on already-staged tables")
    ap.add_argument("--reconcile-bbr", action="store_true",
                    help="reconcile stale basketball_reference/json_slug rows in "
                         "bridge_player_source_id against bridge_player_bbr, then exit "
                         "(no manifest/source needed)")
    args = ap.parse_args()

    if args.reconcile_bbr:
        con = duckdb.connect(args.db, read_only=False)
        ensure_infrastructure(con)
        reconcile_player_bbr_matches(con)
        return
    if not args.source:
        ap.error("source is required unless --reconcile-bbr is passed")

    manifest = load_manifest(args.source)
    con = duckdb.connect(args.db, read_only=args.dry_run)
    con.execute(NORM_NAME_MACRO)

    if args.resolve_only:
        ensure_infrastructure(con)
        register_source(con, manifest)
        loaded = []
    else:
        print(f"== staging files for {args.source} ==")
        if not args.dry_run:
            ensure_infrastructure(con)
            register_source(con, manifest)
        loaded = stage_tables(con, manifest, args.dry_run)
        if args.dry_run:
            print("(dry run: nothing written)")
            return

    print("== resolving crosswalks ==")
    lines = []
    for entity, resolver in RESOLVERS.items():
        if not collect_ids(con, manifest, entity):
            continue
        resolver(con, args.source)
        if entity == "player" and args.source == "kaggle_nba":
            classify_kaggle_unresolved_players(con)
        total, resolved, unresolved = crosswalk_stats(con, entity, args.source)
        pct = 100.0 * resolved / total if total else 100.0
        print(f"  {entity:<7} {resolved:,}/{total:,} resolved ({pct:.2f}%)")
        lines.append((entity, total, resolved, unresolved))

    os.makedirs(REPORTS_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(REPORTS_DIR, f"{args.source}_{stamp}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# Ingest report: {args.source}\n\nRun: {datetime.datetime.now()}\n\n")
        f.write("## Files staged\n\n| table | file | rows |\n|---|---|---:|\n")
        for table, path, n in loaded:
            f.write(f"| {table} | {path} | {n:,} |\n")
        f.write("\n## Crosswalk resolution\n\n")
        for entity, total, resolved, unresolved in lines:
            f.write(f"### {entity}: {resolved:,}/{total:,} resolved\n\n")
            if len(unresolved):
                f.write("Sample unresolved rows:\n\n```\n")
                f.write(unresolved.to_string(index=False) + "\n```\n\n")
    print(f"report: {os.path.relpath(report_path, REPO_ROOT)}")


if __name__ == "__main__":
    main()
