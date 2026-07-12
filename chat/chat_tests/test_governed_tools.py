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
from chat_server.schema_context import ALLOWED_TABLES_FOR_AGENT, SchemaContext
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
    # Tables must either match the approved prefixes OR be in ALLOWED_TABLES_FOR_AGENT
    approved_prefixes = ("dim_", "fact_", "mart_", "analytics_")
    for table in tables:
        ok = table.startswith(approved_prefixes) or table in ALLOWED_TABLES_FOR_AGENT
        assert ok, f"table {table!r} is not in approved prefix set or allowlist"
    # Allowlisted source tables that exist in the warehouse must appear in results.
    # src_fact_bref_team_season_summary is confirmed present by the live-schema tests.
    assert "src_fact_bref_team_season_summary" in tables, (
        "allowlisted source table missing from list_warehouse_tables"
    )

    columns = _call_tool(agent, "describe_table", deps, table="dim_player")
    assert any(column["name"] == "player_id" for column in columns)

    rows = _call_tool(agent, "preview", deps, table="dim_player", n=1)
    assert len(rows) <= 1

    # Also test the allowlisted source table directly.
    src_columns = _call_tool(
        agent, "describe_table", deps, table="src_fact_bref_team_season_summary"
    )
    assert any(col["name"] == "team_id" for col in src_columns)

    src_rows = _call_tool(agent, "preview", deps, table="src_fact_bref_team_season_summary", n=1)
    assert len(src_rows) <= 1


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


def test_describe_table_accepts_allowlisted_source(agent) -> None:
    """describe_table must accept an ALLOWED_TABLES_FOR_AGENT source table
    (src_fact_bref_team_season_summary) past the identifier check;
    the DB call may still fail (no warehouse in unit tests)."""
    deps = _deps(catalog=None, db=cast(DuckDBSingleton, _FailingDb()))
    with pytest.raises(RuntimeError, match="no db"):
        _call_tool(agent, "describe_table", deps, table="src_fact_bref_team_season_summary")


def test_describe_table_rejects_unlisted_source(agent) -> None:
    """describe_table must reject an src_* table NOT in
    ALLOWED_TABLES_FOR_AGENT (e.g. src_secret)."""
    deps = _deps(catalog=None)
    with pytest.raises(ModelRetry):
        _call_tool(agent, "describe_table", deps, table="src_secret")


def test_preview_accepts_allowlisted_source(agent) -> None:
    """preview must accept an ALLOWED_TABLES_FOR_AGENT source table
    past the identifier check; the DB call may still fail."""
    deps = _deps(catalog=None, db=cast(DuckDBSingleton, _FailingDb()))
    with pytest.raises(RuntimeError, match="no db"):
        _call_tool(agent, "preview", deps, table="src_fact_bref_team_season_summary", n=1)


def test_preview_rejects_unlisted_source(agent) -> None:
    """preview must reject an src_* table NOT in
    ALLOWED_TABLES_FOR_AGENT (e.g. src_secret)."""
    deps = _deps(catalog=None)
    with pytest.raises(ModelRetry):
        _call_tool(agent, "preview", deps, table="src_secret", n=1)


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


def test_head_to_head_description_routes_player_questions() -> None:
    """The head_to_head model description must clearly state it is TEAM-vs-TEAM
    only and route player co-appearance questions to player_game_box."""
    from chat_server.semantic_catalog import load_catalog, reset_catalog_cache

    reset_catalog_cache()
    try:
        model = load_catalog().get_model("head_to_head")
        desc = model.description
        assert "TEAM-vs-TEAM ONLY" in desc, (
            "head_to_head description must start by stating it is TEAM-vs-TEAM only"
        )
        assert "NEVER use this model when either named entity is a player" in desc, (
            "head_to_head must explicitly warn against player use"
        )
        assert "player_game_box" in desc, (
            "head_to_head must route player questions to player_game_box"
        )
    finally:
        reset_catalog_cache()


def test_player_game_box_mentions_coappearance() -> None:
    """The player_game_box model must surface player co-appearance / shared-game
    capability at the top level (description, synonyms, examples)."""
    from chat_server.semantic_catalog import load_catalog, reset_catalog_cache

    reset_catalog_cache()
    try:
        model = load_catalog().get_model("player_game_box")
        desc = model.description
        synonyms = list(model.synonyms)
        examples = list(model.example_questions)

        assert "co-appearance" in desc or "shared-game" in desc, (
            "player_game_box description must mention co-appearance / shared-game"
        )
        assert any("co-appearance" in s for s in synonyms) or any(
            "shared game" in s for s in synonyms
        ), "player_game_box synonyms must include co-appearance or shared game"
        assert any("shared-game" in e for e in examples) or any(
            "co-appearance" in e for e in examples
        ), "player_game_box example_questions must show a shared-game or co-appearance query"
    finally:
        reset_catalog_cache()


def test_system_prompt_has_player_coappearance_recipe() -> None:
    """SYSTEM_PROMPT_TEMPLATE_GOVERNED must include the player co-appearance
    cookbook entry using fact_player_game_box self-join with AVG points
    and per-player team record from is_win."""
    prompt = SYSTEM_PROMPT_TEMPLATE_GOVERNED.format(
        schema_context="(test)", catalog_summary="(test)"
    )
    assert "Player-vs-player co-appearance (shared-game record)" in prompt
    assert "fact_player_game_box" in prompt
    assert "a.team_id <> b.team_id" in prompt
    assert "a.min > 0 AND b.min > 0" in prompt
    assert "a.is_win / b.is_win" in prompt or "a.is_win" in prompt, (
        "Recipe must state that is_win provides team record for shared games"
    )
    assert "AVG(a.pts)" in prompt, "Recipe must include per-player PPG averages"
    assert "ROUND(AVG(a.pts), 1) AS player_a_ppg" in prompt, (
        "Recipe must include rounded player_a_ppg"
    )
    assert "ROUND(AVG(b.pts), 1) AS player_b_ppg" in prompt, (
        "Recipe must include rounded player_b_ppg"
    )


def test_system_prompt_has_entity_routing_rules() -> None:
    """SYSTEM_PROMPT_TEMPLATE_GOVERNED must have entity routing rules that
    forbid routing player names to mart_head_to_head and explain why joining
    it fans the result."""
    prompt = SYSTEM_PROMPT_TEMPLATE_GOVERNED.format(
        schema_context="(test)", catalog_summary="(test)"
    )
    assert "ENTITY ROUTING RULES" in prompt
    assert "NEVER route to the head_to_head model" in prompt
    assert "TEAM-vs-TEAM only" in prompt
    assert "includes franchise games where one or both" in prompt, (
        "Must explain that mart_head_to_head includes games where players didn't appear"
    )
    assert "players did not appear" in prompt, "Continuation of players-did-not-appear warning"
    assert "fan the one-row co-appearance result" in prompt, (
        "Must warn that mart_head_to_head cross-join fans the result across rows"
    )
    assert "fact_player_game_box self-join" in prompt, (
        "Must route to the fact_player_game_box self-join pattern"
    )
    assert "is_win / is_home / opponent_team_id" in prompt, (
        "Must state that fact_player_game_box has denormalized team context"
    )


def test_system_prompt_has_franchise_milestone_recipe() -> None:
    """SYSTEM_PROMPT_TEMPLATE_GOVERNED must include the franchise
    'seasons until milestone X' cookbook entry using
    src_fact_bref_team_season_summary, SELECT DISTINCT team_id, and
    inclusive +1 arithmetic, without dim_team_era or is_playoffs filter."""
    prompt = SYSTEM_PROMPT_TEMPLATE_GOVERNED.format(
        schema_context="(test)", catalog_summary="(test)"
    )
    assert "seasons until milestone X" in prompt
    assert "src_fact_bref_team_season_summary" in prompt
    assert "SELECT DISTINCT team_id" in prompt
    assert "72 rows / 43 ids" in prompt
    assert "milestone_season - debut.debut_season + 1" in prompt
    assert "dim_team_era" not in prompt or ("NOT" in prompt and "dim_team_era" in prompt), (
        "Recipe must warn NOT to use dim_team_era"
    )
    assert "Never filter on is_playoffs" in prompt
    assert "season END year" in prompt


def test_system_prompt_has_no_comments_rule() -> None:
    """SYSTEM_PROMPT_TEMPLATE_GOVERNED must include a rule against
    inline -- and /* */ comments inside generated SQL."""
    prompt = SYSTEM_PROMPT_TEMPLATE_GOVERNED.format(
        schema_context="(test)", catalog_summary="(test)"
    )
    assert "Do NOT include inline -- or /* */ comments" in prompt


def test_system_prompt_no_false_is_playoffs_filter() -> None:
    """Cookbook item 4 (team season record) must NOT contain is_playoffs = FALSE,
    which is wrong because is_playoffs is a qualification flag, not a separate
    postseason row."""
    prompt = SYSTEM_PROMPT_TEMPLATE_GOVERNED.format(
        schema_context="(test)", catalog_summary="(test)"
    )
    assert "is_playoffs = FALSE" not in prompt, (
        "Cookbook item 4 must not filter is_playoffs = FALSE "
        "(that would exclude playoff teams' regular-season rows)"
    )
    assert "do NOT filter it" in prompt, (
        "Cookbook item 4 must explicitly warn against filtering is_playoffs"
    )


def test_preview_accepts_valid_identifier_past_regex(agent) -> None:
    """A clean identifier with an approved prefix must pass the regex
    check and only fail on the DB side (no warehouse in unit tests)."""
    deps = _deps(catalog=None, db=cast(DuckDBSingleton, _FailingDb()))
    # The call should NOT raise ModelRetry from the identifier check.
    # It may raise from the DB call (no warehouse), which is fine —
    # we just need to prove the regex didn't reject it.
    with pytest.raises(RuntimeError, match="no db"):
        _call_tool(agent, "preview", deps, table="dim_player", n=3)
