"""Tests for the Phase 3.7 governed-SQL cutover: the catalog tools
(``list_models`` / ``get_model_detail``), the governed system prompt, and the
``CHAT_GOVERNED_SQL_MODE`` flag-driven prompt branching.

The catalog tools are registered as closures on the agent (mirroring
``list_templates`` / ``get_template_detail``). We exercise them through the
agent's function-toolset ``call_tool`` path with a lightweight fake
``RunContext``, and test the prompt branching by invoking the extracted
``SystemPromptRunner.function`` directly.
"""

from __future__ import annotations

import pytest

from chat_server.agent import (
    SYSTEM_PROMPT_TEMPLATE,
    SYSTEM_PROMPT_TEMPLATE_GOVERNED,
    _catalog_summary,
    get_agent,
    reset_agent_for_tests,
)
from chat_server.config import get_settings, reset_settings_cache
from chat_server.semantic_catalog.loader import SemanticCatalog
from chat_server.semantic_catalog.schema import (
    BaseTable,
    BusinessModel,
    Dimension,
    Measure,
)

# -- fixtures --------------------------------------------------------------


def _make_catalog() -> SemanticCatalog:
    """A minimal two-model catalog for tool/prompt tests (no DB needed)."""
    m1 = BusinessModel(
        model="player_season",
        description="Per-player per-season aggregates.",
        grain="one row per player per season",
        base_table=BaseTable(name="mart_player_season", alias="ps"),
        dimensions=[
            Dimension(name="player_id", expr="ps.player_id", description="Player key"),
            Dimension(name="season", expr="ps.season_year", description="Season label"),
        ],
        measures=[
            Measure(
                name="ppg",
                expr="ps.avg_pts",
                description="Points per game",
                additivity="non_additive",
            ),
        ],
        synonyms=["season stats"],
        example_questions=["Who led the league in PPG?"],
    )
    m2 = BusinessModel(
        model="team_season",
        description="Per-team per-season summary.",
        grain="one row per team per season",
        base_table=BaseTable(name="src_fact_bref_team_season_summary", alias="ts"),
    )
    return SemanticCatalog({"player_season": m1, "team_season": m2})


class _FakeSchemaContext:
    """Minimal stand-in exposing ``as_prompt_text``."""

    def as_prompt_text(self) -> str:
        return "(test schema context)"


class _FakeDeps:
    def __init__(self, catalog):
        self.catalog = catalog
        self.schema_context = _FakeSchemaContext()
        self.registry = {}
        self.db = None


class _FakeCtx:
    """Stand-in for ``RunContext`` — only ``.deps`` is read by the tools."""

    def __init__(self, deps):
        self.deps = deps


@pytest.fixture
def fresh_agent():
    reset_agent_for_tests()
    from pydantic_ai.models.test import TestModel

    return get_agent(TestModel())


@pytest.fixture(autouse=True)
def _reset_settings():
    """Ensure each test starts with a clean settings cache."""
    reset_settings_cache()
    yield
    reset_settings_cache()


def _set_governed(monkeypatch, value: bool) -> None:
    """Toggle ``governed_sql_mode`` via env + cache reset."""
    monkeypatch.setenv("CHAT_GOVERNED_SQL_MODE", "1" if value else "0")
    reset_settings_cache()


def _system_prompt_fn(agent):
    """Extract the single ``SystemPromptRunner.function`` from the agent."""
    return agent._system_prompt_functions[0].function


# -- _catalog_summary ------------------------------------------------------


def test_catalog_summary_renders_every_model():
    catalog = _make_catalog()
    summary = _catalog_summary(catalog)
    assert "- player_season" in summary
    assert "- team_season" in summary
    assert "mart_player_season" in summary
    assert "src_fact_bref_team_season_summary" in summary
    # Deterministic ordering (sorted by name).
    assert summary.index("player_season") < summary.index("team_season")


def test_catalog_summary_includes_grain():
    catalog = _make_catalog()
    summary = _catalog_summary(catalog)
    assert "grain: one row per player per season" in summary


# -- governed system prompt formatting -------------------------------------


def test_governed_prompt_template_formats_without_error():
    """The governed prompt has two placeholders and must format cleanly."""
    rendered = SYSTEM_PROMPT_TEMPLATE_GOVERNED.format(
        schema_context="(test)",
        catalog_summary="- player_season — desc (grain: g; base_table: t as a)",
    )
    assert "governed SQL" in rendered.lower() or "SELECT" in rendered
    assert "(test)" in rendered
    # The legacy prompt must NOT contain the governed rules.
    assert "SELECT only" not in SYSTEM_PROMPT_TEMPLATE


# -- prompt branching ------------------------------------------------------


def test_prompt_branch_legacy_when_flag_off(monkeypatch, fresh_agent):
    """Flag off → legacy template-only prompt (no governed-SQL rules)."""
    _set_governed(monkeypatch, False)
    fn = _system_prompt_fn(fresh_agent)
    deps = _FakeDeps(catalog=_make_catalog())
    prompt = fn(_FakeCtx(deps))
    assert "NEVER write SQL" in prompt
    assert "SELECT only" not in prompt


def test_prompt_branch_governed_when_flag_on_and_catalog_present(monkeypatch, fresh_agent):
    """Flag on + catalog loaded → governed prompt with catalog summary."""
    _set_governed(monkeypatch, True)
    fn = _system_prompt_fn(fresh_agent)
    deps = _FakeDeps(catalog=_make_catalog())
    prompt = fn(_FakeCtx(deps))
    assert "SELECT only" in prompt
    assert "list_models" in prompt
    assert "get_model_detail" in prompt
    assert "- player_season" in prompt
    assert "(test schema context)" in prompt


def test_prompt_branch_falls_back_when_catalog_none(monkeypatch, fresh_agent):
    """Flag on BUT catalog None → legacy prompt (governed SQL unavailable)."""
    _set_governed(monkeypatch, True)
    fn = _system_prompt_fn(fresh_agent)
    deps = _FakeDeps(catalog=None)
    prompt = fn(_FakeCtx(deps))
    assert "NEVER write SQL" in prompt
    assert "SELECT only" not in prompt


# -- settings flag ---------------------------------------------------------


def test_governed_sql_mode_reads_env(monkeypatch):
    monkeypatch.setenv("CHAT_GOVERNED_SQL_MODE", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setenv("DUCKDB_PATH", "test")
    reset_settings_cache()
    assert get_settings().chat_governed_sql_mode is True


def test_governed_sql_mode_defaults_off(monkeypatch):
    monkeypatch.delenv("CHAT_GOVERNED_SQL_MODE", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setenv("DUCKDB_PATH", "test")
    reset_settings_cache()
    assert get_settings().chat_governed_sql_mode is False


# -- catalog tools (via agent function-toolset) ----------------------------


def _call_tool(agent, name: str, ctx, **kwargs):
    """Invoke a registered tool function by name via the toolset.

    Returns the tool's return value. Raises ``KeyError`` if the tool is
    not registered.
    """
    import asyncio

    coro = agent._function_toolset.call_tool(name, ctx, kwargs)
    return asyncio.get_event_loop().run_until_complete(coro) if asyncio.iscoroutine(coro) else coro


def test_list_models_tool_returns_catalog(fresh_agent):
    """``list_models`` returns one dict per model when the catalog is loaded."""
    deps = _FakeDeps(catalog=_make_catalog())
    ctx = _FakeCtx(deps)
    # call_tool needs a richer ctx; invoke the underlying function directly
    # via the toolset's registered handlers instead.
    result = _invoke_tool_fn(fresh_agent, "list_models", ctx)
    assert isinstance(result, list)
    assert len(result) == 2
    names = {entry["model"] for entry in result}
    assert names == {"player_season", "team_season"}
    sample = next(e for e in result if e["model"] == "player_season")
    assert sample["base_table"] == "mart_player_season"
    assert "grain" in sample


def test_list_models_tool_empty_when_catalog_none(fresh_agent):
    deps = _FakeDeps(catalog=None)
    ctx = _FakeCtx(deps)
    result = _invoke_tool_fn(fresh_agent, "list_models", ctx)
    assert result == []


def test_get_model_detail_tool_returns_full_detail(fresh_agent):
    deps = _FakeDeps(catalog=_make_catalog())
    ctx = _FakeCtx(deps)
    detail = _invoke_tool_fn(fresh_agent, "get_model_detail", ctx, model="player_season")
    assert detail["model"] == "player_season"
    assert detail["base_table"] == {"name": "mart_player_season", "alias": "ps"}
    assert len(detail["dimensions"]) == 2
    assert detail["dimensions"][0]["name"] == "player_id"
    assert len(detail["measures"]) == 1
    assert detail["measures"][0]["additivity"] == "non_additive"


def test_get_model_detail_tool_unknown_raises_modelretry(fresh_agent):
    from pydantic_ai.exceptions import ModelRetry

    deps = _FakeDeps(catalog=_make_catalog())
    ctx = _FakeCtx(deps)
    with pytest.raises(ModelRetry):
        _invoke_tool_fn(fresh_agent, "get_model_detail", ctx, model="does_not_exist")


def test_get_model_detail_tool_no_catalog_raises_modelretry(fresh_agent):
    from pydantic_ai.exceptions import ModelRetry

    deps = _FakeDeps(catalog=None)
    ctx = _FakeCtx(deps)
    with pytest.raises(ModelRetry):
        _invoke_tool_fn(fresh_agent, "get_model_detail", ctx, model="player_season")


# -- tool invocation helper ------------------------------------------------


def _invoke_tool_fn(agent, tool_name: str, ctx, **kwargs):
    """Call a registered agent tool's underlying function directly.

    The tools are closures inside ``_register_tools``; pydantic-ai stores
    their wrapped functions inside the function-toolset. We dig the raw
    function out so the test can call it with a lightweight fake ctx
    (``call_tool`` requires a full ``RunContext`` which is heavy to build).
    """
    # The function-toolset builds a Tool wrapper per registered function.
    # Access the original function via the internal _tools registry, which
    # is populated lazily. Force population by reading get_tools with a
    # minimal run context.
    fs = agent._function_toolset
    # The underlying functions are held in ``_tools`` (dict: name -> Tool).
    # It's lazily populated; access via the private builder.
    if not hasattr(fs, "_tools") or not fs._tools:
        # Trigger lazy build by accessing the protected builder directly.
        # The function toolset stores functions in a FunctionToolset internal
        # dict keyed by name. Inspect the instance namespace for a dict of
        # callables.
        for attr in vars(fs):
            val = getattr(fs, attr, None)
            if isinstance(val, dict) and tool_name in val:
                tool = val[tool_name]
                fn = getattr(tool, "function", None) or getattr(tool, "fn", None) or tool
                if callable(fn):
                    return fn(ctx, **kwargs)
    # Fallback: direct dict access if populated.
    tool = fs._tools.get(tool_name) if hasattr(fs, "_tools") else None
    if tool is not None:
        fn = getattr(tool, "function", None) or getattr(tool, "fn", None) or tool
        return fn(ctx, **kwargs)
    # Last resort: the toolset may store functions in a different attribute.
    raise RuntimeError(
        f"Could not locate tool {tool_name!r} on the function toolset. "
        f"Available attrs: {[a for a in vars(fs) if not a.startswith('_abc')]}"
    )
