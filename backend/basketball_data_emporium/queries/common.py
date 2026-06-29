"""Small helpers shared by player/team query modules."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Iterable

import duckdb

from basketball_data_emporium.db.normalization import season_end_year_sql
from basketball_data_emporium.server.catalog_registry import (
    build_player_hub_catalog,
    build_team_hub_catalog,
)
from basketball_data_emporium.server.errors import (
    BadRequestError,
    InternalError,
    SchemaDriftError,
)
from basketball_data_emporium.server.models.catalog import ColumnMeta
from basketball_data_emporium.server.models.common import EndpointRowsResponse

logger = logging.getLogger(__name__)


def _query_timeout_seconds() -> float | None:
    raw = os.environ.get("BASKETBALL_DATA_QUERY_TIMEOUT_MS", "10000").strip()
    try:
        timeout_ms = int(raw)
    except ValueError:
        logger.warning(
            "BASKETBALL_DATA_QUERY_TIMEOUT_MS=%r is not an int; disabling query timeout.",
            raw,
        )
        return None
    if timeout_ms <= 0:
        return None
    return timeout_ms / 1000


def fetch_dicts(
    conn: duckdb.DuckDBPyConnection,
    sql: str,
    params: Iterable[Any] | None = None,
) -> list[dict[str, Any]]:
    """Execute a parameterized query and return JSON-ready row dicts."""
    started = time.perf_counter()
    timeout = _query_timeout_seconds()
    timed_out = threading.Event()
    timer: threading.Timer | None = None
    if timeout is not None:

        def interrupt_query() -> None:
            timed_out.set()
            conn.interrupt()

        timer = threading.Timer(timeout, interrupt_query)
        timer.daemon = True
        timer.start()
    try:
        cursor = conn.execute(sql, list(params or []))
        columns = [column[0] for column in cursor.description or []]
        rows = [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]
    except Exception as exc:
        if timed_out.is_set():
            raise InternalError(
                "DuckDB query exceeded server timeout",
                detail={"timeout_ms": int((timeout or 0) * 1000)},
            ) from exc
        raise
    finally:
        if timer is not None:
            timer.cancel()
    logger.info(
        "duckdb.query",
        extra={
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "row_count": len(rows),
            "column_count": len(columns),
        },
    )
    return rows


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
            return (
                entry.endpoint_name,
                entry.columns or [],
                entry.default_visible_columns or [],
            )
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
    return season_end_year_sql(column)
