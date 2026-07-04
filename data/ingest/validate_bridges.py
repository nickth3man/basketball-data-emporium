#!/usr/bin/env python
"""Invariant checks for every bridge_* table in data/nba.duckdb.

Run from the repo root (read-only unless --fix):

    python data/ingest/validate_bridges.py
    python data/ingest/validate_bridges.py --fix   # dedupe exact-duplicate rows

Checks:
  1. no duplicate logical rows in any bridge_* table (logical = all columns
     except surrogate keys *_sk and bookkeeping created_at/last_verified_at)
  2. generic crosswalks (bridge_{player,team,game}_source_id): one row per
     (source_system, source id)
  3. resolved crosswalk rows point at ids that exist in their dim
     (dim_all_players / dim_team+dim_team_history+dim_defunct_team / dim_game)
  4. resolved crosswalk rows carry a match_method; unresolved rows don't
     carry a warehouse id
  5. bridge_player_bbr: one BBR id per warehouse player; fan-in of two
     warehouse ids to one BBR id is reported as WARN (known duplicate
     identities: Vaught, O'Bannon, Werdann, Rambis)
  6. bridge_player_team_season: unique (player_id, team_id, season_year,
     jersey_number) -- mid-season number changes are legitimately two rows;
     --fix collapses position fan-out (same key with G/F/C/NULL variants)

--fix rewrites tables that fail check 1 in place (DELETE + re-INSERT keeps
constraints), keeping one row per logical group (lowest surrogate key).
Everything else is report-only: resolution problems should be fixed in the
source manifest or matching logic, not by hand-editing bridge rows.

Exit code 0 = all PASS (warnings allowed), 1 = at least one FAIL.
"""

import argparse
import os
import sys

import duckdb

DB_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "nba.duckdb")
)
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


CROSSWALKS = {
    "bridge_player_source_id": {
        "wh_id": "person_id",
        "src_id": "source_player_id",
        "dim": "(SELECT DISTINCT person_id AS wid FROM dim_all_players)",
    },
    "bridge_team_source_id": {
        "wh_id": "team_id",
        "src_id": "source_team_id",
        "dim": """(SELECT team_id AS wid FROM dim_team
                   UNION SELECT team_id FROM dim_team_history
                   UNION SELECT team_id FROM dim_defunct_team)""",
    },
    "bridge_game_source_id": {
        "wh_id": "game_id",
        "src_id": "source_game_id",
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
        WHERE (is_unresolved AND {wh} IS NOT NULL)
           OR (NOT is_unresolved AND ({wh} IS NULL OR match_method IS NULL))
        """).fetchone()[0]
    record("FAIL" if bad_flags else "PASS", "flags_consistent", table,
           f"{bad_flags:,} rows with contradictory resolved/unresolved state" if bad_flags else "")

    _, logical = logical_columns(con, table)
    if "unresolved_reason" in logical:
        missing_reason = con.execute(f"""
            SELECT count(*) FROM "{table}"
            WHERE is_unresolved AND unresolved_reason IS NULL""").fetchone()[0]
        record("FAIL" if missing_reason else "PASS", "unresolved_reason_populated", table,
               f"{missing_reason:,} unresolved rows with no unresolved_reason" if missing_reason else "")

    info = con.execute(f"""
        SELECT source_system,
               count(*) FILTER (WHERE NOT is_unresolved) || '/' || count(*)
        FROM "{table}" GROUP BY 1 ORDER BY 1""").fetchall()
    record("INFO", "coverage", table, "; ".join(f"{s}: {c}" for s, c in info))


def check_player_bbr(con):
    t = "bridge_player_bbr"
    fan_out = con.execute(f"""
        SELECT count(*) FROM (SELECT nba_player_id FROM {t}
        GROUP BY 1 HAVING count(*) > 1)""").fetchone()[0]
    record("FAIL" if fan_out else "PASS", "one_bbr_id_per_player", t,
           f"{fan_out} warehouse players with multiple BBR ids" if fan_out else "")
    fan_in = con.execute(f"""
        SELECT count(*) FROM (SELECT bbr_player_id FROM {t}
        GROUP BY 1 HAVING count(*) > 1)""").fetchone()[0]
    if fan_in:
        record("WARN", "one_player_per_bbr_id", t,
               f"{fan_in} BBR ids shared by multiple warehouse ids (known dup identities)")
    else:
        record("PASS", "one_player_per_bbr_id", t)


def check_bbr_reconciliation(con):
    """bridge_player_source_id's basketball_reference/json_slug rows should
    never be unresolved/ambiguous when bridge_player_bbr already has a
    preferred match for that bbr id -- catches the case where ingest.py was
    re-run (e.g. a fresh kaggle_nba resolve) without also re-running
    --reconcile-bbr afterward."""
    t = "bridge_player_source_id"
    stale = con.execute(f"""
        SELECT count(*) FROM {t} b
        WHERE b.source_system IN ('basketball_reference', 'json_slug')
          AND (b.is_unresolved OR b.is_ambiguous)
          AND EXISTS (
              SELECT 1 FROM (
                  SELECT bbr_player_id,
                         row_number() OVER (
                           PARTITION BY bbr_player_id
                           ORDER BY coalesce(g.gp, 0) DESC, nba_player_id
                         ) AS preferred_rank
                  FROM bridge_player_bbr p
                  LEFT JOIN (
                      SELECT player_id, count(*) AS gp
                      FROM fact_player_game_boxscore GROUP BY 1
                  ) g ON g.player_id = p.nba_player_id
              ) r
              WHERE r.bbr_player_id = b.source_player_id AND r.preferred_rank = 1
          )
        """).fetchone()[0]
    record("FAIL" if stale else "PASS", "bbr_reconciliation_complete", t,
           f"{stale:,} rows have a bridge_player_bbr match but are still unresolved/ambiguous; "
           f"re-run: python data/ingest/ingest.py --reconcile-bbr" if stale else "")


def check_player_team_season(con, fix):
    # Key is (player, team, season, jersey): a mid-season number change is two
    # legitimate rows. The corruption seen in practice is position fan-out --
    # the same key repeated with position G, F, C and NULL after a bad join.
    t = "bridge_player_team_season"
    dupes = con.execute(f"""
        SELECT count(*) FROM (SELECT player_id, team_id, season_year, jersey_number
        FROM {t} GROUP BY ALL HAVING count(*) > 1)""").fetchone()[0]
    if dupes == 0:
        record("PASS", "unique_player_team_season", t)
        return
    if not fix:
        record("FAIL", "unique_player_team_season", t,
               f"{dupes:,} duplicated (player, team, season, jersey) keys; rerun with --fix")
        return
    # collapse each key to one row; keep position only when unambiguous
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _pts AS
        SELECT player_id, team_id, season_year, jersey_number,
               CASE WHEN count(DISTINCT position) = 1 THEN min(position) END AS position
        FROM {t} GROUP BY 1, 2, 3, 4""")
    before = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    con.execute(f"DELETE FROM {t}")
    con.execute(f"INSERT INTO {t} SELECT * FROM _pts")
    after = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    record("PASS", "unique_player_team_season", t,
           f"fixed: collapsed {before:,} rows to {after:,} (position kept only when unambiguous)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--fix", action="store_true", help="dedupe exact-duplicate rows")
    args = ap.parse_args()

    con = duckdb.connect(args.db, read_only=not args.fix)
    bridges = [
        r[0]
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name LIKE 'bridge_%' ORDER BY 1"
        ).fetchall()
    ]

    print(f"validating {len(bridges)} bridge tables in {args.db}\n")
    for t in bridges:
        check_exact_dupes(con, t, args.fix)
    for t, spec in CROSSWALKS.items():
        if t in bridges:
            check_crosswalk(con, t, spec)
    if "bridge_player_bbr" in bridges:
        check_player_bbr(con)
    if "bridge_player_source_id" in bridges and "bridge_player_bbr" in bridges:
        check_bbr_reconciliation(con)
    if "bridge_player_team_season" in bridges:
        check_player_team_season(con, args.fix)

    fails = sum(1 for s, *_ in results if s == "FAIL")
    warns = sum(1 for s, *_ in results if s == "WARN")
    print(f"\n{len(results)} checks: {fails} FAIL, {warns} WARN")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
