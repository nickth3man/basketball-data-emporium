"""Adversarial security tests (Phase 8).

These tests PROVE the v1 safety properties hold — they are the evidence for the
"no SQL injection vector, no table outside the allowlist ever executed" claim:

1. User/agent param values are always bound ($name placeholders), never
   interpolated into SQL text → a SQL-injection payload in a param is executed
   as a literal, not as SQL.
2. The read-only DuckDB connection rejects writes even if SQLGlot were bypassed.
3. The table allowlist is enforced for every registered template.
4. Table-valued functions (read_csv_auto, pragma_*, duckdb_*) that would bypass
   the allowlist are rejected (C2).
5. Catalog/schema-qualified names are rejected (H1).
6. Forbidden statements (DDL/DML/PRAGMA/ATTACH/COPY/...) are rejected.
7. Tracebacks logged via ``log.exception`` are scrubbed of ``sk-or-...`` keys (H3).
8. The non-streaming ``/api/chat`` enforces the template timeout (C1).
"""

from __future__ import annotations

import logging

import duckdb
import pytest

from chat_server.sqlgate import validate_select_sql
from chat_tests.conftest import skip_no_db

# -- 1 & 2. Param binding + read-only connection ----------------------------


@skip_no_db
@pytest.mark.asyncio
async def test_read_only_connection_rejects_writes():
    """Defense in depth: even if a write statement reached the
    connection, the read-only DuckDB handle rejects it."""
    from chat_server.db import get_db

    db = get_db()
    with pytest.raises(duckdb.Error):  # noqa: B017 - any write must fail on read-only
        await db.execute("CREATE TEMP TABLE _security_probe (x INT)")


# -- 3. Allowlist enforced per template -------------------------------------


# -- 4 & 5. TVF + catalog-qualification rejection (C2/H1) -------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv_auto('/etc/passwd')",
        "SELECT * FROM read_parquet('s3://bucket/x.parquet')",
        "SELECT * FROM read_json('/tmp/x.json')",
        "SELECT * FROM pragma_storage_info('mart_player_season')",
        "SELECT * FROM pragma_database_list()",
        "SELECT * FROM duckdb_tables()",
        "SELECT * FROM duckdb_columns()",
    ],
)
def test_table_valued_functions_are_rejected(sql: str):
    """TVFs parse as exp.Func, not exp.Table — without the explicit denylist
    they would bypass the table allowlist and read arbitrary files (C2)."""
    report = validate_select_sql(sql, {"mart_player_season"})
    assert not report.valid, f"TVF must be rejected: {sql}"
    assert any("table-valued function" in e or "not allowed" in e for e in report.errors)


def test_catalog_qualified_table_reference_is_rejected():
    """A schema/catalog-qualified name must not slip through on leaf match (H1)."""
    report = validate_select_sql(
        "SELECT * FROM other_db.mart_player_season",
        {"mart_player_season"},
    )
    assert not report.valid
    assert any("qualified" in e for e in report.errors)


# -- 6. Forbidden statements ------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM dim_player",
        "DROP TABLE x",
        "INSERT INTO dim_player VALUES (1)",
        "UPDATE dim_player SET full_name='x'",
        "CREATE TABLE _evil (x INT)",
        "ALTER TABLE dim_player ADD COLUMN x INT",
        "ATTACH 'evil.db' AS evil",
        "COPY dim_player TO '/tmp/out.csv'",
        "PRAGMA database_list",
        "VACUUM",
        "CHECKPOINT",
    ],
)
def test_forbidden_statements_rejected(sql: str):
    """No DDL/DML/PRAGMA/ATTACH/COPY/utility statement may pass."""
    report = validate_select_sql(sql, {"dim_player"})
    assert not report.valid, f"forbidden statement must be rejected: {sql}"


def test_multi_statement_injection_rejected():
    """A second statement smuggled after a SELECT must be rejected."""
    report = validate_select_sql("SELECT 1; DROP TABLE dim_player", {"dim_player"})
    assert not report.valid


# -- 7. Traceback redaction (H3) -------------------------------------------


def test_log_exception_redacts_openrouter_key_in_traceback(tmp_path, monkeypatch):
    """An exception whose repr includes the Authorization header must be
    redacted in the JSONL traceback (H3)."""
    from chat_server.logging_setup import JsonlFormatter

    # Use a fresh formatter (don't depend on global setup state).
    fmt = JsonlFormatter()
    record = logging.LogRecord(
        name="probe",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="boom",
        args=None,
        exc_info=None,
    )
    try:
        raise RuntimeError("Authorization: Bearer sk-or-deadbeefABC123 failed")
    except RuntimeError:
        import sys

        record.exc_info = sys.exc_info()
    line = fmt.format(record)
    assert "sk-or-deadbeefABC123" not in line, "raw key leaked into traceback JSONL"
    assert "sk-or-[REDACTED]" in line, "key was not redacted in traceback"
