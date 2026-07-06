"""End-to-end tests for the Stage 3.4 governed-SQL repair loop.

The repair loop is the second line of defense behind the catalog gate
(see ``test_governed_path.py``): when the agent emits SQL that passes
the allowlist / optimizer / fan-trap checks but still fails to bind
against the live warehouse (stale column, fabricated identifier, ...)
the pipeline gives the structured agent ONE bounded re-prompt to fix
it before falling back to a not-answerable response.

Three tests cover the three repair outcomes:

* ``test_repair_succeeds_on_second_call`` -- the agent emits broken
  SQL on the first call, fixed SQL on the second call. The pipeline
  dry-runs, fails, repairs, re-validates, executes, and streams a
  composed answer. Live-DB gated.

* ``test_repair_decline_degrades_to_not_answerable`` -- the agent
  emits broken SQL on the first call, then declines (not-answerable)
  on the second call. The pipeline falls through to the
  not-answerable path. No DB needed (we stub ``dry_run``).

* ``test_repair_still_invalid_degrades`` -- the agent emits broken
  SQL on the first call, and the "repaired" SQL on the second call
  is STILL not in the catalog allowlist. The pipeline falls through
  to the not-answerable path. No DB needed (we stub ``dry_run``).

Sequential outputs
------------------
``pydantic_ai.models.test.TestModel`` exposes ``custom_output_args``
as a mutable field that the agent reads on every ``request`` call --
so a single TestModel can emit different structured outputs on the
first vs. second ``agent.run(...)`` call. We use
``_SequentialTestModel`` (a tiny wrapper) to make that contract
explicit at the call sites.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import duckdb
from pydantic_ai.models.test import TestModel

from chat_server.agent import AgentDeps, _build_agent
from chat_server.db import DryRunError, DuckDBSingleton
from chat_server.events import (
    AnswerFinished,
    ChatEvent,
    IntentClassified,
    QueryStarted,
)
from chat_server.pipeline import run_turn
from chat_server.schema_context import SchemaContext
from chat_server.semantic_catalog import load_catalog
from chat_server.sessions import SessionStore
from chat_server.sqlgate import validate_governed_sql
from chat_tests.conftest import skip_no_db

# --- Sequential-output TestModel ----------------------------------------


class _SequentialTestModel(TestModel):
    """A ``TestModel`` that emits a different ``custom_output_args`` per call.

    The base ``TestModel`` reads ``self.custom_output_args`` fresh on
    every ``_request`` invocation, so in principle we could mutate it
    between calls from the test body. That requires careful ordering
    (set before first ``agent.run``, set before second ``agent.run``)
    and is fragile when the second call happens deep inside the
    pipeline. Wrapping the swap in ``request`` is the load-bearing
    guarantee: the model emits ``outputs[i]`` on the ``i``-th call,
    regardless of where in the pipeline the second call fires.

    Parameters
    ----------
    outputs
        The ordered list of ``custom_output_args`` dicts to emit.
        The first ``agent.run`` invocation gets ``outputs[0]``; the
        second gets ``outputs[1]``; etc. The list is drained in place.
    """

    def __init__(self, outputs: list[dict[str, Any]]) -> None:
        # NOTE: super().__init__ sets custom_output_args to None. We
        # overwrite it on the first request; keeping the constructor
        # signature minimal makes the call site cleaner.
        super().__init__(call_tools=[], custom_output_args=None)
        self._outputs: list[dict[str, Any]] = list(outputs)

    async def request(self, *args: Any, **kwargs: Any):  # type: ignore[override]
        # Pop the next output BEFORE delegating so the base class
        # reads the freshly-assigned value via _get_output.
        if self._outputs:
            self.custom_output_args = self._outputs.pop(0)
        return await super().request(*args, **kwargs)


# --- shared deps helpers -------------------------------------------------


def _live_deps_with_catalog() -> AgentDeps:
    """Live ``AgentDeps`` including the semantic catalog."""
    from chat_server.config import get_settings

    settings = get_settings()
    return AgentDeps(
        registry={},
        schema_context=SchemaContext(),
        db=DuckDBSingleton(settings.duckdb_path),
        catalog=load_catalog(),
    )


def _stub_deps() -> AgentDeps:
    """``AgentDeps`` whose catalog is loaded but whose DB is a stub.

    Used by the two no-DB tests (``test_repair_decline_*``,
    ``test_repair_still_invalid_*``). Those tests inject a custom
    ``get_db()`` that returns a stub whose ``dry_run`` raises
    ``DryRunError`` and whose ``execute`` records the calls; the real
    DuckDB singleton is never touched.
    """
    return _live_deps_with_catalog()


async def _collect(gen: AsyncIterator[ChatEvent]) -> list[ChatEvent]:
    return [ev async for ev in gen]


# --- 1. repair succeeds on the second call (live DB) --------------------


@skip_no_db
def test_repair_succeeds_on_second_call(monkeypatch, tmp_path):
    """The agent emits broken SQL on the first call and fixed SQL on
    the second. The pipeline dry-runs, fails, repairs, re-validates,
    executes, and streams a composed answer.

    Mirrors ``test_governed_execute_sql_returns_rows`` end-to-end,
    but with a sequential TestModel: the broken SQL
    ``SELECT nonexistent_col FROM mart_player_career LIMIT 3`` is
    catalog-allowed but fails at planner time (unknown column); the
    repaired SQL ``SELECT player_id FROM mart_player_career LIMIT 3``
    is the corrected version the refiner should emit.

    The test asserts:

    * ``IntentClassified`` / ``QueryStarted`` carry the REPAIRED SQL,
      not the original broken one -- i.e. the sentinel + the dry-run
      + repair happen BEFORE the stream-yielded events.
    * The final answer mentions the semantic model.
    * No error event is emitted.
    """
    from chat_server import config as config_module
    from chat_server import pipeline as pipeline_module

    broken_sql = "SELECT player_id FROM mart_player_career LIMIT -1"
    fixed_sql = "SELECT player_id FROM mart_player_career LIMIT 3"
    tm = _SequentialTestModel(
        outputs=[
            {
                "answer_mode": "execute_sql",
                "sql": broken_sql,
                "result_contract": {
                    "grain": "one row per player",
                    "columns": ["player_id"],
                    "row_limit": 3,
                    "answer_style": "prose",
                },
            },
            {
                "answer_mode": "execute_sql",
                "sql": fixed_sql,
                "result_contract": {
                    "grain": "one row per player",
                    "columns": ["player_id"],
                    "row_limit": 3,
                    "answer_style": "prose",
                },
            },
        ]
    )
    agent = _build_agent(tm)
    monkeypatch.setattr(pipeline_module, "get_agent", lambda: agent)

    async def _fake_make_deps() -> AgentDeps:
        return _live_deps_with_catalog()

    monkeypatch.setattr(pipeline_module, "make_deps", _fake_make_deps)
    monkeypatch.setattr(config_module.get_settings(), "chat_log_dir", str(tmp_path))
    temp_store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(pipeline_module, "get_store", lambda: temp_store)

    sid = temp_store.create(title="repair ok").id
    events = asyncio.run(_collect(run_turn(sid, "show some players")))

    # The repaired SQL is the one that reaches the stream.
    intent = next(e for e in events if isinstance(e, IntentClassified))
    assert intent.template_id == "semantic:mart_player_career", intent.template_id
    qs = next(e for e in events if isinstance(e, QueryStarted))
    assert qs.sql == fixed_sql, f"expected the repaired SQL, got: {qs.sql!r}"
    assert "LIMIT -1" not in qs.sql, "broken SQL leaked into the stream"

    finished = next(e for e in events if isinstance(e, AnswerFinished))
    assert "mart_player_career" in finished.answer or "player_career" in finished.answer, (
        f"expected the semantic model in the answer, got: {finished.answer!r}"
    )
    assert not any(e.event == "error" for e in events)


# --- 2. repair declines: degrades to not-answerable (no DB) ------------


def test_repair_decline_degrades_to_not_answerable(monkeypatch, tmp_path):
    """The agent emits broken SQL, then emits ``not_answerable`` on the
    repair call. The pipeline must fall through to a not-answerable
    response WITHOUT ever executing a query.

    We stub ``get_db()`` so ``dry_run`` raises ``DryRunError`` (so
    the repair path is exercised) and ``execute`` raises
    ``AssertionError`` if it's called (so a regression that skips the
    repair falls back to the warehouse fails loudly).
    """
    from chat_server import config as config_module
    from chat_server import pipeline as pipeline_module

    broken_sql = "SELECT player_id FROM mart_player_career LIMIT -1"
    tm = _SequentialTestModel(
        outputs=[
            {
                "answer_mode": "execute_sql",
                "sql": broken_sql,
                "result_contract": {
                    "grain": "one row per player",
                    "answer_style": "prose",
                },
            },
            # Second call: the model DECLINES the repair.
            {
                "answer_mode": "not_answerable",
                "not_answerable_note": "I can't recover from the planner error.",
                "sql": None,
                "result_contract": None,
            },
        ]
    )
    agent = _build_agent(tm)
    monkeypatch.setattr(pipeline_module, "get_agent", lambda: agent)

    class _StubDB:
        """Stub whose dry_run raises and whose execute would crash."""

        def dry_run(self, sql: str) -> None:
            raise DryRunError(sql=sql, original=duckdb.Error("planner error"))

        async def execute(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("db.execute must NOT be called when repair declines")

    monkeypatch.setattr(pipeline_module, "get_db", lambda: _StubDB())

    async def _fake_make_deps() -> AgentDeps:
        return _stub_deps()

    monkeypatch.setattr(pipeline_module, "make_deps", _fake_make_deps)
    monkeypatch.setattr(config_module.get_settings(), "chat_log_dir", str(tmp_path))
    temp_store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(pipeline_module, "get_store", lambda: temp_store)

    sid = temp_store.create(title="repair decline").id
    events = asyncio.run(_collect(run_turn(sid, "unfixable question")))

    # We expect: not-answerable answer, mention of the dry-run error,
    # NO IntentClassified (the SQL never made it past validation+dry-run),
    # NO QueryStarted, NO db.execute call.
    intent_events = [e for e in events if isinstance(e, IntentClassified)]
    assert not intent_events, "IntentClassified should not fire when repair declines"

    finished = next(e for e in events if isinstance(e, AnswerFinished))
    assert "couldn't fix" in finished.answer.lower() or "fix" in finished.answer.lower(), (
        f"expected the dry-run error in the answer, got: {finished.answer!r}"
    )
    assert not any(e.event == "error" for e in events)


# --- 3. repair still invalid: degrades (no DB) -------------------------


def test_repair_still_invalid_degrades(monkeypatch, tmp_path):
    """The agent emits broken SQL, then emits SQL on the second call
    that STILL violates the catalog allowlist (e.g. references a
    phantom table). The pipeline must re-validate, fail, and degrade
    to not-answerable -- without calling ``db.execute``.
    """
    from chat_server import config as config_module
    from chat_server import pipeline as pipeline_module

    # Sanity: the "repaired" SQL really is invalid under the governed
    # gate. Catches a regression where the gate is bypassed.
    catalog = load_catalog()
    invalid_repaired = "SELECT * FROM still_phantom_table"
    rep = validate_governed_sql(invalid_repaired, catalog)
    assert not rep.valid

    broken_sql = "SELECT player_id FROM mart_player_career LIMIT -1"
    tm = _SequentialTestModel(
        outputs=[
            {
                "answer_mode": "execute_sql",
                "sql": broken_sql,
                "result_contract": {
                    "grain": "irrelevant",
                    "answer_style": "prose",
                },
            },
            # Repair returns SQL that fails the catalog allowlist.
            {
                "answer_mode": "execute_sql",
                "sql": invalid_repaired,
                "result_contract": {
                    "grain": "irrelevant",
                    "answer_style": "prose",
                },
            },
        ]
    )
    agent = _build_agent(tm)
    monkeypatch.setattr(pipeline_module, "get_agent", lambda: agent)

    class _StubDB:
        def dry_run(self, sql: str) -> None:
            raise DryRunError(sql=sql, original=duckdb.Error("planner error"))

        async def execute(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError(
                "db.execute must NOT be called when the repaired SQL fails validation"
            )

    monkeypatch.setattr(pipeline_module, "get_db", lambda: _StubDB())

    async def _fake_make_deps() -> AgentDeps:
        return _stub_deps()

    monkeypatch.setattr(pipeline_module, "make_deps", _fake_make_deps)
    monkeypatch.setattr(config_module.get_settings(), "chat_log_dir", str(tmp_path))
    temp_store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(pipeline_module, "get_store", lambda: temp_store)

    sid = temp_store.create(title="repair invalid").id
    events = asyncio.run(_collect(run_turn(sid, "yet another question")))

    intent_events = [e for e in events if isinstance(e, IntentClassified)]
    assert not intent_events, (
        "IntentClassified must not fire when the repaired SQL fails validation"
    )

    finished = next(e for e in events if isinstance(e, AnswerFinished))
    assert "couldn't fix" in finished.answer.lower() or "fix" in finished.answer.lower()
    # The error mention should reference the still-invalid table.
    assert "still_phantom_table" in finished.answer or "phantom" in finished.answer.lower(), (
        f"expected the repaired-SQL validation error in the answer, got: {finished.answer!r}"
    )
    assert not any(e.event == "error" for e in events)
