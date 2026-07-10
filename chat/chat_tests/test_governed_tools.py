"""Tests for governed catalog and live-warehouse introspection tools."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, cast

import pytest
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.models.test import TestModel

from chat_server.agent import (
    SYSTEM_PROMPT_TEMPLATE_GOVERNED,
    AgentDeps,
    _build_agent,
    _catalog_summary,
)
from chat_server.db import DuckDBSingleton, get_db
from chat_server.schema_context import SchemaContext
from chat_server.semantic_catalog.loader import SemanticCatalog
from chat_server.semantic_catalog.schema import BaseTable, BusinessModel, Dimension, Measure
from chat_tests.conftest import skip_no_db


def _make_catalog() -> SemanticCatalog:
    return SemanticCatalog(
        {
            "player_season": BusinessModel(
                model="player_season",
                description="Per-player per-season aggregates.",
                grain="one row per player per season",
                base_table=BaseTable(name="mart_player_season", alias="ps"),
                dimensions=[
                    Dimension(name="player_id", expr="ps.player_id", description="Player key"),
                ],
                measures=[
                    Measure(
                        name="ppg",
                        expr="ps.avg_pts",
                        description="Points per game",
                        additivity="non_additive",
                    )
                ],
            ),
            "team_season": BusinessModel(
                model="team_season",
                description="Per-team per-season summary.",
                grain="one row per team per season",
                base_table=BaseTable(name="mart_team_season", alias="ts"),
            ),
        }
    )


class _Ctx:
    def __init__(self, deps: AgentDeps) -> None:
        self.deps = deps


@pytest.fixture
def agent():
    return _build_agent(TestModel())


def _deps(*, catalog: SemanticCatalog | None, db: DuckDBSingleton | None = None) -> AgentDeps:
    return AgentDeps(schema_context=SchemaContext(), db=cast(DuckDBSingleton, db), catalog=catalog)


def _tool(agent: Any, name: str) -> Any:
    """Return a registered tool's original callable for focused tool tests."""
    toolsets = [agent._function_toolset, *getattr(agent, "_toolsets", [])]
    for toolset in toolsets:
        for value in vars(toolset).values():
            if not isinstance(value, dict) or name not in value:
                continue
            tool = value[name]
            fn = getattr(tool, "function", None) or getattr(tool, "fn", None) or tool
            if callable(fn):
                return fn
    raise AssertionError(f"registered tool {name!r} was not found")


def _call_tool(agent: Any, name: str, deps: AgentDeps, **kwargs: Any) -> Any:
    value = _tool(agent, name)(_Ctx(deps), **kwargs)
    return asyncio.run(value) if inspect.isawaitable(value) else value


def test_catalog_summary_is_deterministic_and_includes_grain() -> None:
    summary = _catalog_summary(_make_catalog())
    assert "mart_player_season" in summary
    assert "grain: one row per player per season" in summary
    assert summary.index("player_season") < summary.index("team_season")


def test_governed_prompt_documents_sql_and_introspection_surface() -> None:
    prompt = SYSTEM_PROMPT_TEMPLATE_GOVERNED.format(
        schema_context="(approved schemas)",
        catalog_summary="- player_season",
    )
    assert "SELECT only" in prompt
    assert "list_warehouse_tables" in prompt
    assert "describe_table" in prompt
    assert "preview" in prompt


def test_catalog_tools_return_models_and_retry_for_unknown_model(agent) -> None:
    deps = _deps(catalog=_make_catalog())
    models = _call_tool(agent, "list_models", deps)
    assert {model["model"] for model in models} == {"player_season", "team_season"}

    detail = _call_tool(agent, "get_model_detail", deps, model="player_season")
    assert detail["base_table"] == {"name": "mart_player_season", "alias": "ps"}
    assert detail["measures"][0]["additivity"] == "non_additive"

    with pytest.raises(ModelRetry):
        _call_tool(agent, "get_model_detail", deps, model="unknown_model")


@skip_no_db
def test_live_warehouse_introspection_tools_only_expose_approved_tables(agent) -> None:
    deps = _deps(catalog=None, db=get_db())
    tables = _call_tool(agent, "list_warehouse_tables", deps)
    assert "dim_player" in tables
    assert tables == sorted(tables)
    assert all(table.startswith(("dim_", "fact_", "mart_", "analytics_")) for table in tables)

    columns = _call_tool(agent, "describe_table", deps, table="dim_player")
    assert any(column["name"] == "player_id" for column in columns)

    rows = _call_tool(agent, "preview", deps, table="dim_player", n=1)
    assert len(rows) <= 1


class _FailingDb:
    """Minimal mock that raises on any execute call."""

    async def execute(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("no db")


def test_introspection_tools_reject_unapproved_tables_and_bad_preview_size(agent) -> None:
    deps = _deps(catalog=None)
    with pytest.raises(ModelRetry):
        _call_tool(agent, "describe_table", deps, table="src_secret")
    with pytest.raises(ModelRetry):
        _call_tool(agent, "preview", deps, table="dim_player", n=6)


def test_preview_rejects_adversarial_identifiers(agent) -> None:
    """Preview must reject table names containing SQL metacharacters
    even when they start with an approved prefix."""
    deps = _deps(catalog=None)
    adversarial = [
        'dim_evil" UNION SELECT sql FROM duckdb_tables() --',
        "dim_x; DROP TABLE dim_player",
        'dim_y" OR "1"="1',
        "dim_a\\' OR \\'1\\'=\\'1",
        "dim_table--comment",
        "dim_tab\r\n",
        "dim_tab\x00",
    ]
    for table in adversarial:
        with pytest.raises(ModelRetry):
            _call_tool(agent, "preview", deps, table=table, n=3)


def test_preview_accepts_valid_identifier_past_regex(agent) -> None:
    """A clean identifier with an approved prefix must pass the regex
    check and only fail on the DB side (no warehouse in unit tests)."""
    deps = _deps(catalog=None, db=cast(DuckDBSingleton, _FailingDb()))
    # The call should NOT raise ModelRetry from the identifier check.
    # It may raise from the DB call (no warehouse), which is fine —
    # we just need to prove the regex didn't reject it.
    with pytest.raises(RuntimeError, match="no db"):
        _call_tool(agent, "preview", deps, table="dim_player", n=3)
