"""Parametrized regression tests for the golden dataset.

Each row in ``backend/tests/golden/golden.csv`` pins a single scalar value
that the DuckDB store must reproduce via the embedded ``sql_query``. The
test loads the CSV, opens a read-only DuckDB connection, and asserts each
``expected_value`` matches the scalar returned by its query.

Type parsing:
  - ``expected_value`` is a string in the CSV.
  - Rows with a value of "NULL" (or empty) are treated as a null assertion
    and the query must return ``None``.
  - ``stat_key in {"PER", "BPM"}`` → float comparison.
  - All other stat keys → int comparison.

The DB path resolves relative to this file: ``../../../../data/nba.duckdb``.
Override via the ``BASKETBALL_DATA_DB_PATH`` env var for CI or local development.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import duckdb
import pytest

HERE = Path(__file__).resolve().parent
GOLDEN_CSV = HERE / "golden.csv"

# backend/tests/golden/test_golden_rows.py  →  data/nba.duckdb
#   here  → tests/golden  → tests  → backend  → repo_root  → data
DEFAULT_DB_PATH = HERE / ".." / ".." / ".." / "data" / "nba.duckdb"

FLOAT_STAT_KEYS = {"PER", "BPM"}


def _load_golden() -> List[Dict[str, str]]:
    with GOLDEN_CSV.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="session")
def db() -> duckdb.DuckDBPyConnection:
    db_path = os.environ.get("BASKETBALL_DATA_DB_PATH", str(DEFAULT_DB_PATH.resolve()))
    conn = duckdb.connect(db_path, read_only=True)
    try:
        yield conn
    finally:
        conn.close()


def _parse_expected(stat_key: str, raw: str) -> Optional[Any]:
    """Convert the CSV string into the type the test should compare against."""
    if raw is None:
        return None
    s = raw.strip()
    if s == "" or s.upper() == "NULL":
        return None
    if stat_key in FLOAT_STAT_KEYS:
        return float(s)
    return int(s)


@pytest.mark.parametrize(
    "row",
    _load_golden(),
    ids=lambda r: r["golden_id"],
)
def test_golden_row(db: duckdb.DuckDBPyConnection, row: Dict[str, str]) -> None:
    """Run the row's sql_query and assert the scalar matches expected_value."""
    expected = _parse_expected(row["stat_key"], row["expected_value"])
    result = db.execute(row["sql_query"]).fetchone()
    assert result is not None, (
        f"{row['golden_id']}: query returned no rows\n  SQL: {row['sql_query']}"
    )
    actual = result[0]
    assert actual == expected, (
        f"{row['golden_id']}: expected {expected!r} ({type(expected).__name__}), "
        f"got {actual!r} ({type(actual).__name__})\n"
        f"  SQL: {row['sql_query']}"
    )


# ---------------------------------------------------------------------------
# Cross-row invariant: a single query across both Harden 2022 stints must
# equal the sum of the two team-specific facts. This guards the BRK + PHI =
# 1432 invariant end-to-end.
# ---------------------------------------------------------------------------


def test_harden_2022_combined_invariant(db: duckdb.DuckDBPyConnection) -> None:
    rows = {r["golden_id"]: r for r in _load_golden()}
    brk = db.execute(rows["harden_2022_brk_pts"]["sql_query"]).fetchone()[0]
    phi = db.execute(rows["harden_2022_phi_pts"]["sql_query"]).fetchone()[0]
    combined = db.execute(rows["harden_2022_combined_check"]["sql_query"]).fetchone()[0]
    assert brk + phi == combined == 1432, (
        f"Harden 2022 split invariant broken: BRK={brk} + PHI={phi} "
        f"!= combined={combined} (expected 1432)"
    )


# ---------------------------------------------------------------------------
# Cross-row invariant: v_franchise_leaders and our fact-based equivalent must
# agree on the leader identity (their *totals* differ because the view
# includes playoffs totals). This guards the schema note in the franchise
# leader facts.
# ---------------------------------------------------------------------------


def test_franchise_leaders_view_matches_team(
    db: duckdb.DuckDBPyConnection,
) -> None:
    rows = {r["golden_id"]: r for r in _load_golden()}
    # v_franchise_leaders returns team-level points INCLUDING playoffs.
    lal_view = db.execute(
        "SELECT pts, pts_person_id FROM api.v_franchise_leaders "
        "WHERE team = 'LAL'"
    ).fetchone()
    bos_view = db.execute(
        "SELECT pts, pts_person_id FROM api.v_franchise_leaders "
        "WHERE team = 'BOS'"
    ).fetchone()
    assert lal_view is not None and bos_view is not None, (
        "v_franchise_leaders must return one row per team"
    )
    assert lal_view[1] == 977, (
        f"LAL leader in v_franchise_leaders should be Kobe Bryant (person_id=977), "
        f"got person_id={lal_view[1]}"
    )
    assert bos_view[1] == 76970, (
        f"BOS leader in v_franchise_leaders should be John Havlicek "
        f"(person_id=76970), got person_id={bos_view[1]}"
    )
    # The view's totals are higher than the regular-season-only anchors
    # because the view includes playoffs.
    assert lal_view[0] > int(
        _parse_expected("PTS", rows["lal_franchise_pts_leader"]["expected_value"])
    ), "v_franchise_leaders LAL total should exceed regular-season anchor"
    assert bos_view[0] > int(
        _parse_expected("PTS", rows["bos_franchise_pts_leader"]["expected_value"])
    ), "v_franchise_leaders BOS total should exceed regular-season anchor"
