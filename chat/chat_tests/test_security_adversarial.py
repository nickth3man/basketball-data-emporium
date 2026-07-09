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

import asyncio
import logging

import duckdb
import pytest
from pydantic import ValidationError

from chat_server.validation import validate_template_sql
from chat_tests.conftest import skip_no_db

# -- 1 & 2. Param binding + read-only connection ----------------------------


@skip_no_db
@pytest.mark.asyncio
async def test_sql_injection_payload_in_param_is_bound_not_executed():
    """A classic injection payload passed as a param value must be treated as a
    literal string — never executed as SQL. Proves the "no SQL injection" contract."""
    from chat_server.templates import get_template

    tmpl = get_template("season_thresholds.fifty_forty_ninety")
    payload = "25; DROP TABLE dim_player; --"
    # Layer 1: Pydantic rejects a non-numeric payload before the DB sees it.
    with pytest.raises(ValidationError):
        tmpl.params_model(min_ppg=payload)

    # Layer 2: for a string-typed param (season_year), the value is bound as a
    # literal. An injection string simply matches no rows — never executed.
    shot = get_template("shot_zones.corner_threes_split")
    malicious = "' OR 1=1; DROP TABLE dim_player; --"
    from chat_server.db import get_db as _get_db

    db = _get_db()
    result = await db.execute(
        shot.sql,
        {"player_id": 201939, "season_year": malicious, "season_type": "Regular"},
        limit=shot.default_limit,
    )
    assert result.row_count == 0
    intact = await db.execute("SELECT COUNT(*) AS n FROM dim_player")
    assert intact.rows[0]["n"] > 0, "dim_player survived — injection was a bound literal"


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


@skip_no_db
def test_every_registered_template_only_references_allowlisted_tables():
    """Each of the 20 templates validates clean against its own ALLOWED_TABLES,
    and is rejected when the allowlist is empty."""
    from chat_server.templates import get_registry

    registry = get_registry()
    assert len(registry) >= 20
    for tid, tmpl in registry.items():
        clean = validate_template_sql(tmpl.sql, tmpl.allowed_tables)
        assert clean.valid, f"{tid} should validate against its own allowlist: {clean.errors}"
        # An empty allowlist must reject it IF the SQL references any table.
        empty = validate_template_sql(tmpl.sql, set())
        if clean.tables_referenced:
            assert not empty.valid, f"{tid} should be rejected with an empty allowlist"


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
    report = validate_template_sql(sql, {"mart_player_season"})
    assert not report.valid, f"TVF must be rejected: {sql}"
    assert any("table-valued function" in e or "not allowed" in e for e in report.errors)


def test_catalog_qualified_table_reference_is_rejected():
    """A schema/catalog-qualified name must not slip through on leaf match (H1)."""
    report = validate_template_sql(
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
    report = validate_template_sql(sql, {"dim_player"})
    assert not report.valid, f"forbidden statement must be rejected: {sql}"


def test_multi_statement_injection_rejected():
    """A second statement smuggled after a SELECT must be rejected."""
    report = validate_template_sql("SELECT 1; DROP TABLE dim_player", {"dim_player"})
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


# -- 8. Non-streaming timeout (C1) -----------------------------------------


@pytest.mark.asyncio
async def test_non_streaming_chat_enforces_template_timeout(monkeypatch, tmp_path):
    """The non-streaming /api/chat wraps DB execution in asyncio.wait_for with
    the template's timeout — a slow query is cut off, not allowed to hang (C1)."""
    from fastapi.testclient import TestClient

    import chat_server.routes.chat as chat_routes
    from chat_server.agent import TemplatePlan
    from chat_server.db import QueryResult
    from chat_server.templates import get_template

    # A fake agent that returns a fixed plan (no Pydantic AI / no tools / no network)
    # so the route reaches the DB-execution step where the timeout applies.
    class _FakeResult:
        def __init__(self, output):
            self.output = output

    class _FakeAgent:
        async def run(self, message, deps=None):
            return _FakeResult(
                TemplatePlan(
                    template_id="season_thresholds.fifty_forty_ninety",
                    params={"min_ppg": 25.0},
                )
            )

    # A fake DB whose execute() blocks longer than the (lowered) timeout.
    slow_template = get_template("season_thresholds.fifty_forty_ninety")
    monkeypatch.setattr(slow_template, "timeout_seconds", 0.2, raising=False)

    class _SlowDB:
        async def execute(self, *a, **kw):
            await asyncio.sleep(2.0)  # well past the 0.2s timeout
            return QueryResult(columns=[], rows=[], row_count=0, duration_ms=0.0, truncated=False)

    async def _slow_make_deps():
        from chat_server.agent import AgentDeps
        from chat_server.schema_context import get_schema_context
        from chat_server.templates import get_registry

        return AgentDeps(
            registry=get_registry(),
            schema_context=await get_schema_context(),
            db=_SlowDB(),  # ty: ignore[invalid-argument-type] - test fake, not a real DuckDBSingleton
        )

    monkeypatch.setattr(chat_routes, "get_agent", lambda: _FakeAgent())
    monkeypatch.setattr(chat_routes, "make_deps", _slow_make_deps)
    # The route executes via get_db().execute(...) — patch THAT, not deps.db.
    monkeypatch.setattr(chat_routes, "get_db", lambda: _SlowDB())

    from chat_server.main import app

    client = TestClient(app)
    r = client.post("/api/chat", json={"message": "50-40-90 with 25 ppg"})
    assert r.status_code == 200
    body = r.json()
    # The slow query is cut off → not-answerable with the timeout note.
    assert body["not_answerable"] is True
    assert "timeout" in body["not_answerable_note"].lower()
