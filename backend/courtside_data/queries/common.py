"""Small helpers shared by player/team query modules."""

from __future__ import annotations

from typing import Any, Iterable

import duckdb

from courtside_data.server.catalog_registry import (
    build_player_hub_catalog,
    build_team_hub_catalog,
)
from courtside_data.server.errors import BadRequestError, SchemaDriftError
from courtside_data.server.models.catalog import ColumnMeta
from courtside_data.server.models.common import EndpointRowsResponse


def fetch_dicts(
    conn: duckdb.DuckDBPyConnection,
    sql: str,
    params: Iterable[Any] | None = None,
) -> list[dict[str, Any]]:
    """Execute a parameterized query and return JSON-ready row dicts."""
    cursor = conn.execute(sql, list(params or []))
    columns = [column[0] for column in cursor.description or []]
    return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]


def fetch_one(
    conn: duckdb.DuckDBPyConnection,
    sql: str,
    params: Iterable[Any] | None = None,
) -> dict[str, Any] | None:
    rows = fetch_dicts(conn, sql, params)
    return rows[0] if rows else None


def player_dataset_meta(dataset: str) -> tuple[str, list[ColumnMeta], list[str]]:
    catalog = build_player_hub_catalog()
    for entry in catalog.datasets:
        if entry.id == dataset:
            return entry.endpoint_name, entry.columns, entry.default_visible_columns
    raise BadRequestError("Unknown player dataset", detail={"dataset": dataset})


def team_dataset_meta(dataset: str) -> tuple[str, list[ColumnMeta], list[str]]:
    catalog = build_team_hub_catalog()
    for entry in catalog.datasets:
        if entry.id == dataset:
            return entry.endpoint_name, entry.columns or [], entry.default_visible_columns or []
    raise BadRequestError("Unknown team dataset", detail={"dataset": dataset})


def build_rows_response(
    *,
    dataset: str,
    endpoint_name: str,
    params: dict[str, Any],
    columns: list[ColumnMeta],
    default_visible_columns: list[str],
    rows: list[dict[str, Any]],
) -> EndpointRowsResponse:
    visible = set(default_visible_columns)
    for index, row in enumerate(rows):
        if not visible.issubset(row.keys()):
            raise SchemaDriftError(
                "Dataset row does not contain the registered visible columns",
                detail={
                    "dataset": dataset,
                    "row": index,
                    "missing": sorted(visible - set(row.keys())),
                },
            )
    return EndpointRowsResponse(
        dataset=dataset,
        endpoint_name=endpoint_name,
        params=params,
        row_count=len(rows),
        columns=columns,
        default_visible_columns=default_visible_columns,
        rows=rows,
    )


def season_end_expr(column: str = "season_year") -> str:
    """SQL expression converting `YYYY` or `YYYY-YY` labels to ending year."""
    return (
        f"CASE WHEN CAST({column} AS VARCHAR) LIKE '%-%' "
        f"THEN CAST(SUBSTR(CAST({column} AS VARCHAR), 1, 4) AS INTEGER) + 1 "
        f"ELSE CAST({column} AS INTEGER) END"
    )


def csv_escape_value(value: Any) -> Any:
    """Neutralize spreadsheet formulas before CSV serialization."""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value
