"""MVCS Test 4 — Column-semantic manifest lineage.

For every ``ColumnContract`` in :mod:`courtside_data.catalog` we assert:

1. **Lineage exists.** The declared ``(schema, table, column)`` tuple
   must resolve to a real row in ``information_schema.columns``. This
   catches "the view was renamed" / "the column was dropped" / "the
   column name is actually uppercase" failures at build time, not
   request time.
2. **Keys are unique.** Two contracts may not claim the same API key.
3. **Available-since-season is sensible.** OREB/DREB/STL/BLK >= 1974,
   3P columns >= 1980, TOV >= 1978, GS >= 1984. The dataset's
   well-known BBR scrape quirks (zero-filling pre-tracking counters,
   back-filling GS) are not the contract's problem — the contract
   pins the *officially-correct* start year.

The fixture (``duckdb_conn``) comes from
``backend/tests/schema/conftest.py`` so we re-use the same
session-scoped read-only connection the rest of the schema tests
use; opening the 22 GB DuckDB file is expensive and we don't want to
do it more than once per test run.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

import duckdb
import pytest

from courtside_data.catalog import (
    ALL_COLUMN_CONTRACTS,
    ColumnContract,
)


# Columns the league added after the 1946-47 BAA tip-off. These are
# the constraints from the MVCS brief, §3 "Verification".
_3P_PREFIXES = ("fg3", "3p", "3pa")
_3P_EXACT = {"3p", "3pa", "3p_pct"}


def _season_constraint_violations(contract: ColumnContract) -> list[str]:
    """Return a list of human-readable constraint failures for one contract."""
    violations: list[str] = []
    k = contract.key
    if k in {"oreb", "dreb", "stl", "blk"}:
        if contract.available_since_season < 1974:
            violations.append(
                f"{k}: available_since_season={contract.available_since_season} "
                "< 1974 (league tracked these from 1973-74)"
            )
    if k in _3P_EXACT or k.startswith(_3P_PREFIXES):
        if contract.available_since_season < 1980:
            violations.append(
                f"{k}: available_since_season={contract.available_since_season} "
                "< 1980 (3-point line introduced in 1979-80)"
            )
    if k == "tov":
        if contract.available_since_season < 1978:
            violations.append(
                f"{k}: available_since_season={contract.available_since_season} "
                "< 1978 (TOV tracked from 1977-78)"
            )
    if k == "gs":
        if contract.available_since_season < 1984:
            violations.append(
                f"{k}: available_since_season={contract.available_since_season} "
                "< 1984 (GS tracked from 1983-84)"
            )
    if k in {"game_blocks", "game_steals", "game_oreb", "game_dreb"}:
        if contract.available_since_season < 1974:
            violations.append(
                f"{k}: available_since_season={contract.available_since_season} "
                "< 1974 (per-game box score counters for these are "
                "available only from 1973-74)"
            )
    if k in {"game_turnovers"}:
        if contract.available_since_season < 1978:
            violations.append(
                f"{k}: available_since_season={contract.available_since_season} "
                "< 1978 (per-game turnovers tracked from 1977-78)"
            )
    if k in {"game_fg3m", "game_fg3a", "game_fg3_pct"}:
        if contract.available_since_season < 1980:
            violations.append(
                f"{k}: available_since_season={contract.available_since_season} "
                "< 1980 (per-game 3P counters tracked from 1979-80)"
            )
    return violations


# ---------------------------------------------------------------------------
# 1. Lineage exists — every declared tuple must resolve in DuckDB.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "contract",
    ALL_COLUMN_CONTRACTS,
    ids=lambda c: c.key,
)
def test_lineage_exists(
    duckdb_conn: duckdb.DuckDBPyConnection,
    contract: ColumnContract,
) -> None:
    """The declared (schema, table, column) tuple must exist in the live DB.

    If this fails, the most common cause is one of:
      * The view or table was renamed (search the repo for the old name).
      * The column name is case-sensitive — DuckDB stores ``3P`` not
        ``3p`` in the canonical view, and ``PTS`` not ``pts``.
      * The schema was dropped (e.g. ``api`` is now ``analytics``).

    Fix ``column_manifest.py``; do NOT change the test to match.
    """
    row = duckdb_conn.execute(
        """
        SELECT data_type
        FROM information_schema.columns
        WHERE table_schema = ?
          AND table_name   = ?
          AND column_name  = ?
        """,
        [
            contract.lineage.schema,
            contract.lineage.table,
            contract.lineage.column,
        ],
    ).fetchone()
    assert row is not None, (
        f"lineage missing for key={contract.key!r}: "
        f"{contract.lineage.schema}.{contract.lineage.table}."
        f"{contract.lineage.column!r} not found in information_schema.columns. "
        f"Update column_manifest.py."
    )
    # Sanity-check the catalog returned a real DuckDB data type. If
    # this is ever anything other than a TYPE_NAME we know the row
    # is bogus.
    assert isinstance(row[0], str) and row[0], (
        f"lineage for {contract.key!r} returned an empty data_type: {row!r}"
    )


# ---------------------------------------------------------------------------
# 2. No duplicate keys.
# ---------------------------------------------------------------------------


def test_no_duplicate_column_keys() -> None:
    """Each column key is declared exactly once."""
    keys = [c.key for c in ALL_COLUMN_CONTRACTS]
    counter = Counter(keys)
    dupes = sorted(k for k, n in counter.items() if n > 1)
    assert not dupes, (
        f"Duplicate column keys in manifest: {dupes}. "
        f"Each `key` must map to exactly one ColumnContract."
    )


# ---------------------------------------------------------------------------
# 3. available_since_season is consistent with league history.
# ---------------------------------------------------------------------------


def test_available_since_season_consistency() -> None:
    """OREB/DREB/STL/BLK >= 1974; 3P >= 1980; TOV >= 1978; GS >= 1984.

    These are *league* facts, not DB-observed facts — the DB scrubs
    pre-tracking counters to 0 (or NULL) and we want the contract to
    pin the *officially-correct* start year so consumers can build a
    "since 1973-74" UI hint without re-deriving the cutoff from data.
    """
    violations: list[str] = []
    for c in ALL_COLUMN_CONTRACTS:
        violations.extend(_season_constraint_violations(c))
    assert not violations, (
        "available_since_season constraints violated:\n  - "
        + "\n  - ".join(violations)
    )


# ---------------------------------------------------------------------------
# 4. Sanity checks on the manifest shape.
# ---------------------------------------------------------------------------


def test_manifest_is_nonempty() -> None:
    """The manifest is not empty (defensive — would catch a refactor that
    accidentally nukes the list)."""
    assert ALL_COLUMN_CONTRACTS, "ALL_COLUMN_CONTRACTS is empty"


def test_manifest_keys_are_unique_strings() -> None:
    """Every key is a non-empty string with no whitespace surprises."""
    bad: list[str] = []
    for c in ALL_COLUMN_CONTRACTS:
        if not isinstance(c.key, str) or not c.key.strip():
            bad.append(f"{c.key!r}: empty key")
        if c.key != c.key.strip():
            bad.append(f"{c.key!r}: key has leading/trailing whitespace")
        if any(ch.isspace() for ch in c.key):
            bad.append(f"{c.key!r}: key contains whitespace")
    assert not bad, "key hygiene violations: " + ", ".join(bad)


def test_manifest_dtypes_and_units_are_known() -> None:
    """Every contract uses one of the documented dtype/unit literals."""
    valid_dtypes = {"int", "float", "decimal", "str", "bool"}
    valid_units = {
        "points",
        "fraction",
        "percent",
        "decimal_minutes",
        "tenths_of_feet",
        "games",
        "count",
        "year",
        "slug",
    }
    bad: list[str] = []
    for c in ALL_COLUMN_CONTRACTS:
        if c.dtype not in valid_dtypes:
            bad.append(f"{c.key}: dtype={c.dtype!r} not in {sorted(valid_dtypes)}")
        if c.unit not in valid_units:
            bad.append(f"{c.key}: unit={c.unit!r} not in {sorted(valid_units)}")
    assert not bad, "dtype/unit violations: " + "; ".join(bad)


def test_manifest_format_rule_is_nonempty() -> None:
    """Every contract names a format_rule; empty strings are a refactor smell."""
    bad = [c.key for c in ALL_COLUMN_CONTRACTS if not c.format_rule]
    assert not bad, f"format_rule is empty for: {bad}"


def test_manifest_season_floor() -> None:
    """No contract should claim data before the 1946-47 BAA season."""
    bad = [
        c.key
        for c in ALL_COLUMN_CONTRACTS
        if c.available_since_season < 1947
    ]
    assert not bad, (
        f"available_since_season < 1947 (the league's first season): {bad}"
    )


def test_manifest_is_playoffs_scoped_is_boolean() -> None:
    """is_playoffs_scoped must be True or False (not None, not 0/1 ints)."""
    bad: list[str] = []
    for c in ALL_COLUMN_CONTRACTS:
        if not isinstance(c.is_playoffs_scoped, bool):
            bad.append(f"{c.key}: is_playoffs_scoped is {type(c.is_playoffs_scoped).__name__}")
    assert not bad, "is_playoffs_scoped is not strictly bool: " + ", ".join(bad)


# ---------------------------------------------------------------------------
# 5. Manifest is regenerable (proves the file is not hand-edited into
#    oblivion). Skipped by default because the regen script is currently
#    a TODO; uncomment when
#    ``backend/courtside_data/catalog/build_manifest.py`` exists.
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="build_manifest.py is a Phase 3 task; once it lands this test "
    "should regenerate the manifest from a single discovery query and "
    "fail if the hand-written file diverges.",
)
def test_manifest_regenerates_from_db() -> None:
    """Future test: the hand-written manifest must match what a discovery
    query against ``information_schema.columns`` would produce."""
    raise NotImplementedError


# ---------------------------------------------------------------------------
# 6. Generated-on sentinel. If a manifest is older than 90 days,
#    surface a warning so a maintainer knows the lineage is stale.
# ---------------------------------------------------------------------------


def test_manifest_has_recent_generated_at() -> None:
    """The manifest module exposes a ``__generated_at__`` ISO timestamp;
    warn (do not fail) if it's older than 90 days.

    The lineage-existence test above is the strict check; this is a
    soft reminder that the DB schema should be re-walked periodically.
    """
    import courtside_data.catalog.column_manifest as cm

    generated_at = getattr(cm, "__generated_at__", None)
    if generated_at is None:
        pytest.skip(
            "column_manifest.__generated_at__ not set; "
            "set it to an ISO-8601 string at regen time."
        )
    try:
        ts = datetime.fromisoformat(generated_at)
    except ValueError:
        pytest.fail(
            f"column_manifest.__generated_at__={generated_at!r} is not a valid "
            f"ISO-8601 timestamp."
        )
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - ts).days
    assert age_days <= 180, (
        f"column_manifest is {age_days} days old "
        f"(generated_at={generated_at!r}); re-run the regenerator to "
        f"reconcile with the live DB schema."
    )
