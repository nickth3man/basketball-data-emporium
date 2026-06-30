"""Synthetic-DuckDB tests for the audit/DQ status aggregator.

These tests build a tiny in-memory ``audit.*`` schema (mirroring the
real one) and drive ``read_audit_status`` through each branch of the
severity + staleness gate. They exist alongside the live-DuckDB tests
in ``test_can_read_pipeline_log.py`` and are independent of the 22 GB
snapshot — every assertion is over a fully synthetic schema.

The stale-DQ guard (issue #7) means a fresh-looking ``dq_results``
table that has not been written to in a long time must NOT count as
"passed" — see ``status_audit.read_audit_status`` for the contract.
The tests below pin every branch of the staleness gate:

(a) fresh dq + unaccepted blocking -> dq failed
(b) fresh dq + all blocking accepted -> passed
(c) stale dq (old checked_at) -> not verified / stale reason
(d) dq with no checked_at column -> falls back to current behaviour
(e) empty / missing dq -> still unknown / missing (unchanged)
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import duckdb
import pytest

from basketball_data_emporium.server.status_audit import (
    AuditStateReason,
    read_audit_status,
)


# ---------------------------------------------------------------------------
# Synthetic schema builder
# ---------------------------------------------------------------------------


def _build_schema() -> tuple[str, str]:
    """Return SQL DDL strings for the minimal audit schema we exercise."""
    pipeline_ddl = """
        CREATE TABLE audit.pipeline_run_log (
            run_id     VARCHAR,
            stage_name VARCHAR,
            status     VARCHAR,
            started_at TIMESTAMP
        )
    """
    dq_ddl = """
        CREATE TABLE audit.dq_results (
            check_name VARCHAR,
            table_name VARCHAR,
            severity   VARCHAR,
            row_count  BIGINT,
            details    VARCHAR,
            checked_at TIMESTAMP
        )
    """
    return pipeline_ddl, dq_ddl


@pytest.fixture()
def conn() -> Iterator[duckdb.DuckDBPyConnection]:
    """Yield a fresh in-memory DuckDB connection with an ``audit`` schema.

    The connection is private to the test — no two tests share state.
    The default ``DQ_STALENESS_HOURS`` (48) is used; tests that need a
    different value set the env var on the ``monkeypatch`` fixture
    *before* calling ``read_audit_status`` (the threshold is read on
    every call, so a per-test override is sufficient).
    """
    con = duckdb.connect(":memory:")
    con.execute("CREATE SCHEMA audit")
    yield con
    con.close()


def _insert_recent_pipeline_pass(con: duckdb.DuckDBPyConnection) -> None:
    """Insert a successful pipeline run that started ``now``."""
    pipeline_ddl, _ = _build_schema()
    con.execute(pipeline_ddl)
    con.execute(
        "INSERT INTO audit.pipeline_run_log VALUES (?, ?, ?, ?)",
        ["r1", "final", "success", datetime.now(timezone.utc)],
    )


def _insert_dq_row(
    con: duckdb.DuckDBPyConnection,
    check_name: str,
    severity: str,
    row_count: int,
    checked_at: datetime,
    *,
    include_checked_at: bool = True,
) -> None:
    """Insert a single dq_results row.

    If ``include_checked_at`` is False, the table is created without a
    ``checked_at`` column so the staleness gate has nothing to bite on.
    """
    if include_checked_at:
        ddl = """
            CREATE TABLE audit.dq_results (
                check_name VARCHAR,
                table_name VARCHAR,
                severity   VARCHAR,
                row_count  BIGINT,
                details    VARCHAR,
                checked_at TIMESTAMP
            )
        """
    else:
        ddl = """
            CREATE TABLE audit.dq_results (
                check_name VARCHAR,
                table_name VARCHAR,
                severity   VARCHAR,
                row_count  BIGINT,
                details    VARCHAR
            )
        """
    con.execute(ddl)
    if include_checked_at:
        con.execute(
            "INSERT INTO audit.dq_results VALUES (?, ?, ?, ?, ?, ?)",
            [check_name, "t", severity, row_count, "detail", checked_at],
        )
    else:
        con.execute(
            "INSERT INTO audit.dq_results VALUES (?, ?, ?, ?, ?)",
            [check_name, "t", severity, row_count, "detail"],
        )


# ---------------------------------------------------------------------------
# (a) fresh dq + unaccepted blocking -> dq failed
# ---------------------------------------------------------------------------


def test_fresh_dq_with_unaccepted_blocking_fails(conn: duckdb.DuckDBPyConnection) -> None:
    """Severity path: one HIGH row, no discrepancy entry, recent checked_at.

    This is the *old* behaviour pinned by a test (the gate-fix landed
    in a prior change). Re-pinned here alongside the staleness tests
    so the severity path cannot silently regress while we add the
    staleness gate.
    """
    _insert_recent_pipeline_pass(conn)
    _insert_dq_row(
        conn,
        "fresh_unaccepted_blocking",
        "HIGH",
        7,
        datetime.now(timezone.utc),
    )

    snap = read_audit_status(conn)
    assert snap.dq_status == "failed"
    assert snap.state == "failed"
    assert snap.reason == "latest_dq_failed"
    assert snap.is_verified is False
    # Pipeline is fresh, dq is fresh — only the dq *content* failed.
    assert snap.is_stale is False


# ---------------------------------------------------------------------------
# (b) fresh dq + all blocking accepted -> passed
# ---------------------------------------------------------------------------


def test_fresh_dq_with_all_blocking_accepted_passes(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Severity path: one HIGH row, but its check_name is in the known-divergence table.

    The discrepancy_known_divergence table is intentionally minimal —
    we just need it to expose a ``check_name`` column containing the
    matching value.
    """
    _insert_recent_pipeline_pass(conn)
    _insert_dq_row(
        conn,
        "accepted_high_row",
        "HIGH",
        3,
        datetime.now(timezone.utc),
    )
    conn.execute(
        """
        CREATE TABLE audit.discrepancy_known_divergence (
            check_name VARCHAR
        )
        """
    )
    # Single known-divergence entry that should accept the blocking row.
    conn.execute(
        "INSERT INTO audit.discrepancy_known_divergence VALUES (?)",
        ["accepted_high_row"],
    )

    snap = read_audit_status(conn)
    assert snap.dq_status == "passed"
    assert snap.state == "passed"
    assert snap.reason == "verified"
    assert snap.is_verified is True
    assert snap.is_stale is False


# ---------------------------------------------------------------------------
# (c) STALE dq (old checked_at) -> not verified / stale reason
# ---------------------------------------------------------------------------


def test_stale_dq_marks_snapshot_stale(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Staleness gate: pipeline fresh + dq fresh-but-old -> dq_stale.

    Pipeline is recent, dq is present with only LOW-severity rows so
    the severity path would otherwise report "passed", BUT the
    ``checked_at`` is older than ``DQ_STALENESS_HOURS`` (default 48h)
    and the snapshot must report ``state="stale"`` /
    ``reason="dq_stale"`` and ``is_verified=False``.
    """
    _insert_recent_pipeline_pass(conn)
    old = datetime.now(timezone.utc) - timedelta(hours=72)
    _insert_dq_row(conn, "low_severity", "LOW", 1, old)

    snap = read_audit_status(conn)
    assert snap.dq_status == "stale", (
        f"expected dq_status='stale' for 72h-old dq, got {snap.dq_status!r}"
    )
    assert snap.state == "stale"
    assert snap.reason == "dq_stale"
    assert snap.is_verified is False
    assert snap.is_stale is True


def test_stale_dq_threshold_is_configurable(
    conn: duckdb.DuckDBPyConnection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 24h-old dq is fresh when DQ_STALENESS_HOURS=48, stale when =12.

    Pins that the threshold is the only thing that changed between
    the two calls — the data, schema, and pipeline are identical.
    """
    _insert_recent_pipeline_pass(conn)
    twenty_four_hours_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    _insert_dq_row(conn, "low_severity", "LOW", 1, twenty_four_hours_ago)

    monkeypatch.setenv("DQ_STALENESS_HOURS", "48")
    snap = read_audit_status(conn)
    assert snap.dq_status == "passed"
    assert snap.state == "passed"
    assert snap.reason == "verified"

    monkeypatch.setenv("DQ_STALENESS_HOURS", "12")
    snap = read_audit_status(conn)
    assert snap.dq_status == "stale"
    assert snap.state == "stale"
    assert snap.reason == "dq_stale"


def test_stale_dq_demotes_failed_dq_status(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """A stale dq with unaccepted blocking rows reports dq_stale, not latest_dq_failed.

    Staleness wins over the per-row severity evaluation: if the table
    is too old to trust, it doesn't matter whether the rows inside say
    "failed" or "passed". The actionable signal is the staleness.
    """
    _insert_recent_pipeline_pass(conn)
    old = datetime.now(timezone.utc) - timedelta(hours=72)
    _insert_dq_row(conn, "blocking_and_stale", "HIGH", 5, old)

    snap = read_audit_status(conn)
    # dq_status is the *stale* demotion, not the underlying severity.
    assert snap.dq_status == "stale"
    # dq_stale wins over latest_dq_failed in the reason ladder.
    assert snap.state == "stale"
    assert snap.reason == "dq_stale"
    assert snap.is_verified is False
    assert snap.is_stale is True


def test_dq_stale_does_not_override_pipeline_failure(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """A failed pipeline beats dq_stale in the reason ladder.

    If the pipeline itself failed, that is the most actionable signal
    for an operator; the dq table being stale on top of that is
    secondary. ``state`` and ``reason`` reflect the pipeline failure;
    ``is_stale`` is True (because dq *is* stale) and ``dq_status`` is
    "stale" — both pieces of information are preserved.
    """
    pipeline_ddl, _ = _build_schema()
    conn.execute(pipeline_ddl)
    conn.execute(
        "INSERT INTO audit.pipeline_run_log VALUES (?, ?, ?, ?)",
        ["r1", "final", "failed", datetime.now(timezone.utc)],
    )
    old = datetime.now(timezone.utc) - timedelta(hours=72)
    _insert_dq_row(conn, "low_severity", "LOW", 1, old)

    snap = read_audit_status(conn)
    assert snap.dq_status == "stale"
    assert snap.state == "failed"
    assert snap.reason == "latest_pipeline_failed"
    assert snap.is_verified is False
    assert snap.is_stale is True


# ---------------------------------------------------------------------------
# (d) dq with no checked_at column -> falls back to current behaviour
# ---------------------------------------------------------------------------


def test_dq_without_checked_at_column_is_not_marked_stale(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Defensive fallback: a dq_results table that has no checked_at column
    is *not* marked stale — we have no signal to evaluate against, so the
    severity / status-column path runs as if the staleness gate did not
    exist.
    """
    _insert_recent_pipeline_pass(conn)
    # Build the dq_results table WITHOUT a checked_at column.
    _insert_dq_row(
        conn,
        "no_timestamp_col",
        "HIGH",
        1,
        checked_at=datetime.now(timezone.utc),  # ignored — no column
        include_checked_at=False,
    )

    snap = read_audit_status(conn)
    # dq_status reflects the severity path (HIGH + row_count>0 + no
    # divergence entry) — i.e. dq_status='failed', not 'stale'.
    assert snap.dq_status == "failed"
    assert snap.state == "failed"
    assert snap.reason == "latest_dq_failed"
    assert snap.is_stale is False


def test_dq_with_null_checked_at_is_not_marked_stale(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Defensive fallback: dq_results present, checked_at present, but every
    row has NULL checked_at. ``SELECT MAX(checked_at)`` is NULL, the
    helper returns (False, None), and the staleness gate is skipped.
    """
    _insert_recent_pipeline_pass(conn)
    # Build the dq_results table with a checked_at column but insert
    # NULL — the only way to exercise the NULL max branch from a
    # CREATE TABLE statement is a literal NULL insert.
    dq_ddl = """
        CREATE TABLE audit.dq_results (
            check_name VARCHAR,
            table_name VARCHAR,
            severity   VARCHAR,
            row_count  BIGINT,
            details    VARCHAR,
            checked_at TIMESTAMP
        )
    """
    conn.execute(dq_ddl)
    conn.execute(
        "INSERT INTO audit.dq_results VALUES (?, ?, ?, ?, ?, ?)",
        ["null_ts", "t", "LOW", 1, "detail", None],
    )

    snap = read_audit_status(conn)
    # The severity path sees no HIGH/CRITICAL rows -> dq_status='passed'.
    # Staleness is *not* applied because MAX(checked_at) was NULL.
    assert snap.dq_status == "passed"
    assert snap.state == "passed"
    assert snap.reason == "verified"
    assert snap.is_stale is False


# ---------------------------------------------------------------------------
# (e) empty / missing dq -> still unknown / missing (unchanged)
# ---------------------------------------------------------------------------


def test_no_dq_results_table_still_unverified(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Pins the stub/empty-dq path: no audit.dq_results at all.

    This is the path exercised by the test-suite stub pool — the
    frontend / tests must continue to see ``dq_missing`` /
    ``unverified`` (NOT ``failed`` or ``stale``) when the table is
    absent. The staleness gate must not crash or misfire.
    """
    _insert_recent_pipeline_pass(conn)
    # No audit.dq_results table created.
    snap = read_audit_status(conn)
    assert snap.dq_status is None
    assert snap.state == "unverified"
    assert snap.reason == "dq_missing"
    assert snap.is_verified is False
    # Pipeline is fresh, so is_stale reflects the pipeline-run-log
    # staleness (False) — not the dq_results staleness (n/a).
    assert snap.is_stale is False


def test_empty_dq_results_table_does_not_trigger_staleness(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Empty dq_results (table exists, zero rows) -> severity path returns passed.

    The DQ framework has not yet written any rows, so the severity
    evaluation finds no blocking rows and ``dq_status`` resolves to
    ``"passed"`` (existing behaviour — preserved by this change).
    The staleness gate must NOT fire on an empty table: ``MAX(checked_at)``
    is NULL, ``_dq_is_stale`` returns ``(False, None)``, and the gate
    is skipped. ``is_stale`` therefore reflects only the pipeline
    staleness, which is fresh in this fixture.

    This pins the "empty table" defensive branch of the staleness
    gate alongside the missing-table branch in
    ``test_no_dq_results_table_still_unverified``.
    """
    _insert_recent_pipeline_pass(conn)
    dq_ddl = """
        CREATE TABLE audit.dq_results (
            check_name VARCHAR,
            table_name VARCHAR,
            severity   VARCHAR,
            row_count  BIGINT,
            details    VARCHAR,
            checked_at TIMESTAMP
        )
    """
    conn.execute(dq_ddl)
    # No rows inserted.

    snap = read_audit_status(conn)
    # Severity path with 0 rows -> dq_status="passed" (preserved).
    assert snap.dq_status == "passed"
    assert snap.state == "passed"
    assert snap.reason == "verified"
    # Staleness gate does NOT fire: empty table -> MAX(checked_at) is NULL.
    assert snap.is_stale is False
    assert snap.is_verified is True


# ---------------------------------------------------------------------------
# Reason vocabulary — the dq_stale enum value is exposed in the API schema
# ---------------------------------------------------------------------------


def test_dq_stale_is_in_reason_vocabulary() -> None:
    """The ``dq_stale`` reason is part of the declared ``AuditStateReason`` Literal.

    This is a structural assertion — it does not exercise the DB, but
    it pins the contract that ``dq_stale`` is an exposed reason value
    so the OpenAPI / Pydantic schema (which mirrors this Literal)
    cannot silently drop it.
    """
    # The Literal is the canonical enum; if dq_stale is ever removed
    # from the source, this assertion fires before the OpenAPI tests
    # notice the drift.
    assert "dq_stale" in AuditStateReason.__args__  # type: ignore[attr-defined]
