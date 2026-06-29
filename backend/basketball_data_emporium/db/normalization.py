"""Shared SQL normalization helpers for warehouse edge cases."""

from __future__ import annotations

from dataclasses import dataclass


def season_end_year_sql(column: str) -> str:
    """Return SQL that normalizes `YYYY` and `YYYY-YY` season encodings."""
    return (
        f"CASE WHEN CAST({column} AS VARCHAR) LIKE '%-%' "
        f"THEN CAST(SUBSTR(CAST({column} AS VARCHAR), 1, 4) AS INTEGER) + 1 "
        f"ELSE CAST({column} AS INTEGER) END"
    )


def team_active_window_sql(
    season_end_expr: str,
    *,
    alias: str = "t",
) -> str:
    """Return a season-active predicate for `unified_star.dim_team` joins."""
    return (
        f"COALESCE({alias}.season_founded, 0) <= {season_end_expr} "
        f"AND COALESCE({alias}.season_active_till, 9999) >= {season_end_expr}"
    )


@dataclass(frozen=True)
class AvailabilityRule:
    key: str
    available_since_season: int
    missing_before_means_unknown: bool


HISTORICAL_AVAILABILITY: tuple[AvailabilityRule, ...] = (
    AvailabilityRule("oreb", 1974, True),
    AvailabilityRule("dreb", 1974, True),
    AvailabilityRule("stl", 1974, True),
    AvailabilityRule("blk", 1974, True),
    AvailabilityRule("tov", 1978, True),
    AvailabilityRule("fg3", 1980, True),
)


DERIVED_FIELD_LINEAGE: dict[str, str] = {
    "points_per_game": "PTS / G when G > 0",
    "total_rebounds_per_game": "TRB / G when G > 0",
    "assists_per_game": "AST / G when G > 0",
    "win_pct": "wins / (wins + losses) when games > 0",
    "season_end_year": "normalized ending year from YYYY or YYYY-YY season labels",
}


PUBLIC_IDENTITY_SOURCE = "basketball-reference"
