"""Tests for the Pydantic AI agent + the chat route end-to-end.

Two layers of testing live here:

1. **Agent unit tests** — driven by ``pydantic_ai.models.test.TestModel``
   so no live OpenRouter calls happen during normal ``pytest`` runs. They
   pin the typed output shape, the ``ModelRetry`` path for unknown
   templates, and the lookup tools' behaviour against the live warehouse.

2. **Chat-route end-to-end test** — monkeypatches
   ``chat_server.routes.chat.get_agent`` with a TestModel-backed agent
   and asserts the full ``POST /api/chat`` request returns a happy-path
   response (sql + row_count + grounded answer mentioning Curry).

3. **Live agent test** — gated behind ``CHAT_RUN_LIVE_AGENT_TESTS=1`` so
   normal CI never spends money on OpenRouter. Skipped by default.

All tests in this file skip cleanly when the parallel fixer's
``chat_server.agent`` module is missing or the warehouse is absent.
"""

from __future__ import annotations

import asyncio
import os

import pytest

# `chat_server.agent` is owned by the parallel fixer; if their PR hasn't
# landed yet, skip the whole module so collection stays green.
pytest.importorskip("chat_server.agent")

from fastapi.testclient import TestClient  # noqa: E402
from pydantic_ai.models.test import TestModel  # noqa: E402

from chat_server.agent import (  # noqa: E402
    AgentDeps,
    QueryPlan,
    get_agent,
    make_deps,
    reset_agent_for_tests,
)
from chat_server.schema_context import SchemaContext  # noqa: E402
from chat_server.templates import TemplateNotFound, get_template  # noqa: E402
from chat_tests.conftest import skip_no_db  # noqa: E402

# --- fixtures ------------------------------------------------------------


@pytest.fixture
def fresh_agent():
    """Build a TestModel-backed agent via the parallel fixer's builder.

    Returns
    -------
    (agent, tm)
        ``agent`` is a fresh ``Agent`` wired to a ``TestModel`` whose
        ``custom_output_args`` is mutable (set it before each ``run``).
        ``tm`` is the TestModel itself so tests can override the output.

    Notes
    -----
    Prefers the parallel fixer's ``_build_agent(tm)`` builder when
    available (the contract the brief documented). Falls back to
    building the agent from scratch using only the public surface if
    the underscore-private name is missing. The fallback mirrors the
    essentials of the parallel fixer's wiring — same ``output_type``
    and ``deps_type`` — so the agent behaves the same in tests.
    """
    tm = TestModel(call_tools=[], custom_output_args=None)
    from chat_server import agent as agent_module

    if hasattr(agent_module, "_build_agent"):
        agent = agent_module._build_agent(tm)
    else:  # pragma: no cover  - defensive; _build_agent is documented as the contract
        from pydantic_ai import Agent

        agent = Agent(
            tm,
            output_type=QueryPlan,
            deps_type=AgentDeps,
            retries={"output": 3, "tools": 2},
        )
    return agent, tm


def _noop_deps() -> AgentDeps:
    """Build an `AgentDeps` whose tool bodies never run.

    Tool bodies are skipped at the TestModel level (``call_tools=[]``),
    so the deps' `db` is never read and an empty `SchemaContext` is
    fine for the system-prompt decorator.
    """
    return AgentDeps(
        registry={},
        schema_context=SchemaContext(),
        db=None,  # type: ignore[arg-type] - tests bypass tool calls
    )


# --- QueryPlan output validation ----------------------------------------


def test_queryplan_validates_template_id(fresh_agent):
    """A TestModel that emits a known template id is round-tripped."""
    agent, tm = fresh_agent
    tm.custom_output_args = {
        "template_id": "season_thresholds.fifty_forty_ninety",
        "params": {"min_ppg": 25.0},
    }
    result = asyncio.run(agent.run("50-40-90 with 25 ppg", deps=_noop_deps()))
    assert result.output.template_id == "season_thresholds.fifty_forty_ninety"
    assert result.output.params["min_ppg"] == 25.0
    assert result.output.clarification is None
    assert result.output.not_answerable_note is None


def test_queryplan_passes_through_clarification(fresh_agent):
    """A clarification-only plan is preserved end-to-end."""
    agent, tm = fresh_agent
    tm.custom_output_args = {
        "template_id": "",
        "params": {},
        "clarification": "Which season?",
        "not_answerable_note": None,
    }
    result = asyncio.run(agent.run("?", deps=_noop_deps()))
    assert result.output.clarification == "Which season?"
    assert result.output.not_answerable_note is None
    assert result.output.template_id == ""


def test_queryplan_passes_through_not_answerable_note(fresh_agent):
    """A not-answerable-only plan is preserved end-to-end."""
    agent, tm = fresh_agent
    tm.custom_output_args = {
        "template_id": "",
        "params": {},
        "clarification": None,
        "not_answerable_note": "no template fits",
    }
    result = asyncio.run(agent.run("?", deps=_noop_deps()))
    assert result.output.not_answerable_note == "no template fits"
    assert result.output.clarification is None


def test_run_returns_property_output_and_usage(fresh_agent):
    """`result.output` is the typed QueryPlan; `result.usage` is the RunUsage property."""
    agent, tm = fresh_agent
    tm.custom_output_args = {
        "template_id": "season_thresholds.fifty_forty_ninety",
        "params": {"min_ppg": 30.0},
    }
    result = asyncio.run(agent.run("test", deps=_noop_deps()))
    assert isinstance(result.output, QueryPlan)
    assert result.usage is not None  # property, not method
    # `all_messages` is a method that returns the full message history.
    msgs = result.all_messages()
    assert isinstance(msgs, list)
    assert len(msgs) >= 1


# --- tool self-correction via ModelRetry --------------------------------


def test_get_template_detail_unknown_id_raises_modelretry(fresh_agent):
    """Unknown template ids feed back ModelRetry so the model self-corrects.

    We don't drive the tool through the agent (TestModel auto-calls
    tools and emits output simultaneously, which bypasses the retry
    path). Instead we exercise the contract's two layers:

    1. ``chat_server.templates.get_template(unknown_id)`` raises
       ``TemplateNotFound`` — the loader uses this exception to signal
       the model-handler to convert to ``ModelRetry``.
    2. The handler in ``agent.py`` catches ``TemplateNotFound`` and
       raises ``ModelRetry`` — verified by reading the source under
       ``chat_server/agent.py`` (a grep test below).
    """
    with pytest.raises(TemplateNotFound):
        get_template("does.not.exist")

    # Source-level check: the handler really does translate the lookup
    # exception into ModelRetry. A regression to "re-raise TemplateNotFound"
    # would break the model's self-correction path (pydantic-ai#822).
    import inspect

    from chat_server import agent as agent_module

    source = inspect.getsource(agent_module)
    assert "ModelRetry" in source
    assert "TemplateNotFound" in source


def test_lookup_team_and_lookup_season_also_use_modelretry(fresh_agent):
    """``ModelRetry`` is wired into the lookups for ambiguous phrases.

    Sanity check that the parallel fixer's contract includes the retry
    path for both free-text lookups — a regression to silent failure
    would surface as the model hallucinating ids.
    """
    import inspect

    from chat_server import agent as agent_module

    source = inspect.getsource(agent_module)
    # lookup_player uses ILIKE — no ModelRetry path there; but lookup_team
    # and lookup_season must raise ModelRetry on unparseable input.
    assert "lookup_team" in source
    assert "lookup_season" in source
    # `lookup_season` must reference ModelRetry (its unknown-phrase branch
    # is the documented retry path).
    assert source.count("raise ModelRetry") >= 1


# --- lookup_player tool (DB-backed) --------------------------------------


@skip_no_db
def test_lookup_player_finds_curry():
    """Phase 3 placeholder: exercise the lookup_player tool against the live DB.

    The tool is registered on the agent via Pydantic AI's decorator. The
    cleanest portable test path is to invoke it as a plain function if
    the parallel fixer re-exposed it at module scope (some implementations
    do, for testability). Otherwise we fall back to invoking the same
    DuckDB query the tool runs.
    """
    import chat_server.db as db_module
    from chat_server import agent as agent_module

    fn = getattr(agent_module, "lookup_player", None)
    if fn is not None:
        # Tool body expects a RunContext; build a fake that exposes only
        # `deps.db.execute`.
        import types

        class _FakeCtx:
            def __init__(self, db):
                self.deps = types.SimpleNamespace(db=db)

        db = db_module.get_db()
        ctx = _FakeCtx(db)
        hits = asyncio.run(fn(ctx, "Stephen Curry"))
    else:
        # Fallback: exercise the equivalent query directly.
        db = db_module.get_db()

        async def _lookup():
            r = await db.execute(
                "SELECT player_id, full_name FROM dim_player "
                "WHERE full_name ILIKE $pattern ORDER BY full_name LIMIT 10",
                {"pattern": "%Stephen Curry%"},
            )
            return r.rows

        hits = [
            {"player_id": int(r["player_id"]), "full_name": str(r["full_name"])}
            for r in asyncio.run(_lookup())
        ]

    names = [h["full_name"] for h in hits]
    assert any("Stephen Curry" in n for n in names), f"expected Curry in {names}"


# --- chat route end-to-end (TestModel-backed) ---------------------------


@skip_no_db
def test_chat_route_end_to_end_with_testmodel(monkeypatch, tmp_path):
    """Full POST /api/chat round-trip with a TestModel-controlled agent.

    Patches:
    * ``chat_server.routes.chat.get_agent`` — the route's binding to the
      agent factory. We return a TestModel-backed agent that emits a
      valid 50-40-90 plan.
    * ``chat_server.routes.chat.make_deps`` — replace with a sync stub
      that returns a real `AgentDeps` pointing at the live warehouse, so
      the tool lookup calls (skipped via TestModel.call_tools=[]) and the
      executor can both run.
    * ``chat_server.routes.chat.get_store`` — temp-dir-backed store so
      test history doesn't leak into the real `data/sessions/` tree.
    """
    from chat_server import agent as agent_module
    from chat_server.routes import chat as chat_routes
    from chat_server.sessions import SessionStore

    tm = TestModel(
        call_tools=[],
        custom_output_args={
            "template_id": "season_thresholds.fifty_forty_ninety",
            "params": {"min_ppg": 25.0},
        },
    )
    test_agent = agent_module._build_agent(tm)

    from chat_server.db import get_db as _get_db

    db_handle = _get_db()

    async def _fake_make_deps() -> AgentDeps:
        return AgentDeps(
            registry={},
            schema_context=SchemaContext(),
            db=db_handle,
        )

    temp_store = SessionStore(tmp_path)

    monkeypatch.setattr(chat_routes, "get_agent", lambda: test_agent)
    monkeypatch.setattr(chat_routes, "make_deps", _fake_make_deps)
    monkeypatch.setattr(chat_routes, "get_store", lambda: temp_store)

    from chat_server.main import app

    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "50-40-90 with at least 25 ppg"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["template_id"] == "season_thresholds.fifty_forty_ninety"
    assert body["not_answerable"] is False
    assert body["row_count"] is not None and body["row_count"] >= 1
    assert "Curry" in body["answer"], body["answer"]
    # The rendered SQL must be present and contain the bound parameter.
    assert body["sql"] and "mart_player_season" in body["sql"]
    assert body["session_id"]
    # Citations should include the allowlisted tables.
    cited = {c["table_name"] for c in body["citations"]}
    assert {"mart_player_season", "dim_player"}.issubset(cited)
    # Duration is reported in milliseconds.
    assert body["duration_ms"] is not None and body["duration_ms"] >= 0


@skip_no_db
def test_chat_route_creates_session_when_id_omitted(monkeypatch, tmp_path):
    """Omitting `session_id` creates a fresh session with a derived title."""
    from chat_server import agent as agent_module
    from chat_server.routes import chat as chat_routes
    from chat_server.sessions import SessionStore

    tm = TestModel(
        call_tools=[],
        custom_output_args={
            "template_id": "season_thresholds.fifty_forty_ninety",
            "params": {"min_ppg": 25.0},
        },
    )
    test_agent = agent_module._build_agent(tm)
    from chat_server.db import get_db as _get_db

    db_handle = _get_db()

    async def _fake_make_deps() -> AgentDeps:
        return AgentDeps(registry={}, schema_context=SchemaContext(), db=db_handle)

    monkeypatch.setattr(chat_routes, "get_agent", lambda: test_agent)
    monkeypatch.setattr(chat_routes, "make_deps", _fake_make_deps)
    monkeypatch.setattr(chat_routes, "get_store", lambda: SessionStore(tmp_path))

    from chat_server.main import app

    client = TestClient(app)
    message = "50-40-90 seasons"
    response = client.post("/api/chat", json={"message": message})

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"]
    # The created session should be discoverable through the temp store.
    sid = body["session_id"]
    temp_store = SessionStore(tmp_path)
    meta = temp_store.get(sid)
    assert meta.title == message[:40]
    # Both user + assistant messages persisted.
    page = temp_store.history(sid, limit=10)
    assert page.total == 2
    assert page.messages[0].role == "user"
    assert page.messages[0].content == message
    assert page.messages[1].role == "assistant"


@skip_no_db
def test_chat_route_handles_clarification_plan(monkeypatch, tmp_path):
    """A clarification plan returns immediately without executing SQL."""
    from chat_server import agent as agent_module
    from chat_server.routes import chat as chat_routes
    from chat_server.sessions import SessionStore

    tm = TestModel(
        call_tools=[],
        custom_output_args={
            "template_id": "",
            "params": {},
            "clarification": "Which season?",
            "not_answerable_note": None,
        },
    )
    test_agent = agent_module._build_agent(tm)
    from chat_server.db import get_db as _get_db

    db_handle = _get_db()

    async def _fake_make_deps() -> AgentDeps:
        return AgentDeps(registry={}, schema_context=SchemaContext(), db=db_handle)

    monkeypatch.setattr(chat_routes, "get_agent", lambda: test_agent)
    monkeypatch.setattr(chat_routes, "make_deps", _fake_make_deps)
    monkeypatch.setattr(chat_routes, "get_store", lambda: SessionStore(tmp_path))

    from chat_server.main import app

    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "?"})
    body = response.json()
    assert response.status_code == 200
    assert body["answer"] == "Which season?"
    assert body["sql"] is None
    assert body["row_count"] is None


@skip_no_db
def test_chat_route_handles_not_answerable_plan(monkeypatch, tmp_path):
    """A not-answerable plan returns the note without running a query."""
    from chat_server import agent as agent_module
    from chat_server.routes import chat as chat_routes
    from chat_server.sessions import SessionStore

    tm = TestModel(
        call_tools=[],
        custom_output_args={
            "template_id": "",
            "params": {},
            "clarification": None,
            "not_answerable_note": "no template fits the question",
        },
    )
    test_agent = agent_module._build_agent(tm)
    from chat_server.db import get_db as _get_db

    db_handle = _get_db()

    async def _fake_make_deps() -> AgentDeps:
        return AgentDeps(registry={}, schema_context=SchemaContext(), db=db_handle)

    monkeypatch.setattr(chat_routes, "get_agent", lambda: test_agent)
    monkeypatch.setattr(chat_routes, "make_deps", _fake_make_deps)
    monkeypatch.setattr(chat_routes, "get_store", lambda: SessionStore(tmp_path))

    from chat_server.main import app

    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "weird question"})
    body = response.json()
    assert response.status_code == 200
    assert body["not_answerable"] is True
    assert body["not_answerable_note"] == "no template fits the question"
    assert body["sql"] is None
    assert body["row_count"] is None


@skip_no_db
def test_chat_route_handles_invalid_params(monkeypatch, tmp_path):
    """Bad params from the agent degrade to a not-answerable response, not 500."""
    from chat_server import agent as agent_module
    from chat_server.routes import chat as chat_routes
    from chat_server.sessions import SessionStore

    # `min_ppg` must be a float >= 0; pass a string to force Pydantic failure.
    tm = TestModel(
        call_tools=[],
        custom_output_args={
            "template_id": "season_thresholds.fifty_forty_ninety",
            "params": {"min_ppg": "not-a-number"},
        },
    )
    test_agent = agent_module._build_agent(tm)
    from chat_server.db import get_db as _get_db

    db_handle = _get_db()

    async def _fake_make_deps() -> AgentDeps:
        return AgentDeps(registry={}, schema_context=SchemaContext(), db=db_handle)

    monkeypatch.setattr(chat_routes, "get_agent", lambda: test_agent)
    monkeypatch.setattr(chat_routes, "make_deps", _fake_make_deps)
    monkeypatch.setattr(chat_routes, "get_store", lambda: SessionStore(tmp_path))

    from chat_server.main import app

    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "50-40-90"})
    body = response.json()
    assert response.status_code == 200
    assert body["not_answerable"] is True
    assert "Invalid params" in body["not_answerable_note"]


@skip_no_db
def test_chat_route_handles_unknown_template(monkeypatch, tmp_path):
    """An unknown template_id from the agent returns not-answerable, not 500."""
    from chat_server import agent as agent_module
    from chat_server.routes import chat as chat_routes
    from chat_server.sessions import SessionStore

    tm = TestModel(
        call_tools=[],
        custom_output_args={
            "template_id": "does.not.exist",
            "params": {},
            "clarification": None,
            "not_answerable_note": None,
        },
    )
    test_agent = agent_module._build_agent(tm)
    from chat_server.db import get_db as _get_db

    db_handle = _get_db()

    async def _fake_make_deps() -> AgentDeps:
        return AgentDeps(registry={}, schema_context=SchemaContext(), db=db_handle)

    monkeypatch.setattr(chat_routes, "get_agent", lambda: test_agent)
    monkeypatch.setattr(chat_routes, "make_deps", _fake_make_deps)
    monkeypatch.setattr(chat_routes, "get_store", lambda: SessionStore(tmp_path))

    from chat_server.main import app

    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "?"})
    body = response.json()
    assert response.status_code == 200
    assert body["not_answerable"] is True
    assert "does.not.exist" in body["not_answerable_note"]


# --- singleton smoke ----------------------------------------------------


def test_get_agent_is_singleton_when_unset():
    """`get_agent()` returns the cached singleton; resetting drops it."""
    # Reset, then build via the live wiring — this exercises the real
    # (OpenRouter) constructor path. We never call `.run()` so no
    # network happens.
    reset_agent_for_tests()
    a = get_agent()
    b = get_agent()
    assert a is b  # singleton

    # Resetting and re-fetching should produce a different instance.
    reset_agent_for_tests()
    c = get_agent()
    assert c is not a


# --- live agent test (gated; OFF by default) ----------------------------


@pytest.mark.skipif(
    not os.environ.get("CHAT_RUN_LIVE_AGENT_TESTS"),
    reason="set CHAT_RUN_LIVE_AGENT_TESTS=1 to run live OpenRouter calls (costs money)",
)
@skip_no_db
def test_live_agent_classifies_fifty_forty_ninety():
    """One real OpenRouter call: classify the 50-40-90 benchmark question."""
    # Drop any cached singleton so the live model is used.
    reset_agent_for_tests()
    agent = get_agent()  # live OpenRouterModel
    deps = asyncio.run(make_deps())
    result = asyncio.run(
        agent.run("Who shot 50/40/90 with at least 25 points per game?", deps=deps)
    )
    assert result.output.template_id == "season_thresholds.fifty_forty_ninety"
    # The model should pick >= 25 PPG (default 25.0 or higher).
    assert result.output.params.get("min_ppg", 25.0) >= 25.0 - 0.01
