"""End-to-end tests for the governed-SQL branch (Stage 3.3b).

These tests pin the wiring between `chat_server.pipeline.run_turn` /
`chat_server.routes.chat.chat` and the governed-SQL helpers
(``validate_governed_sql``, ``compose_governed``). No live OpenRouter
calls -- every test uses ``pydantic_ai.models.test.TestModel`` to drive
the agent with a canned ``QueryPlan``.

Coverage
--------
* ``test_governed_execute_sql_returns_rows`` -- the happy path:
  a real catalog + valid SQL produces a composed answer that mentions
  the sentinel ``semantic:mart_player_career``; the streaming events
  carry the same sentinel on ``IntentClassified`` / ``QueryStarted`` /
  ``ChatResponse.template_id``. Live-DB gated.
* ``test_governed_invalid_sql_degrades_to_not_answerable`` -- SQL that
  the legacy gate rejects (table outside the catalog allowlist) is
  degraded to a not-answerable response WITHOUT touching the warehouse.
* ``test_governed_catalog_none_degrades`` -- when ``AgentDeps.catalog``
  is ``None`` the governed branch falls back to a not-answerable
  response WITHOUT touching the warehouse.
* ``test_legacy_template_path_still_works`` -- regression guard that
  the legacy ``{template_id, params}`` plan still hits the unchanged
  ``else`` branch (Stage 3.3b inserts the new branch in between -- a
  reordering or branch-numbering mistake would break this).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from pydantic_ai.models.test import TestModel

from chat_server.agent import AgentDeps, _build_agent
from chat_server.composer import compose_not_answerable
from chat_server.db import DuckDBSingleton
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

# --- helpers -------------------------------------------------------------


def _make_test_agent(output_args: dict) -> tuple[TestModel, object]:
    """Build a TestModel-backed agent with ``custom_output_args`` set."""
    tm = TestModel(call_tools=[], custom_output_args=output_args)
    agent = _build_agent(tm)
    return tm, agent


def _live_deps_with_catalog() -> AgentDeps:
    """Build live ``AgentDeps`` including the semantic catalog.

    Mirrors ``_live_deps`` from ``test_pipeline.py`` but additionally
    loads the catalog so the governed-SQL branch can validate. The
    loader is module-cached so the cost is paid once per process.
    """
    from chat_server.config import get_settings

    settings = get_settings()
    return AgentDeps(
        registry={},
        schema_context=SchemaContext(),
        db=DuckDBSingleton(settings.duckdb_path),
        catalog=load_catalog(),
    )


def _live_deps_no_catalog() -> AgentDeps:
    """Build live ``AgentDeps`` WITHOUT a semantic catalog.

    Used by ``test_governed_catalog_none_degrades`` so the governed
    branch's first guard (catalog is None -> not-answerable) fires
    before any DB work happens. No warehouse access required.
    """
    from chat_server.config import get_settings

    settings = get_settings()
    return AgentDeps(
        registry={},
        schema_context=SchemaContext(),
        db=DuckDBSingleton(settings.duckdb_path),
        catalog=None,
    )


async def _collect(gen: AsyncIterator[ChatEvent]) -> list[ChatEvent]:
    """Drain an async generator into a list (test helper)."""
    return [ev async for ev in gen]


# --- 1. happy path: live execute emits the sentinel on every event ----


@skip_no_db
def test_governed_execute_sql_returns_rows(monkeypatch, tmp_path):
    """A governed ``EXECUTE_SQL`` plan runs end-to-end through the
    pipeline: the SQL is validated, executed, composed, and streamed
    back. Every governed turn uses the ``semantic:<base_table>``
    sentinel as its ``template_id``.
    """
    from chat_server import config as config_module
    from chat_server import pipeline as pipeline_module

    tm, agent = _make_test_agent(
        {
            "answer_mode": "execute_sql",
            "sql": "SELECT player_id, career_pts FROM mart_player_career LIMIT 3",
            "result_contract": {
                "grain": "one row per player",
                "columns": ["player_id", "career_pts"],
                "row_limit": 3,
                "answer_style": "prose",
            },
        }
    )
    monkeypatch.setattr(pipeline_module, "get_agent", lambda: agent)

    async def _fake_make_deps() -> AgentDeps:
        return _live_deps_with_catalog()

    monkeypatch.setattr(pipeline_module, "make_deps", _fake_make_deps)
    monkeypatch.setattr(config_module.get_settings(), "chat_log_dir", str(tmp_path))
    temp_store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(pipeline_module, "get_store", lambda: temp_store)

    sid = temp_store.create(title="governed").id
    events = asyncio.run(_collect(run_turn(sid, "some player career question")))

    # Sanity: we actually reached the governed branch (vs the unchanged
    # legacy else). The sentinel on IntentClassified is the unambiguous
    # marker.
    intent = next(e for e in events if isinstance(e, IntentClassified))
    assert intent.template_id == "semantic:mart_player_career", intent.template_id
    assert intent.confidence == 1.0

    # QueryStarted carries the same sentinel (no schema change to events).
    qs = next(e for e in events if isinstance(e, QueryStarted))
    assert qs.template_id == "semantic:mart_player_career"
    assert qs.sql == "SELECT player_id, career_pts FROM mart_player_career LIMIT 3"

    # Compose emits an answer that mentions the semantic model.
    finished = next(e for e in events if isinstance(e, AnswerFinished))
    assert finished.answer
    assert "mart_player_career" in finished.answer or "player_career" in finished.answer, (
        f"expected the semantic model name in the answer, got: {finished.answer!r}"
    )

    # No error event -- the happy path.
    assert not any(e.event == "error" for e in events)


# --- 2. invalid SQL: validation rejects before the warehouse is hit ---


def test_governed_invalid_sql_degrades_to_not_answerable(monkeypatch, tmp_path):
    """A governed plan whose SQL references a table outside the catalog
    allowlist degrades to a not-answerable response WITHOUT touching
    the warehouse.

    Verifies both the validator contract (``report.valid`` flips to
    False when the legacy gate rejects) and the branch wiring (the
    pipeline streams a not-answerable answer and never executes the
    query).
    """
    from chat_server import config as config_module
    from chat_server import pipeline as pipeline_module

    catalog = load_catalog()
    # Direct sanity: the SQL really is invalid under the governed gate.
    report = validate_governed_sql("SELECT * FROM phantom_table", catalog)
    assert not report.valid
    assert report.errors, "expected at least one error from the legacy gate"

    tm, agent = _make_test_agent(
        {
            "answer_mode": "execute_sql",
            "sql": "SELECT * FROM phantom_table",
            "result_contract": {
                "grain": "irrelevant",
                "answer_style": "prose",
            },
        }
    )
    monkeypatch.setattr(pipeline_module, "get_agent", lambda: agent)

    # Track whether the DB was called -- if it was, the test fails (the
    # validation gate should have rejected BEFORE we got to execute).
    class _ExplodingDB:
        def execute(self, *args, **kwargs):
            raise AssertionError("db.execute must NOT be called when validation fails")

    monkeypatch.setattr(pipeline_module, "get_db", lambda: _ExplodingDB())

    async def _fake_make_deps() -> AgentDeps:
        # Catalog present so the catalog-None branch doesn't shadow the
        # validation-rejection branch we want to exercise.
        return AgentDeps(
            registry={},
            schema_context=SchemaContext(),
            db=None,  # ty: ignore[invalid-argument-type]  - validated branch never calls db
            catalog=catalog,
        )

    monkeypatch.setattr(pipeline_module, "make_deps", _fake_make_deps)
    monkeypatch.setattr(config_module.get_settings(), "chat_log_dir", str(tmp_path))
    temp_store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(pipeline_module, "get_store", lambda: temp_store)

    sid = temp_store.create(title="bad sql").id
    events = asyncio.run(_collect(run_turn(sid, "bad question")))

    # The pipeline emitted a composed not-answerable answer; the SQL
    # never reached the warehouse.
    finished = next(e for e in events if isinstance(e, AnswerFinished))
    # `compose_not_answerable` echoes the note into the answer.
    note = "; ".join(report.errors)
    assert note in finished.answer or "phantom_table" in finished.answer, (
        f"expected the validation error in the answer, got: {finished.answer!r}"
    )
    # No error event -- this is a graceful not-answerable, not a runtime error.
    assert not any(e.event == "error" for e in events)


# --- 3. catalog=None: degrade WITHOUT touching the warehouse -----------


def test_governed_catalog_none_degrades(monkeypatch, tmp_path):
    """When ``AgentDeps.catalog`` is ``None`` (catalog failed to load),
    the governed branch short-circuits to a not-answerable response.
    No DB call is made.
    """
    from chat_server import config as config_module
    from chat_server import pipeline as pipeline_module

    # Sanity: compose_not_answerable is the function the branch calls
    # in the catalog-None path.
    note = "The semantic catalog is not loaded, so I can't run governed queries yet."
    composed = compose_not_answerable(note)
    assert composed.not_answerable is True
    assert composed.answer == note

    tm, agent = _make_test_agent(
        {
            "answer_mode": "execute_sql",
            "sql": "SELECT player_id FROM mart_player_career LIMIT 1",
            "result_contract": {
                "grain": "one row per player",
                "answer_style": "prose",
            },
        }
    )
    monkeypatch.setattr(pipeline_module, "get_agent", lambda: agent)

    class _ExplodingDB:
        def execute(self, *args, **kwargs):
            raise AssertionError("db.execute must NOT be called when catalog is None")

    monkeypatch.setattr(pipeline_module, "get_db", lambda: _ExplodingDB())

    async def _fake_make_deps() -> AgentDeps:
        return _live_deps_no_catalog()

    monkeypatch.setattr(pipeline_module, "make_deps", _fake_make_deps)
    monkeypatch.setattr(config_module.get_settings(), "chat_log_dir", str(tmp_path))
    temp_store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(pipeline_module, "get_store", lambda: temp_store)

    sid = temp_store.create(title="no catalog").id
    events = asyncio.run(_collect(run_turn(sid, "some question")))

    finished = next(e for e in events if isinstance(e, AnswerFinished))
    assert "catalog" in finished.answer.lower(), finished.answer
    assert not any(e.event == "error" for e in events)


# --- 4. regression: legacy template path still hits the unchanged else --


@skip_no_db
def test_legacy_template_path_still_works(monkeypatch, tmp_path):
    """A legacy ``{template_id, params}`` plan still reaches the
    unchanged ``else`` branch (no governed sentinel, the real
    template_id appears on ``IntentClassified``).

    Mirrors ``test_pipeline_unknown_template_not_answerable`` as the
    regression baseline -- if Stage 3.3b accidentally reordered or
    renumbered the branches, this test flips red.
    """
    from chat_server import config as config_module
    from chat_server import pipeline as pipeline_module

    tm, agent = _make_test_agent(
        {
            "template_id": "season_thresholds.fifty_forty_ninety",
            "params": {"min_ppg": 25.0},
        }
    )
    monkeypatch.setattr(pipeline_module, "get_agent", lambda: agent)

    async def _fake_make_deps() -> AgentDeps:
        return _live_deps_with_catalog()

    monkeypatch.setattr(pipeline_module, "make_deps", _fake_make_deps)
    monkeypatch.setattr(config_module.get_settings(), "chat_log_dir", str(tmp_path))
    temp_store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(pipeline_module, "get_store", lambda: temp_store)

    sid = temp_store.create(title="legacy").id
    events = asyncio.run(_collect(run_turn(sid, "50-40-90 with 25 ppg")))

    # Legacy path: the real template_id, NOT a sentinel.
    intent = next(e for e in events if isinstance(e, IntentClassified))
    assert intent.template_id == "season_thresholds.fifty_forty_ninety"
    # QueryStarted likewise carries the real template_id.
    qs = next(e for e in events if isinstance(e, QueryStarted))
    assert qs.template_id == "season_thresholds.fifty_forty_ninety"
    # No governed sentinel leaked into the answer.
    leaks = [
        e.template_id
        for e in events
        if hasattr(e, "template_id")
        and isinstance(e.template_id, str)
        and e.template_id.startswith("semantic:")
    ]
    assert not leaks, f"governed sentinel leaked into legacy events: {leaks}"


# --- direct composer wiring check (no DB / no agent) -------------------
# (Kept empty: the composer dispatch matrix is exhaustively covered in
# `test_compose_governed.py`; the four tests above transitively verify
# that `pipeline.run_turn` invokes `compose_governed` with the right
# shape via the composed-answer assertions.)
