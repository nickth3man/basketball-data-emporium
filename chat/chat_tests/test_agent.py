"""Tests for the governed-SQL agent's typed output contract.

All agent runs use ``TestModel``; this module never calls OpenRouter.
"""

from __future__ import annotations

import asyncio
from typing import cast

import pytest
from pydantic_ai.models.test import TestModel

from chat_server.agent import (
    AgentDeps,
    ClarifyPlan,
    NotAnswerablePlan,
    ResultContract,
    SqlPlan,
    _build_agent,
)
from chat_server.db import DuckDBSingleton
from chat_server.schema_context import SchemaContext


@pytest.fixture
def fresh_agent():
    """Return a fresh TestModel-backed agent and its mutable model."""
    model = TestModel(call_tools=[], custom_output_args=None)
    return _build_agent(model), model


def _noop_deps() -> AgentDeps:
    """Build deps for an output-only run; TestModel never invokes tools."""
    return AgentDeps(
        schema_context=SchemaContext(),
        db=cast(DuckDBSingleton, None),
        catalog=None,
    )


def test_agent_returns_sql_plan_with_result_contract(fresh_agent) -> None:
    agent, model = fresh_agent
    model.custom_output_args = {
        "answer_mode": "execute_sql",
        "question_interpretation": "Rank regular-season scoring averages.",
        "sql": "SELECT player_id FROM mart_player_season LIMIT 3",
        "result_contract": {
            "grain": "one row per player season",
            "columns": ["player_id"],
            "row_limit": 3,
            "answer_style": "ranked_list",
        },
    }

    result = asyncio.run(agent.run("Show scoring leaders", deps=_noop_deps()))

    assert isinstance(result.output, SqlPlan)
    assert result.output.sql == "SELECT player_id FROM mart_player_season LIMIT 3"
    assert result.output.result_contract == ResultContract(
        grain="one row per player season",
        columns=["player_id"],
        row_limit=3,
        answer_style="ranked_list",
    )
    assert result.usage is not None


def test_agent_returns_clarification_plan_and_coerces_bare_question(fresh_agent) -> None:
    agent, model = fresh_agent
    model.custom_output_args = {
        "answer_mode": "clarify",
        "clarification": "Which season should I use?",
    }

    result = asyncio.run(agent.run("Who led the league?", deps=_noop_deps()))

    assert isinstance(result.output, ClarifyPlan)
    assert result.output.clarification.question == "Which season should I use?"
    assert result.output.clarification.options == []


def test_agent_returns_not_answerable_plan(fresh_agent) -> None:
    agent, model = fresh_agent
    model.custom_output_args = {
        "answer_mode": "not_answerable",
        "not_answerable_note": "The warehouse does not contain college statistics.",
    }

    result = asyncio.run(agent.run("What was his college PER?", deps=_noop_deps()))

    assert isinstance(result.output, NotAnswerablePlan)
    assert "college statistics" in result.output.not_answerable_note


def test_sql_plan_requires_non_empty_sql() -> None:
    with pytest.raises(ValueError):
        SqlPlan(sql="")
