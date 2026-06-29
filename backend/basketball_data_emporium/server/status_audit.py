"""Audit/DQ status aggregation for `/api/status`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from typing import Any, Literal

import duckdb

AuditDataState = Literal["passed", "failed", "stale", "unverified"]


@dataclass(frozen=True)
class AuditStatusSnapshot:
    """Latest ETL/DQ state exposed through `/api/status`."""

    latest_run_id: str | None
    latest_stage: str | None
    latest_status: str | None
    latest_started_at: datetime | None
    dq_status: str | None
    is_verified: bool
    is_stale: bool
    state: AuditDataState


def _empty_snapshot() -> AuditStatusSnapshot:
    return AuditStatusSnapshot(
        latest_run_id=None,
        latest_stage=None,
        latest_status=None,
        latest_started_at=None,
        dq_status=None,
        is_verified=False,
        is_stale=True,
        state="unverified",
    )


def _table_exists(conn: duckdb.DuckDBPyConnection, schema: str, table: str) -> bool:
    try:
        row = conn.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = ? AND table_name = ?
            LIMIT 1
            """,
            [schema, table],
        ).fetchone()
    except Exception:  # noqa: BLE001 - status must stay best-effort
        return False
    return row is not None


def _columns(conn: duckdb.DuckDBPyConnection, schema: str, table: str) -> set[str]:
    try:
        rows = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = ? AND table_name = ?
            """,
            [schema, table],
        ).fetchall()
    except Exception:  # noqa: BLE001
        return set()
    return {str(row[0]) for row in rows}


def _first_existing(columns: set[str], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def _scalar(conn: duckdb.DuckDBPyConnection, sql: str) -> Any:
    row = conn.execute(sql).fetchone()
    return row[0] if row else None


def _is_stale(started_at: datetime | None) -> bool:
    if started_at is None:
        return True
    max_age_hours = int(os.environ.get("BASKETBALL_DATA_AUDIT_STALE_HOURS", "72"))
    ts = started_at if started_at.tzinfo else started_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ts > timedelta(hours=max_age_hours)


def read_audit_status(conn: duckdb.DuckDBPyConnection) -> AuditStatusSnapshot:
    """Return the latest audit/DQ state.

    Missing audit tables are reported as ``unverified`` rather than making the
    liveness endpoint fail. A reachable DB with unverified data is a different
    state from an offline API, and the frontend can now render that difference.
    """
    if not _table_exists(conn, "audit", "pipeline_run_log"):
        return _empty_snapshot()

    pipeline_columns = _columns(conn, "audit", "pipeline_run_log")
    started_col = _first_existing(
        pipeline_columns, ("started_at", "run_started_at", "created_at")
    )
    status_col = _first_existing(pipeline_columns, ("status", "run_status"))
    stage_col = _first_existing(pipeline_columns, ("stage_name", "stage"))
    run_id_col = _first_existing(pipeline_columns, ("run_id", "pipeline_run_id", "id"))

    if status_col is None:
        return _empty_snapshot()

    order_expr = f"{started_col} DESC NULLS LAST" if started_col else "1"
    select_parts = [
        f"CAST({run_id_col} AS VARCHAR) AS run_id" if run_id_col else "NULL AS run_id",
        f"CAST({stage_col} AS VARCHAR) AS stage" if stage_col else "NULL AS stage",
        f"CAST({status_col} AS VARCHAR) AS status",
        f"{started_col} AS started_at" if started_col else "NULL AS started_at",
    ]
    latest = conn.execute(
        f"""
        SELECT {", ".join(select_parts)}
        FROM audit.pipeline_run_log
        ORDER BY {order_expr}
        LIMIT 1
        """
    ).fetchone()
    if latest is None:
        return _empty_snapshot()

    latest_run_id, latest_stage, latest_status, latest_started_at = latest
    latest_status_normalized = (
        str(latest_status).lower() if latest_status is not None else None
    )

    dq_status: str | None = None
    if _table_exists(conn, "audit", "dq_results"):
        dq_columns = _columns(conn, "audit", "dq_results")
        dq_status_col = _first_existing(
            dq_columns, ("status", "result_status", "check_status")
        )
        dq_time_col = _first_existing(
            dq_columns, ("checked_at", "created_at", "started_at")
        )
        if dq_status_col:
            dq_order = f"ORDER BY {dq_time_col} DESC NULLS LAST" if dq_time_col else ""
            dq_status = _scalar(
                conn,
                f"SELECT CAST({dq_status_col} AS VARCHAR) FROM audit.dq_results {dq_order} LIMIT 1",
            )
            dq_status = str(dq_status).lower() if dq_status is not None else None
        else:
            count = _scalar(conn, "SELECT count(*) FROM audit.dq_results")
            dq_status = "present" if count and int(count) > 0 else None

    is_stale = _is_stale(
        latest_started_at if isinstance(latest_started_at, datetime) else None
    )
    dq_passed = dq_status in {"passed", "pass", "success", "succeeded", "ok", "present"}
    run_passed = latest_status_normalized in {
        "success",
        "succeeded",
        "passed",
        "pass",
        "ok",
    }
    is_verified = bool(run_passed and dq_passed and not is_stale)
    if is_stale:
        state = "stale"
    elif latest_status_normalized in {"failed", "failure", "error"} or dq_status in {
        "failed",
        "failure",
        "error",
    }:
        state = "failed"
    elif is_verified:
        state = "passed"
    else:
        state = "unverified"

    return AuditStatusSnapshot(
        latest_run_id=str(latest_run_id) if latest_run_id is not None else None,
        latest_stage=str(latest_stage) if latest_stage is not None else None,
        latest_status=latest_status_normalized,
        latest_started_at=latest_started_at
        if isinstance(latest_started_at, datetime)
        else None,
        dq_status=dq_status,
        is_verified=is_verified,
        is_stale=is_stale,
        state=state,
    )
