#!/usr/bin/env python
"""Invariant checks for every bridge_*/map_* crosswalk table in data/nba.duckdb.

Run from the repo root (read-only unless --fix):

    python data/ingest/validate_bridges.py
    python data/ingest/validate_bridges.py --fix   # dedupe exact-duplicate rows

Checks:
  1. no duplicate logical rows in any bridge_*/map_* crosswalk table (logical
     = all columns except surrogate keys *_sk and bookkeeping
     created_at/last_verified_at)
  2. generic crosswalks (map_{player,team,game}_source_id): one row per
     (source_system, source_id)
  3. resolved crosswalk rows point at ids that exist in their dim
     (src_dim_all_players / dim_team+dim_team_era+src_dim_defunct_team / dim_game)
  4. resolved crosswalk rows carry a match_method; unresolved rows don't
     carry a warehouse id
  5. map_player_bbr: one BBR id per warehouse player; fan-in of two
     warehouse ids to one BBR id is WARN only when every such BBR id is a
     curated, known duplicate identity in dim_player_identity_merge (Vaught,
     O'Bannon, Werdann, Rambis) -- FAIL on any new, uncurated one, and write
     it to data/audit/out/duplicate_identity_candidates.csv for review
  6. src_bridge_player_team_season: unique (player_id, team_id, season_year,
     jersey_number) -- mid-season number changes are legitimately two rows;
     report-only (this is a lossless src_* copy, --fix does not touch it)

--fix rewrites tables that fail check 1 in place (DELETE + re-INSERT keeps
constraints), keeping one row per logical group (lowest surrogate key).
Everything else is report-only: resolution problems should be fixed in the
source manifest or matching logic, not by hand-editing crosswalk rows.

Exit code 0 = all PASS (warnings allowed), 1 = at least one FAIL.
"""

import argparse
import csv
import os
import sys

import duckdb

DB_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "nba.duckdb")
)
OUT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "audit", "out"))
BOOKKEEPING = {"created_at", "last_verified_at"}

results = []  # (status, check, table, detail)


def record(status, check, table, detail=""):
    results.append((status, check, table, detail))
    print(f"  [{status:^4}] {check:<28} {table:<28} {detail}")


def logical_columns(con, table):
    cols = [
        r[0]
        for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = ? ORDER BY ordinal_position",
            [table],
        ).fetchall()
    ]
    logical = [c for c in cols if not c.endswith("_sk") and c not in BOOKKEEPING]
    return cols, logical


def q(cols):
    return ", ".join(f'"{c}"' for c in cols)


def check_exact_dupes(con, table, fix):
    cols, logical = logical_columns(con, table)
    total = con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
    distinct = con.execute(
        f'SELECT count(*) FROM (SELECT DISTINCT {q(logical)} FROM "{table}")'
    ).fetchone()[0]
    dupes = total - distinct
    if dupes == 0:
        record("PASS", "no_duplicate_rows", table, f"{total:,} rows")
        return
    if not fix:
        record("FAIL", "no_duplicate_rows", table, f"{dupes:,} duplicate rows (of {total:,}); rerun with --fix")
        return
    sk = next((c for c in cols if c.endswith("_sk")), logical[0])
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _dedup AS
        SELECT {q(cols)} FROM (
            SELECT *, row_number() OVER (PARTITION BY {q(logical)} ORDER BY "{sk}") AS _rn
            FROM "{table}"
        ) WHERE _rn = 1""")
    con.execute(f'DELETE FROM "{table}"')
    con.execute(f'INSERT INTO "{table}" SELECT {q(cols)} FROM _dedup')
    record("PASS", "no_duplicate_rows", table, f"fixed: removed {dupes:,} duplicate rows, {distinct:,} kept")


# bridge_{player,team,game}_source_id were renamed AND reshaped to
# map_{player,team,game}_source_id in the 2026-07-04 schema rebuild: the
# per-entity wh/src id columns were unified to player_id/team_id/game_id +
# source_id, and the is_unresolved boolean became a resolution_status
# string ('resolved'/'ambiguous'/'unresolved').
CROSSWALKS = {
    "map_player_source_id": {
        "wh_id": "player_id",
        "src_id": "source_id",
        "dim": "(SELECT DISTINCT person_id AS wid FROM src_dim_all_players)",
    },
    "map_team_source_id": {
        "wh_id": "team_id",
        "src_id": "source_id",
        "dim": """(SELECT team_id AS wid FROM dim_team
                   UNION SELECT team_id FROM dim_team_era
                   UNION SELECT team_id FROM src_dim_defunct_team)""",
    },
    "map_game_source_id": {
        "wh_id": "game_id",
        "src_id": "source_id",
        "dim": "(SELECT DISTINCT game_id AS wid FROM dim_game)",
    },
}


def check_crosswalk(con, table, spec):
    wh, src = spec["wh_id"], spec["src_id"]
    dupes = con.execute(f"""
        SELECT count(*) FROM (
            SELECT source_system, {src} FROM "{table}"
            GROUP BY 1, 2 HAVING count(*) > 1
        )""").fetchone()[0]
    if dupes:
        sample = con.execute(f"""
            SELECT source_system, {src}, count(*) AS n FROM "{table}"
            GROUP BY 1, 2 HAVING count(*) > 1 ORDER BY n DESC LIMIT 3""").fetchall()
        record("FAIL", "unique_source_id", table, f"{dupes:,} source ids mapped more than once, e.g. {sample}")
    else:
        record("PASS", "unique_source_id", table)

    orphans = con.execute(f"""
        SELECT count(*) FROM "{table}" b
        LEFT JOIN {spec['dim']} d ON b.{wh} = d.wid
        WHERE b.{wh} IS NOT NULL AND d.wid IS NULL""").fetchone()[0]
    record("FAIL" if orphans else "PASS", "resolved_ids_exist_in_dim", table,
           f"{orphans:,} rows point at missing dim ids" if orphans else "")

    bad_flags = con.execute(f"""
        SELECT count(*) FROM "{table}"
        WHERE (resolution_status = 'unresolved' AND {wh} IS NOT NULL)
           OR (resolution_status != 'unresolved' AND ({wh} IS NULL OR match_method IS NULL))
        """).fetchone()[0]
    record("FAIL" if bad_flags else "PASS", "flags_consistent", table,
           f"{bad_flags:,} rows with contradictory resolved/unresolved state" if bad_flags else "")

    _, logical = logical_columns(con, table)
    if "unresolved_reason" in logical:
        missing_reason = con.execute(f"""
            SELECT count(*) FROM "{table}"
            WHERE resolution_status = 'unresolved' AND unresolved_reason IS NULL""").fetchone()[0]
        record("FAIL" if missing_reason else "PASS", "unresolved_reason_populated", table,
               f"{missing_reason:,} unresolved rows with no unresolved_reason" if missing_reason else "")

    info = con.execute(f"""
        SELECT source_system,
               count(*) FILTER (WHERE resolution_status != 'unresolved') || '/' || count(*)
        FROM "{table}" GROUP BY 1 ORDER BY 1""").fetchall()
    record("INFO", "coverage", table, "; ".join(f"{s}: {c}" for s, c in info))


def write_duplicate_identity_candidates(con, table, bbr_ids):
    """Mirrors data/audit/out/override_candidates.csv's shape: enough
    context (name, games played, career span) for a human to decide the
    canonical direction before adding a row to dim_player_identity_merge."""
    rows = con.execute(f"""
        SELECT m.bbr_player_id, m.player_id, m.full_name,
               (SELECT count(*) FROM fact_player_game_box g WHERE g.player_id = m.player_id) AS gp,
               d.from_year, d.to_year
        FROM {table} m
        LEFT JOIN dim_player d ON d.player_id = m.player_id
        WHERE m.bbr_player_id IN ({",".join("?" * len(bbr_ids))})
        ORDER BY m.bbr_player_id, gp DESC
    """, bbr_ids).fetchall()
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "duplicate_identity_candidates.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["bbr_player_id", "player_id", "full_name", "gp", "from_year", "to_year"])
        w.writerows(rows)
    print(f"  wrote {len(rows)} candidate rows to {path}", file=sys.stderr)


def check_player_bbr(con):
    t = "map_player_bbr"
    fan_out = con.execute(f"""
        SELECT count(*) FROM (SELECT player_id FROM {t}
        GROUP BY 1 HAVING count(*) > 1)""").fetchone()[0]
    record("FAIL" if fan_out else "PASS", "one_bbr_id_per_player", t,
           f"{fan_out} warehouse players with multiple BBR ids" if fan_out else "")
    # Fan-in (one BBR id, multiple warehouse ids) is only a WARN when every
    # such BBR id is a curated, known duplicate identity in
    # dim_player_identity_merge. A *new*, uncurated one is a real signal
    # (undiscovered phantom-duplicate id) and FAILs instead of silently
    # joining the same WARN bucket -- see meta_known_gap:
    # bbr_duplicate_identity_phantom_ids.
    has_merge_table = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'dim_player_identity_merge'"
    ).fetchone()[0] > 0
    fan_in_ids = con.execute(f"""
        SELECT bbr_player_id FROM {t} GROUP BY 1 HAVING count(*) > 1""").fetchall()
    fan_in = len(fan_in_ids)
    if not fan_in:
        record("PASS", "one_player_per_bbr_id", t)
    elif has_merge_table:
        curated = {
            r[0] for r in con.execute("SELECT DISTINCT bbr_player_id FROM dim_player_identity_merge").fetchall()
        }
        uncurated = [bid for (bid,) in fan_in_ids if bid not in curated]
        if uncurated:
            write_duplicate_identity_candidates(con, t, uncurated)
            record("FAIL", "one_player_per_bbr_id", t,
                   f"{len(uncurated)} BBR ids shared by multiple warehouse ids with NO "
                   f"dim_player_identity_merge entry (new duplicate identity, needs curation): "
                   f"{uncurated} -- see data/audit/out/duplicate_identity_candidates.csv")
        else:
            record("WARN", "one_player_per_bbr_id", t,
                   f"{fan_in} BBR ids shared by multiple warehouse ids, all curated in dim_player_identity_merge")
    else:
        record("WARN", "one_player_per_bbr_id", t,
               f"{fan_in} BBR ids shared by multiple warehouse ids (dim_player_identity_merge not found -- "
               f"cannot distinguish known from new duplicates)")


def check_bbr_reconciliation(con):
    """map_player_source_id's basketball_reference/json_slug rows should
    never be unresolved/ambiguous when map_player_bbr already has a
    preferred match for that bbr id -- catches the case where ingest.py was
    re-run (e.g. a fresh kaggle_nba resolve) without also re-running
    --reconcile-bbr afterward. map_player_bbr.is_preferred already carries
    the ranking (computed once in build_nba.py's build_maps()); read it
    directly instead of re-deriving it here."""
    t = "map_player_source_id"
    stale = con.execute(f"""
        SELECT count(*) FROM {t} b
        WHERE b.source_system IN ('basketball_reference', 'json_slug')
          AND b.resolution_status IN ('unresolved', 'ambiguous')
          AND EXISTS (
              SELECT 1 FROM map_player_bbr r
              WHERE r.bbr_player_id = b.source_id AND r.is_preferred
          )
        """).fetchone()[0]
    record("FAIL" if stale else "PASS", "bbr_reconciliation_complete", t,
           f"{stale:,} rows have a map_player_bbr match but are still unresolved/ambiguous; "
           f"re-run: python data/ingest/ingest.py --reconcile-bbr" if stale else "")


def check_player_team_season(con, fix):
    # Key is (player, team, season, jersey): a mid-season number change is two
    # legitimate rows. The corruption seen in practice is position fan-out --
    # the same key repeated with position G, F, C and NULL after a bad join.
    #
    # bridge_player_team_season was renamed to src_bridge_player_team_season
    # (a lossless src_* copy with provenance columns) in the 2026-07-04
    # rebuild -- no canonical map_/resolved replacement exists for this
    # concept. --fix's old collapse-in-place logic would silently drop the
    # provenance columns, violating the src_* layer's lossless-copy
    # guarantee, so --fix is refused here; this check is report-only until a
    # canonical replacement exists.
    t = "src_bridge_player_team_season"
    dupes = con.execute(f"""
        SELECT count(*) FROM (SELECT player_id, team_id, season_year, jersey_number
        FROM {t} GROUP BY ALL HAVING count(*) > 1)""").fetchone()[0]
    if dupes == 0:
        record("PASS", "unique_player_team_season", t)
        return
    record("FAIL" if not fix else "WARN", "unique_player_team_season", t,
           f"{dupes:,} duplicated (player, team, season, jersey) keys; "
           f"--fix does not touch this src_* (lossless) table -- see comment above")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--fix", action="store_true", help="dedupe exact-duplicate rows")
    args = ap.parse_args()

    con = duckdb.connect(args.db, read_only=not args.fix)
    all_tables = [
        r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
    ]
    # The 2026-07-04 rebuild renamed every bridge_* table to map_*/src_* --
    # scan both prefixes so this still validates something on the live
    # warehouse instead of silently finding zero bridge_* tables.
    bridges = [t for t in all_tables if t.startswith("bridge_")]
    dupe_scan_tables = bridges + [t for t in CROSSWALKS if t in all_tables]

    print(f"validating {len(dupe_scan_tables)} bridge/map crosswalk tables in {args.db}\n")
    for t in dupe_scan_tables:
        check_exact_dupes(con, t, args.fix)
    for t, spec in CROSSWALKS.items():
        if t in all_tables:
            check_crosswalk(con, t, spec)
    # bridge_player_bbr was renamed map_player_bbr in the canonical layer
    # (not a bridge_* table anymore) -- gate on its actual live name.
    if "map_player_bbr" in all_tables:
        check_player_bbr(con)
    if "map_player_source_id" in all_tables and "map_player_bbr" in all_tables:
        check_bbr_reconciliation(con)
    if "src_bridge_player_team_season" in all_tables:
        check_player_team_season(con, args.fix)

    fails = sum(1 for s, *_ in results if s == "FAIL")
    warns = sum(1 for s, *_ in results if s == "WARN")
    print(f"\n{len(results)} checks: {fails} FAIL, {warns} WARN")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
