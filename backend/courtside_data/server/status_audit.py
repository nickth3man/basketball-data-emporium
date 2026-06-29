"""Scaffold for audit/DQ status aggregation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import duckdb


@dataclass(frozen=True)
class AuditStatusSnapshot:
    """Latest ETL/DQ state to eventually expose through `/api/status`."""

    latest_run_id: str | None
    latest_stage: str | None
    latest_status: str | None
    latest_started_at: datetime | None
    dq_status: str | None
    is_verified: bool
    is_stale: bool


def read_audit_status(conn: duckdb.DuckDBPyConnection) -> AuditStatusSnapshot:
    """Return the latest audit/DQ state.

    TODO P1-BE-01: Wire `audit.*` into `/api/status`.
    Query `audit.pipeline_run_log` and `audit.dq_results`, decide the
    user-facing state (`passed`, `failed`, `stale`, `unverified`), and extend
    the status response schema. The implementation must handle the current DB
    reality where data exists but the latest audit rows are failed, so the UI
    can distinguish "API online" from "data verified".
    """
    _ = conn
    return AuditStatusSnapshot(
        latest_run_id=None,
        latest_stage=None,
        latest_status=None,
        latest_started_at=None,
        dq_status=None,
        is_verified=False,
        is_stale=True,
    )

