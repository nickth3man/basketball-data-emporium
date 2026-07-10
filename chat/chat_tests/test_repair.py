"""Phase-5 repair coverage: two bounded, fully checked repair rounds."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import duckdb
from pydantic_ai.models.test import TestModel

from chat_server.agent import AgentDeps, _build_agent
from chat_server.db import DryRunError, DuckDBSingleton, QueryResult
from chat_server.repair import MAX_ROUND, repair_sql
from chat_server.schema_context import SchemaContext
from chat_server.semantic_catalog import load_catalog


class _SequentialTestModel(TestModel):
    def __init__(self, outputs: list[dict[str, Any]]) -> None:
        super().__init__(call_tools=[], custom_output_args=None)
        self.outputs = list(outputs)

    async def request(self, *args: Any, **kwargs: Any):  # type: ignore[override]
        self.custom_output_args = self.outputs.pop(0)
        return await super().request(*args, **kwargs)


class _RepairDb:
    def __init__(self, dry_run_failures: set[str] | None = None) -> None:
        self.dry_run_failures = dry_run_failures or set()
        self.dry_runs: list[str] = []
        self.schema_reads = 0

    async def execute(self, sql: str, *args, **kwargs) -> QueryResult:
        assert "information_schema.columns" in sql
        self.schema_reads += 1
        return QueryResult(
            columns=["table_name", "column_name", "data_type"],
            rows=[
                {
                    "table_name": "mart_player_career",
                    "column_name": "player_id",
                    "data_type": "BIGINT",
                }
            ],
            row_count=1,
            duration_ms=0,
            truncated=False,
        )

    async def dry_run(self, sql: str) -> None:
        self.dry_runs.append(sql)
        if sql in self.dry_run_failures:
            raise DryRunError(sql, duckdb.Error("planner rejected candidate"))


def _plan(sql: str) -> dict[str, Any]:
    return {
        "answer_mode": "execute_sql",
        "question_interpretation": "Show player ids.",
        "sql": sql,
        "result_contract": {"grain": "one row per player", "answer_style": "prose"},
    }


def _repair(outputs: list[dict[str, Any]], db: _RepairDb):
    agent = _build_agent(_SequentialTestModel(outputs))
    deps = AgentDeps(
        schema_context=SchemaContext(), db=cast(DuckDBSingleton, db), catalog=load_catalog()
    )
    return asyncio.run(
        repair_sql(
            agent,
            deps,
            question="show players",
            broken_sql="SELECT * FROM phantom_table",
            error="table does not exist",
            db=cast(DuckDBSingleton, db),
        )
    )


def test_repair_revalidates_and_dry_runs_each_viable_candidate():
    invalid = "SELECT * FROM still_phantom_table"
    valid = "SELECT player_id FROM mart_player_career LIMIT 2"
    db = _RepairDb()

    repaired = _repair([_plan(invalid), _plan(valid)], db)

    assert MAX_ROUND == 2
    assert repaired is not None and repaired.sql == valid
    # Both candidates re-enter the gate; only the gate-approved one dry-runs.
    assert db.schema_reads == 2
    assert db.dry_runs == [valid]


def test_repair_retries_after_candidate_dry_run_failure():
    first = "SELECT player_id FROM mart_player_career LIMIT 1"
    fixed = "SELECT player_id FROM mart_player_career LIMIT 2"
    db = _RepairDb(dry_run_failures={first})

    repaired = _repair([_plan(first), _plan(fixed)], db)

    assert repaired is not None and repaired.sql == fixed
    assert db.schema_reads == 2
    assert db.dry_runs == [first, fixed]


def test_repair_exhaustion_and_decline_return_none():
    db = _RepairDb()
    exhausted = _repair(
        [_plan("SELECT * FROM phantom_one"), _plan("SELECT * FROM phantom_two")], db
    )
    assert exhausted is None
    assert db.schema_reads == MAX_ROUND
    assert db.dry_runs == []

    declined = _repair(
        [
            {
                "answer_mode": "not_answerable",
                "question_interpretation": "No matching data exists.",
                "not_answerable_note": "I cannot repair this safely.",
            }
        ],
        _RepairDb(),
    )
    assert declined is None
