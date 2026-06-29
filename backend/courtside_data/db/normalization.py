"""Database normalization scaffold for messy warehouse semantics."""

from __future__ import annotations


def season_end_year_sql(column: str) -> str:
    """Return a SQL expression that normalizes mixed season encodings.

    TODO P1-BE-03: Centralize season validation and encoding.
    Replace route-local CASE expressions with one tested helper for integer
    `YYYY`, string `YYYY`, and string `YYYY-YY` values. Pair this with a Python
    validator that rejects seasons outside the supported DB range before SQL is
    executed.
    """
    return (
        f"CASE WHEN CAST({column} AS VARCHAR) LIKE '%-%' "
        f"THEN CAST(SUBSTR(CAST({column} AS VARCHAR), 1, 4) AS INTEGER) + 1 "
        f"ELSE CAST({column} AS INTEGER) END"
    )


# TODO P1-BE-04: Normalize team identity with season-active windows.
# Add a shared SQL join helper for `dim_team` that joins by `team_id` and
# constrains `season_end_year BETWEEN season_founded AND season_active_till`.
# This prevents duplicate rows for historical team records.

# TODO P1-DB-01: Resolve schema naming divergence with compatibility views.
# Prefer API-facing DuckDB views with stable names (`display_name`,
# `team_full_name`) over repeated Python-side aliases.

# TODO P1-DB-02: Fix or explicitly model pre-1973 null semantics.
# Add availability metadata and/or corrected ETL views so missing historical
# counters are not aggregated as real zeros.

# TODO P2-DB-02: Add derived-field lineage.
# Fields such as `points_per_game`, `total_rebounds_per_game`, `assists_per_game`,
# and `win_pct` need declared formulas so they are testable like physical DB
# columns.

# TODO P2-DB-03: Decide when to wire `xref.*` identity resolution.
# Keep BBR slugs as the public v1 IDs, but add an identity service before any
# feature blends NBA.com, BBR, and legacy source identifiers.

