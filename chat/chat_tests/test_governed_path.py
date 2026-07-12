"""End-to-end governed SQL execution tests without a live warehouse."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, cast

from pydantic_ai.models.test import TestModel

from chat_server.agent import AgentDeps, _build_agent
from chat_server.db import DuckDBSingleton, QueryResult
from chat_server.events import AnswerFinished, ChatEvent, IntentClassified, QueryStarted, TableReady
from chat_server.pipeline import _query_ref, run_turn
from chat_server.schema_context import SchemaContext
from chat_server.semantic_catalog import load_catalog
from chat_server.sessions import SessionStore
from chat_server.sqlgate import ValidationReport


class _GovernedDb:
    """A minimal live-schema/runner seam used by the governed gate."""

    def __init__(self) -> None:
        self.dry_runs: list[str] = []
        self.executed: list[str] = []

    async def execute(self, sql: str, *args, **kwargs) -> QueryResult:
        if "information_schema.columns" in sql:
            return QueryResult(
                columns=["table_name", "column_name", "data_type"],
                rows=[
                    {
                        "table_name": "mart_player_career",
                        "column_name": "player_id",
                        "data_type": "BIGINT",
                    },
                    {
                        "table_name": "mart_player_career",
                        "column_name": "career_pts",
                        "data_type": "BIGINT",
                    },
                ],
                row_count=2,
                duration_ms=0,
                truncated=False,
            )
        self.executed.append(sql)
        return QueryResult(
            columns=["player_id", "career_pts"],
            rows=[{"player_id": 30, "career_pts": 100}],
            row_count=1,
            duration_ms=1,
            truncated=False,
        )

    async def dry_run(self, sql: str) -> None:
        self.dry_runs.append(sql)


class _SequentialTestModel(TestModel):
    """Emit the initial plan and repair plan from one injected agent."""

    def __init__(self, outputs: list[dict[str, Any]]) -> None:
        super().__init__(call_tools=[], custom_output_args=None)
        self.outputs = list(outputs)

    async def request(self, *args: Any, **kwargs: Any):  # type: ignore[override]
        self.custom_output_args = self.outputs.pop(0)
        return await super().request(*args, **kwargs)


async def _collect(gen: AsyncIterator[ChatEvent]) -> list[ChatEvent]:
    return [event async for event in gen]


def test_governed_sql_is_validated_dry_run_and_executed(monkeypatch, tmp_path):
    from chat_server import config, pipeline

    sql = "SELECT player_id, career_pts FROM mart_player_career LIMIT 3"
    agent = _build_agent(
        TestModel(
            call_tools=[],
            custom_output_args={
                "answer_mode": "execute_sql",
                "question_interpretation": "List a few player career totals.",
                "sql": sql,
                "result_contract": {
                    "grain": "one row per player",
                    "columns": ["player_id", "career_pts"],
                    "row_limit": 3,
                    "answer_style": "table",
                },
            },
        )
    )
    db = _GovernedDb()
    monkeypatch.setattr(pipeline, "get_agent", lambda: agent)

    async def make_deps() -> AgentDeps:
        return AgentDeps(
            schema_context=SchemaContext(), db=cast(DuckDBSingleton, db), catalog=load_catalog()
        )

    monkeypatch.setattr(pipeline, "make_deps", make_deps)
    monkeypatch.setattr(config.get_settings(), "chat_log_dir", str(tmp_path))
    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(pipeline, "get_store", lambda: store)

    session_id = store.create(title="governed").id
    events = asyncio.run(_collect(run_turn(session_id, "show career points")))

    intent = next(event for event in events if isinstance(event, IntentClassified))
    started = next(event for event in events if isinstance(event, QueryStarted))
    assert intent.query_ref.source == "catalog"
    assert intent.query_ref.tables == ["mart_player_career"]
    assert started.query_ref == intent.query_ref
    assert started.sql == sql
    assert db.dry_runs == [sql]
    assert db.executed == [sql]
    assert next(event for event in events if isinstance(event, AnswerFinished)).answer
    table_event = next(event for event in events if isinstance(event, TableReady))
    assert table_event.row_count == 1
    assert len(table_event.rows) == 1
    assert table_event.columns[0].name == "player_id"
    assert table_event.truncated is False
    assert "error" not in [event.event for event in events]
    query_logs = list((tmp_path / "queries").rglob("*.sql"))
    result_logs = list((tmp_path / "queries").rglob("*.result.json"))
    assert len(query_logs) == len(result_logs) == 1
    assert query_logs[0].read_text(encoding="utf-8") == sql


def test_gate_failure_enters_repair_before_execution(monkeypatch, tmp_path):
    from chat_server import config, pipeline

    invalid = "SELECT player_id FROM phantom_table"
    repaired = "SELECT player_id FROM mart_player_career LIMIT 2"
    agent = _build_agent(
        _SequentialTestModel(
            [
                {
                    "answer_mode": "execute_sql",
                    "question_interpretation": "Show player ids.",
                    "sql": invalid,
                    "result_contract": {"grain": "one row per player"},
                },
                {
                    "answer_mode": "execute_sql",
                    "question_interpretation": "Show player ids.",
                    "sql": repaired,
                    "result_contract": {"grain": "one row per player"},
                },
            ]
        )
    )
    db = _GovernedDb()
    monkeypatch.setattr(pipeline, "get_agent", lambda: agent)

    async def make_deps() -> AgentDeps:
        return AgentDeps(
            schema_context=SchemaContext(), db=cast(DuckDBSingleton, db), catalog=load_catalog()
        )

    monkeypatch.setattr(pipeline, "make_deps", make_deps)
    monkeypatch.setattr(config.get_settings(), "chat_log_dir", str(tmp_path))
    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(pipeline, "get_store", lambda: store)

    events = asyncio.run(_collect(run_turn(store.create(title="repair").id, "show players")))

    started = next(event for event in events if isinstance(event, QueryStarted))
    assert started.sql == repaired
    assert db.dry_runs == [repaired]
    assert db.executed == [repaired]


def test_query_ref_marks_non_catalog_tables_as_warehouse():
    catalog = load_catalog()
    # dim_team and fact_official_assignment are in ALLOWED_TABLES_FOR_AGENT
    # but neither is a catalog model base table.
    report = ValidationReport(
        valid=True, tables_referenced={"dim_team", "fact_official_assignment"}
    )

    ref = _query_ref(report, catalog)

    assert ref.source == "warehouse"
    assert ref.tables == ["dim_team", "fact_official_assignment"]
