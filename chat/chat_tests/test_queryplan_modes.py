"""Tests for the additive QueryPlan restructure (Stage 3.1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from chat_server.agent import AnswerMode, Clarification, QueryPlan, ResultContract


def test_legacy_dict_validates_as_template() -> None:
    plan = QueryPlan(template_id="season_thresholds", params={"min_pts": 20})
    assert plan.answer_mode == AnswerMode.TEMPLATE
    assert plan.sql is None


def test_legacy_clarification_str_still_validates() -> None:
    plan = QueryPlan(clarification="Which season?")
    # answer_mode defaults to TEMPLATE; the str is accepted by the union
    assert plan.clarification == "Which season?"


def test_execute_sql_requires_sql() -> None:
    with pytest.raises(ValidationError):
        QueryPlan(answer_mode=AnswerMode.EXECUTE_SQL, result_contract=ResultContract(grain="x"))


def test_execute_sql_with_sql_and_contract_validates() -> None:
    plan = QueryPlan(
        answer_mode=AnswerMode.EXECUTE_SQL,
        sql="SELECT 1",
        result_contract=ResultContract(grain="one row"),
    )
    assert plan.sql == "SELECT 1"


def test_structured_clarification_round_trips() -> None:
    c = Clarification(question="Which metric?", options=["averages", "totals"])
    plan = QueryPlan(answer_mode=AnswerMode.CLARIFY, clarification=c)
    assert isinstance(plan.clarification, Clarification)
    assert plan.clarification.options == ["averages", "totals"]
