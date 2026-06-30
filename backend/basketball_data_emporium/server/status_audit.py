"""Audit/DQ status aggregation for `/api/status`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from typing import Any, Literal

import duckdb

AuditDataState = Literal["passed", "failed", "stale", "unverified"]
AuditStateReason = Literal[
    "audit_missing",
    "latest_pipeline_failed",
    "latest_dq_failed",
    "audit_stale",
    "dq_stale",
    "dq_missing",
    "verified",
    "unverified",
]


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
    reason: AuditStateReason


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
        reason="audit_missing",
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


def _load_known_divergences(conn: duckdb.DuckDBPyConnection) -> set[str]:
    """Return the set of `check_name`-like values from `audit.discrepancy_known_divergence`.

    The table is optional and its schema is discovered defensively (we
    look for a `check_name` / `rule_name` / `name` / `check` / `rule`
    column rather than hardcoding one). Returns an empty set if the
    table is missing, has no recognizable name column, or fails to
    query — the audit gate is best-effort and must never crash the
    status endpoint.
    """
    if not _table_exists(conn, "audit", "discrepancy_known_divergence"):
        return set()
    cols = _columns(conn, "audit", "discrepancy_known_divergence")
    name_col = _first_existing(
        cols, ("check_name", "rule_name", "name", "check", "rule")
    )
    if name_col is None:
        return set()
    try:
        rows = conn.execute(
            f"SELECT DISTINCT CAST({name_col} AS VARCHAR) "
            f"FROM audit.discrepancy_known_divergence"
        ).fetchall()
    except Exception:  # noqa: BLE001 - status must stay best-effort
        return set()
    return {str(r[0]) for r in rows if r[0] is not None}


def _is_stale(started_at: datetime | None) -> bool:
    if started_at is None:
        return True
    max_age_hours = int(os.environ.get("BASKETBALL_DATA_AUDIT_STALE_HOURS", "72"))
    ts = started_at if started_at.tzinfo else started_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ts > timedelta(hours=max_age_hours)


def _dq_staleness_threshold_hours() -> int:
    """Return the configured DQ staleness threshold in hours.

    Configurable via the ``DQ_STALENESS_HOURS`` env var; default ``48``.
    Mirrors the pattern of ``BASKETBALL_DATA_AUDIT_STALE_HOURS`` (which
    guards the pipeline-run-log staleness) but applies specifically to
    ``audit.dq_results.checked_at``.
    """
    raw = os.environ.get("DQ_STALENESS_HOURS", "48")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 48


def _dq_is_stale(
    conn: duckdb.DuckDBPyConnection,
) -> tuple[bool, datetime | None]:
    """Check whether ``audit.dq_results.checked_at`` is older than the threshold.

    Returns ``(is_stale, max_checked_at)``. ``is_stale`` is ``True`` only
    when ALL of the following hold:

    * ``audit.dq_results`` exists
    * the table has a ``checked_at`` column
    * ``SELECT MAX(checked_at)`` returns a non-NULL, parseable
      ``datetime`` value
    * that value is older than the configured ``DQ_STALENESS_HOURS``

    Any defensive failure (missing table, missing column, NULL max,
    unparseable value, query error) returns ``(False, None)`` and the
    caller falls back to the current behaviour — staleness is an
    *additional* gate, never a hard requirement.
    """
    if not _table_exists(conn, "audit", "dq_results"):
        return False, None
    cols = _columns(conn, "audit", "dq_results")
    if "checked_at" not in cols:
        return False, None
    try:
        row = conn.execute(
            "SELECT MAX(CAST(checked_at AS TIMESTAMP)) FROM audit.dq_results"
        ).fetchone()
    except Exception:  # noqa: BLE001 - status must stay best-effort
        return False, None
    if not row or row[0] is None:
        return False, None
    max_checked_at = row[0]
    if not isinstance(max_checked_at, datetime):
        return False, None
    ts = (
        max_checked_at
        if max_checked_at.tzinfo
        else max_checked_at.replace(tzinfo=timezone.utc)
    )
    threshold = _dq_staleness_threshold_hours()
    return (
        datetime.now(timezone.utc) - ts > timedelta(hours=threshold),
        max_checked_at,
    )


def read_audit_status(conn: duckdb.DuckDBPyConnection) -> AuditStatusSnapshot:
    """Return the latest audit/DQ state.

    Missing audit tables are reported as ``unverified`` rather than making the
    liveness endpoint fail. A reachable DB with unverified data is a different
    state from an offline API, and the frontend can now render that difference.

    DQ evaluation is schema-aware. When ``audit.dq_results`` has a
    status-like column (``status`` / ``result_status`` / ``check_status``)
    its most recent value drives ``dq_status`` exactly as before. When
    that column is absent, we evaluate ``severity`` defensively: a row
    is **blocking** when ``upper(severity) IN ('CRITICAL','HIGH')`` AND
    ``row_count > 0``; a blocking row is **accepted** when its
    ``check_name`` appears in ``audit.discrepancy_known_divergence``
    (if that table exists and exposes a check-name-like column). Any
    unaccepted blocking row sets ``dq_status = 'failed'``; otherwise
    ``dq_status = 'passed'``. The legacy "present"/None fallback is
    retained for the case where ``dq_results`` exists but lacks the
    ``severity`` / ``row_count`` / ``check_name`` columns needed for
    severity-based evaluation.

    Staleness gate (issue #7): a *fresh* ``audit.dq_results`` is no
    guarantee of correctness if the table itself has not been written
    to in a long time — the rows may describe a snapshot of the data
    from long ago. To prevent the audit gate from silently counting a
    stale DQ table as healthy, an *additional* staleness check is
    applied after the per-row evaluation above:

    * If ``audit.dq_results`` has a ``checked_at`` column, the largest
      ``checked_at`` value is compared against
      ``DQ_STALENESS_HOURS`` (env var, default ``48``).
    * If that max is older than the threshold, ``dq_status`` is
      overridden to ``"stale"`` (so ``dq_passed`` is False and
      ``is_verified`` is False) and the snapshot reports
      ``state="stale"`` with ``reason="dq_stale"``.
    * The check is intentionally defensive: if the table is missing,
      the column is missing, the max is NULL, the value is not a
      ``datetime``, or the query errors, the staleness gate is
      *skipped* and the function falls back to the current
      behaviour. Staleness is an *additional* gate, never a hard
      requirement.
    * ``is_stale`` on the returned snapshot is True if **either** the
      pipeline run log is stale (``audit_stale``) **or** the DQ table
      is stale (``dq_stale``); the ``reason`` field disambiguates.

    External operational items (NOT implemented in this module —
    tracked in issue #7 and related issues #1-#6):

    1. **DQ refresh wiring.** ``audit.dq_results`` is currently
       populated out-of-band. The staleness gate introduced here is a
       safety net: as long as the table is not refreshed, the audit
       gate will start reporting ``dq_stale`` after
       ``DQ_STALENESS_HOURS`` hours. The DQ framework must be wired
       into the ETL so the table is rewritten on every successful
       pipeline run.

    2. **Triage of the 25 currently-unaccepted blocking check_names.**
       As of this change, the live ``audit.dq_results`` contains 25
       distinct ``check_name`` values that are ``CRITICAL`` or
       ``HIGH`` with ``row_count > 0`` and are **not** listed in
       ``audit.discrepancy_known_divergence`` (which currently only
       documents ``pre_1974_orb_untracked`` /
       ``pre_1974_drb_untracked``). Each of those check_names must
       be triaged into either:

       * an entry in ``audit.discrepancy_known_divergence`` with
         owner + rationale (e.g. known upstream source divergence,
         intentionally unbounded for a research use case), or
       * a code/data fix that makes the check pass.

       Until that triage is complete, the audit gate will continue
       to report ``latest_dq_failed`` even when the pipeline itself
       is healthy. This is intentional — silently accepting them
       would defeat the gate.
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
            # No status-like column — evaluate severity defensively. A row
            # is BLOCKING when upper(severity) IN ('CRITICAL','HIGH') AND
            # row_count > 0; a blocking row is ACCEPTED when its
            # check_name appears in audit.discrepancy_known_divergence
            # (if that table exists). Any UNACCEPTED blocking row
            # fails the audit. If we cannot discover severity /
            # row_count / check_name we fall back to the legacy
            # "present"/None behaviour so the API still returns a
            # best-effort answer.
            severity_col = _first_existing(dq_columns, ("severity",))
            row_count_col = _first_existing(
                dq_columns, ("row_count", "violation_count", "count", "rows")
            )
            check_name_col = _first_existing(
                dq_columns, ("check_name", "name", "check", "rule_name", "rule")
            )
            if severity_col and row_count_col and check_name_col:
                accepted = _load_known_divergences(conn)
                try:
                    blocking_rows = conn.execute(
                        f"""
                        SELECT DISTINCT CAST({check_name_col} AS VARCHAR) AS name
                        FROM audit.dq_results
                        WHERE upper(CAST({severity_col} AS VARCHAR))
                              IN ('CRITICAL','HIGH')
                          AND CAST({row_count_col} AS BIGINT) > 0
                        """
                    ).fetchall()
                except Exception:  # noqa: BLE001 - status must stay best-effort
                    blocking_rows = []
                unaccepted = [
                    str(name)
                    for (name,) in blocking_rows
                    if name is not None and str(name) not in accepted
                ]
                dq_status = "failed" if unaccepted else "passed"
            else:
                count = _scalar(conn, "SELECT count(*) FROM audit.dq_results")
                dq_status = "present" if count and int(count) > 0 else None

    # Staleness gate (issue #7): if the dq_results table is present and
    # has a usable checked_at, the newest checked_at must be within
    # DQ_STALENESS_HOURS. A stale table silently counts as "passed"
    # otherwise — explicitly demote it to "stale" so is_verified is
    # False. Defensive: _dq_is_stale returns (False, None) on any
    # missing/unparseable signal and we fall back to current behaviour.
    dq_is_stale, _max_checked_at = _dq_is_stale(conn)
    if dq_is_stale and dq_status is not None:
        dq_status = "stale"

    pipeline_is_stale = _is_stale(
        latest_started_at if isinstance(latest_started_at, datetime) else None
    )
    # The snapshot's is_stale reflects EITHER source of staleness; the
    # reason field below disambiguates which one fired.
    is_stale = pipeline_is_stale or dq_is_stale
    dq_passed = dq_status in {"passed", "pass", "success", "succeeded", "ok", "present"}
    run_passed = latest_status_normalized in {
        "success",
        "succeeded",
        "passed",
        "pass",
        "ok",
    }
    is_verified = bool(run_passed and dq_passed and not is_stale)
    reason: AuditStateReason
    if latest_status_normalized in {"failed", "failure", "error"}:
        state = "failed"
        reason = "latest_pipeline_failed"
    elif dq_status in {"failed", "failure", "error"}:
        state = "failed"
        reason = "latest_dq_failed"
    elif dq_is_stale:
        # dq_stale wins over audit_stale when both apply: the dq
        # staleness is the actionable signal (the pipeline may be
        # running fine but the DQ table isn't being refreshed).
        state = "stale"
        reason = "dq_stale"
    elif is_stale:
        state = "stale"
        reason = "audit_stale"
    elif is_verified:
        state = "passed"
        reason = "verified"
    elif dq_status is None:
        state = "unverified"
        reason = "dq_missing"
    else:
        state = "unverified"
        reason = "unverified"

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
        reason=reason,
    )
