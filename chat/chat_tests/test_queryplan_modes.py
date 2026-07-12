"""Tests for the `Plan` discriminated union (ARCHITECTURE.md §9 step 3).

The v1 `QueryPlan` additive superset carried every mode's fields at once
and relied on a documented check order. The union makes illegal states
unrepresentable; these tests pin the discriminator contract:

* each member validates from a plain dict via `TypeAdapter(Plan)`;
* the discriminator (`answer_mode`) is required — a dict without it is
  rejected (the silent TEMPLATE default is retired);
* per-member requireds hold (SqlPlan needs non-empty sql, ClarifyPlan
  needs a clarification, NotAnswerablePlan needs a note);
* a bare-str clarification is coerced into `Clarification`.
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from chat_server.agent import (
    Clarification,
    ClarifyPlan,
    NotAnswerablePlan,
    Plan,
    ResultContract,
    SqlPlan,
)

PLAN_ADAPTER: TypeAdapter = TypeAdapter(Plan)


def test_each_member_validates_via_discriminator() -> None:
    cases = [
        ({"answer_mode": "clarify", "clarification": {"question": "Which season?"}}, ClarifyPlan),
        ({"answer_mode": "not_answerable", "not_answerable_note": "no data"}, NotAnswerablePlan),
        ({"answer_mode": "execute_sql", "sql": "SELECT 1"}, SqlPlan),
    ]
    for payload, expected_type in cases:
        plan = PLAN_ADAPTER.validate_python(payload)
        assert isinstance(plan, expected_type), (payload, type(plan))


def test_missing_discriminator_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PLAN_ADAPTER.validate_python({"sql": "SELECT 1"})


def test_execute_sql_requires_nonempty_sql() -> None:
    with pytest.raises(ValidationError):
        SqlPlan(sql="")
    with pytest.raises(ValidationError):
        PLAN_ADAPTER.validate_python({"answer_mode": "execute_sql"})


def test_sql_plus_clarification_is_unrepresentable() -> None:
    """The superset's illegal state: extra fields don't smuggle a second
    mode into a member (SqlPlan has no clarification field)."""
    plan = PLAN_ADAPTER.validate_python(
        {
            "answer_mode": "execute_sql",
            "sql": "SELECT 1",
            "clarification": "Which season?",
        }
    )
    assert isinstance(plan, SqlPlan)
    assert not hasattr(plan, "clarification")


def test_execute_sql_with_contract_validates() -> None:
    plan = SqlPlan(sql="SELECT 1", result_contract=ResultContract(grain="one row"))
    assert plan.sql == "SELECT 1"
    assert plan.result_contract is not None
    assert plan.result_contract.grain == "one row"


def test_clarify_requires_clarification() -> None:
    with pytest.raises(ValidationError):
        PLAN_ADAPTER.validate_python({"answer_mode": "clarify"})


def test_bare_str_clarification_is_coerced() -> None:
    plan = ClarifyPlan(clarification="Which season?")
    assert isinstance(plan.clarification, Clarification)
    assert plan.clarification.question == "Which season?"
    assert plan.clarification.options == []


def test_structured_clarification_round_trips() -> None:
    c = Clarification(question="Which metric?", options=["averages", "totals"])
    plan = ClarifyPlan(clarification=c)
    assert isinstance(plan.clarification, Clarification)
    assert plan.clarification.options == ["averages", "totals"]


def test_not_answerable_requires_note() -> None:
    with pytest.raises(ValidationError):
        PLAN_ADAPTER.validate_python({"answer_mode": "not_answerable"})
    with pytest.raises(ValidationError):
        NotAnswerablePlan(not_answerable_note="")
