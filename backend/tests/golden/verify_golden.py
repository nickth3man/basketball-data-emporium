"""Verify each row in golden.csv against the live DuckDB.

Run from the repo root:
    python backend/tests/golden/verify_golden.py
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[3]
CSV_PATH = Path(__file__).resolve().parent / "golden.csv"
DB_PATH = ROOT / "data" / "nba.duckdb"


def parse_expected(stat_key: str, raw: str):
    if raw is None or raw.strip() == "" or raw.strip().upper() == "NULL":
        return None
    if stat_key in {"PER", "BPM"}:
        return float(raw)
    return int(raw)


def main() -> int:
    if not DB_PATH.exists():
        print(f"error: {DB_PATH} missing", file=sys.stderr)
        return 2
    con = duckdb.connect(str(DB_PATH), read_only=True)
    failures = []
    with CSV_PATH.open(encoding="utf-8") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):
            gid = row["golden_id"]
            stat = row["stat_key"]
            expected = parse_expected(stat, row["expected_value"])
            try:
                result = con.execute(row["sql_query"]).fetchone()
            except Exception as e:  # pragma: no cover - diagnostic
                failures.append((gid, f"EXEC ERROR: {e}"))
                print(f"FAIL  {gid:<32s} exec: {e}")
                continue
            actual = result[0] if result is not None else None
            ok = (expected is None and actual is None) or (
                expected is not None and actual == expected
            )
            tag = "ok  " if ok else "FAIL"
            print(
                f"{tag}  {gid:<32s} stat={stat:<5s} expected={expected!r:<8} actual={actual!r}"
            )
            if not ok:
                failures.append((gid, f"expected={expected!r} actual={actual!r}"))
    print()
    if failures:
        print(f"{len(failures)} failure(s):")
        for gid, msg in failures:
            print(f"  {gid}: {msg}")
        return 1
    print("all golden rows reproduce their expected value")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
