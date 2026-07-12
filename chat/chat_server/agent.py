"""Pydantic AI agent for the basketball chatbot.

Phase 3 exit criteria:
* Singleton `Agent` with native `OpenRouterModel`.
* Typed `Plan` output — a discriminated union on `answer_mode`
  (`ClarifyPlan | NotAnswerablePlan | SqlPlan | TemplatePlan`).
* Tools: `list_templates`, `get_template_detail`, `lookup_player`,
  `lookup_team`, `lookup_season`.
* `retries={'output': 3, 'tools': 2}` (mitigates
  pydantic-ai#822 where some OpenRouter models return plain text on
  structured output).

System-prompt strategy
----------------------
We use the **decorator form** (`@agent.system_prompt`) rather than the
static `system_prompt=[...]` constructor argument, so the schema-context
text comes from `ctx.deps.schema_context` at run time. Pros:

* `make_deps()` can inject a different `SchemaContext` (e.g. a
  test-built one) without rebuilding the agent singleton.
* The decorator body is sync and cheap — `SchemaContext` is
  materialized once via the cached `await get_schema_context()`.

Public surface (re-exported from this module):
* `Plan` and its members — the typed output union.
* `AgentDeps` — the deps dataclass (registry + schema_context + db + catalog).
* `get_agent(model=None)` — lazy singleton with optional model override
  (used by tests to inject `TestModel` and avoid live OpenRouter calls).
* `make_deps()` — async helper that builds the default `AgentDeps`.
"""

from __future__ import annotations

import contextlib
import logging
import re
import threading
from dataclasses import dataclass
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, Field, field_validator
from pydantic_ai import Agent, NativeOutput, RunContext, ToolOutput
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ToolCallPart, ToolReturnPart
from pydantic_ai.models import Model
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_core import PydanticUndefined

from .config import get_settings
from .db import DuckDBSingleton, get_db
from .schema_context import ALLOWED_TABLES_FOR_AGENT, SchemaContext, get_schema_context
from .semantic_catalog import SemanticCatalog, load_catalog

logger = logging.getLogger(__name__)


class Clarification(BaseModel):
    """A structured disambiguation question.

    ``ClarifyPlan.clarification`` coerces a bare ``str`` (the form
    smaller models sometimes emit) into this structured shape, so the
    rest of the system only ever sees ``question`` / ``options``.
    """

    question: str = Field(..., min_length=1)
    options: list[str] = Field(default_factory=list)
    model_name: str | None = Field(default=None)


class ResultContract(BaseModel):
    """What the pipeline should produce for a governed-SQL turn.

    Read by the composer to pick the answer formatter and by the DB
    layer to enforce the server-side row cap.
    """

    grain: str = Field(..., min_length=1)
    columns: list[str] = Field(default_factory=list)
    row_limit: int | None = Field(default=None, ge=1)
    answer_style: Literal["ranked_list", "single_value", "count", "prose", "table"] = "prose"


class ClarifyPlan(BaseModel):
    """The question is too ambiguous to act on; ask before querying."""

    answer_mode: Literal["clarify"] = "clarify"
    question_interpretation: str = ""
    clarification: str | Clarification = Field(
        ...,
        description="The specific disambiguation question (plus options where useful).",
    )

    @field_validator("clarification", mode="before")
    @classmethod
    def _coerce_bare_str(cls, value: object) -> object:
        """Accept a bare question string and lift it into `Clarification`."""
        if isinstance(value, str) and value.strip():
            return Clarification(question=value)
        return value


class NotAnswerablePlan(BaseModel):
    """The question cannot be answered from the warehouse; explain why."""

    answer_mode: Literal["not_answerable"] = "not_answerable"
    question_interpretation: str = ""
    not_answerable_note: str = Field(
        ...,
        min_length=1,
        description="Why the question cannot be answered, with the evidence checked.",
    )


class SqlPlan(BaseModel):
    """The governed path: validated, dry-run, and executed read-only."""

    answer_mode: Literal["execute_sql"] = "execute_sql"
    question_interpretation: str = ""
    sql: str = Field(
        ...,
        min_length=1,
        description="Generated DuckDB SELECT SQL drawn from approved warehouse tables.",
    )
    result_contract: ResultContract | None = Field(
        default=None,
        description="Expected grain/columns/row-limit/answer-style.",
    )


Plan = Annotated[
    ClarifyPlan | NotAnswerablePlan | SqlPlan,
    Field(discriminator="answer_mode"),
]
"""Typed output the agent produces on every turn.

A Pydantic discriminated union on ``answer_mode``: a plan carrying both
``sql`` and ``clarification`` is unrepresentable, and the pipeline
dispatch is an exhaustive ``match`` on the member type instead of a
documented check order.
"""


@dataclass
class AgentDeps:
    """Runtime deps carried by every agent run.

    Attributes
    ----------
    registry
        The live template registry dict (from `get_registry()`). The
        `list_templates` / `get_template_detail` tools use this rather
        than re-importing the templates package — keeps tool bodies
        testable in isolation.
    schema_context
        The compact schema context for this run. Built once at startup
        via `get_schema_context()` and reused.
    db
        The process-wide `DuckDBSingleton`. `lookup_player` /
        `lookup_team` / `lookup_season` hit the read-only warehouse.
    catalog
        The semantic catalog (Phase 3 Lane B). Populated by
        ``make_deps()`` via :func:`load_catalog`; ``None`` when the
        catalog failed to load (treated by the governed-SQL branch as
        a "catalog not loaded" / not-answerable signal). Defaults to
        ``None`` so every existing ``AgentDeps(registry=..., schema_context=...,
        db=...)`` construction in the test suite keeps working unchanged.
    """

    schema_context: SchemaContext
    db: DuckDBSingleton
    catalog: SemanticCatalog | None = None


SYSTEM_PROMPT_TEMPLATE_GOVERNED = """\
OUTPUT PROTOCOL — mandatory:
- Your final response for every turn MUST be a JSON object matching the output schema.
- Do NOT respond in prose or plain text.
- The JSON object must have an `answer_mode` field set to one of: `execute_sql`,
  `clarify`, `not_answerable`.
- For `execute_sql`: include `sql` with valid DuckDB SELECT. For `clarify`: include
  `clarification`. For `not_answerable`: include `not_answerable_note`.
- No markdown fences, no explanation outside the JSON.

ANSWER MODE RULES — strictly enforced:
- NEVER use answer_mode = "template". This mode is deprecated and disabled.
  Always choose one of: execute_sql, clarify, or not_answerable.
- PREFER execute_sql. Attempt to write SQL for any question the warehouse can answer.
  Use the catalog models and their documented dimensions/measures.
- Only use not_answerable when the question is genuinely unanswerable from the
  available catalog (e.g., the data doesn't exist at all). "I don't know the SQL"
  is NOT a valid reason for not_answerable — try the catalog tools first.
- Use clarify only for genuine ambiguity that changes the answer (e.g., "best"
  could mean multiple things). Do not clarify when the intent is clear.

You are a basketball analytics assistant with governed SQL access to an NBA
DuckDB warehouse. Prefer a curated semantic catalog model: call list_models /
get_model_detail to inspect its base table, dimensions, measures, joins, and
caveats. If it lacks coverage, use list_warehouse_tables / describe_table /
preview to inspect approved live warehouse tables, then report tables used.

Available semantic models (call get_model_detail for full schemas):
{catalog_summary}

GOVERNED SQL COOKBOOK — use these patterns:
1. Player lookup + career stats: SELECT * FROM mart_player_career pc
   WHERE pc.full_name ILIKE '%name%'.
2. Season leaderboard: SELECT ps.player_id, <metric> FROM mart_player_season ps
   WHERE ps.season_year = '<YYYY-YY>' AND ps.season_type = 'Regular'
   ORDER BY <metric> DESC LIMIT 10.
3. Player comparison: WITH baseline AS (SELECT <metric> AS metric
   FROM mart_player_season WHERE player_id = <player_id> AND season_year = '<base>')
   SELECT ps.season_year, ps.<metric> - b.metric AS delta
   FROM mart_player_season ps CROSS JOIN baseline b WHERE ps.player_id = <player_id>.
4. Team season record: SELECT ts.team, SUM(ts.w) AS wins, SUM(ts.l) AS losses
   FROM src_fact_bref_team_season_summary ts WHERE ts.season = <end_year>
   AND ts.team_id = <team_id> GROUP BY ts.team. ts.season is the season END
   year (2016 = 2015-16 season). is_playoffs is a playoff-qualification flag,
   NOT a separate postseason row — do NOT filter it to FALSE (that would
   exclude valid regular-season rows for playoff teams).
5. Career similarity: WITH target AS (SELECT career_ppg, career_rpg, career_apg
   FROM mart_player_career WHERE player_id = <player_id>)
   SELECT pc.player_id, ABS(pc.career_ppg - t.career_ppg) + ABS(pc.career_rpg - t.career_rpg)
   + ABS(pc.career_apg - t.career_apg) AS diff FROM mart_player_career pc CROSS JOIN target t
   WHERE pc.player_id <> <player_id> ORDER BY diff LIMIT 10.
6. PREFER writing SQL directly with ILIKE patterns for player/team names
   (e.g., WHERE player_name ILIKE '%Russell%'). Only use lookup_player /
   lookup_team tools if you need the exact ID and can't match by name.

 7. Player-vs-player co-appearance (shared-game record): Use two aliases of
    fact_player_game_box joined on game_id, different team_id, positive min
    on both, with player name resolution through dim_player.
    The self-join produces one row per shared game; aggregate to one row with
    per-player averages. a.is_win / b.is_win on the shared-game rows already
    provide each player's team record (wins / losses) for games both appeared.
    Example pattern (gold for conv_026 "Kobe vs Duncan record"):
    SELECT COUNT(*) AS shared_games,
      SUM(CASE WHEN a.is_win THEN 1 ELSE 0 END) AS player_a_wins,
      COUNT(*) - SUM(CASE WHEN a.is_win THEN 1 ELSE 0 END) AS player_a_losses,
      ROUND(AVG(a.pts), 1) AS player_a_ppg,
      ROUND(AVG(b.pts), 1) AS player_b_ppg
    FROM fact_player_game_box a
    JOIN dim_player p1 ON a.player_id = p1.player_id AND LOWER(p1.full_name) = 'player one'
    JOIN fact_player_game_box b ON a.game_id = b.game_id
    JOIN dim_player p2 ON b.player_id = p2.player_id AND LOWER(p2.full_name) = 'player two'
    WHERE a.team_id <> b.team_id AND a.min > 0 AND b.min > 0

 8. Franchise "seasons until milestone X" (e.g., first 30-win season):
    Resolve team IDs via lookup_team or SELECT DISTINCT team_id from dim_team
    (dim_team has 72 rows / 43 ids — multiple name variants per team — so
    always SELECT DISTINCT). Compute debut season and milestone season,
    then include `+ 1` for inclusive count.
    Pattern:
    WITH team AS (
      SELECT DISTINCT team_id FROM dim_team WHERE nickname ILIKE '%<team_nickname>%'
    ),
    debut AS (
      SELECT MIN(ts.season) AS debut_season
      FROM src_fact_bref_team_season_summary ts
      WHERE ts.team_id IN (SELECT team_id FROM team)
    ),
    milestone AS (
      SELECT MIN(ts.season) AS milestone_season
      FROM src_fact_bref_team_season_summary ts
      WHERE ts.team_id IN (SELECT team_id FROM team)
        AND ts.w >= <threshold>   -- e.g. 30 for first 30-win season
    )
    SELECT debut.debut_season, milestone.milestone_season,
           milestone.milestone_season - debut.debut_season + 1 AS seasons_to_milestone
    FROM debut CROSS JOIN milestone
    IMPORTANT: Use src_fact_bref_team_season_summary, NOT dim_team_era.
    Do NOT join through dim_team_era. Never filter on is_playoffs being
    false (is_playoffs is a playoff-qualification flag, NOT a separate
    postseason row, so filtering it out would exclude regular-season rows
    for playoff teams).
    ts.season is the season END year (2016 = 2015-16 season).

ENTITY ROUTING RULES:
- When the user names two specific players (e.g. "Kobe vs Duncan", "LeBron vs
  Curry"), NEVER route to the head_to_head model (mart_head_to_head). That
  model is TEAM-vs-TEAM only and includes franchise games where one or both
  players did not appear. Even joining mart_head_to_head just for team context
  can fan the one-row co-appearance result across multiple (team, opponent,
  season) rows and produce incorrect counts. Use the player co-appearance
  pattern (item 7) via fact_player_game_box self-join — fact_player_game_box
  contains is_win / is_home / opponent_team_id denormalized on every row, so
  team context does not require any external join.

Warehouse reference (allowlist + metrics, supplementary to the catalog):
{schema_context}

Rules for every governed SQL answer:
1. SELECT only. No INSERT / UPDATE / DELETE / CREATE / DROP / PRAGMA / ATTACH.
2. Use catalog base tables when available. Otherwise, query only approved
   live warehouse tables discovered through introspection; never invent a
   table or column name.
3. Always return answer_mode = execute_sql with a non-empty sql string AND a
   filled result_contract (grain, columns you expect, row_limit, answer_style).
   The composer uses result_contract to format the answer.
4. Prefer the catalog's declared measures (SUM/AVG per the additivity) over
   hand-rolled aggregates. Honor each measure's additivity when composing.
5. Use the catalog's declared joins (left_on/right_on) when combining models;
   never invent join keys.
6. If the question is ambiguous (e.g. which season, which player when several
   match, a pronoun with no referent), return answer_mode = clarify with a
   specific clarification question and, where useful, options.
7. If the question is out of scope after catalog and warehouse inspection, or
   it asks for non-NBA data, return answer_mode = not_answerable with a note
   explaining why and what evidence you checked.
8. The warehouse is the only source of truth. Never fabricate numbers; every
   value in your answer must come from the executed SQL's result rows.
9. ALWAYS fill question_interpretation with a concise plain-English statement of
   how you read the user's question — especially when it relies on a subjective
   or ambiguous term (e.g. "similar", "comparable", "like", "clutch", "elite").
   State the concrete criteria you chose (which stats, which thresholds, which
   time window). The user sees this before the data and can redirect on the
   next turn if your reading doesn't match their intent. Treat question_interpretation
   as a transparency contract, not boilerplate — a vague restatement of the
   question is useless; name the specific yardsticks you applied.
10. For SOFT ambiguity (a subjective term with a reasonable default, like
    "similar players"), PREFER to execute your best-guess query and surface the
    interpretation (rule 9) over asking the user to clarify first — the user can
    refine after seeing concrete results. Reserve answer_mode = clarify (rule 6)
    for HARD ambiguity (which of several players, which season, an unresolved
    pronoun) where guessing would waste a query.
11. Do NOT include inline -- or /* */ comments inside generated SQL. Raw SQL
    must contain query tokens only — no explanatory text. Inline comments can
    truncate the query, causing the gate to reject it as tableless.
"""


def _catalog_summary(catalog: SemanticCatalog) -> str:
    """Render a one-line-per-model summary for the governed system prompt.

    Each line: ``name — description (grain; base_table=...)``. Sorted by
    model name for determinism. Kept short so the prompt stays well under
    token limits even with a large catalog (the agent calls
    ``get_model_detail`` for full schemas on demand).
    """
    lines: list[str] = []
    for name in catalog.list_models():
        m = catalog.get_model(name)
        lines.append(
            f"- {name} — {m.description} (grain: {m.grain}; "
            f"base_table: {m.base_table.name} as {m.base_table.alias})"
        )
    return "\n".join(lines)


_DOCUMENTED_CATALOG_TYPE = re.compile(
    r"\b(BIGINT|HUGEINT|INTEGER|DOUBLE|VARCHAR|BOOLEAN|DATE|TIMESTAMP)\b",
    re.IGNORECASE,
)


def _catalog_field_type(name: str, expr: str, description: str, *, measure: bool) -> str:
    """Return a documented or conservative DuckDB type for catalog output."""
    documented = _DOCUMENTED_CATALOG_TYPE.search(description)
    if documented:
        return documented.group(1).upper()
    upper_expr = expr.upper()
    if "CAST(" in upper_expr or "AVG(" in upper_expr or "/" in expr:
        return "DOUBLE"
    if "COUNT(" in upper_expr:
        return "BIGINT"
    if measure:
        if any(
            token in name for token in ("pct", "rating", "pace", "margin", "average", "distance")
        ):
            return "DOUBLE"
        return "HUGEINT" if name.startswith("total_") else "BIGINT"
    if name.endswith("_id") or name in {
        "conf_rank",
        "div_rank",
        "month",
        "week",
        "series_game_number",
    }:
        return "BIGINT" if name.endswith("_id") else "INTEGER"
    if name.startswith("is_"):
        return "BOOLEAN"
    if name.endswith("_date"):
        return "DATE"
    if "datetime" in name:
        return "TIMESTAMP"
    return "VARCHAR"


def _build_model() -> OpenRouterModel:
    """Construct the live `OpenRouterModel` from settings.

    The native `OpenRouterProvider` sets the required `HTTP-Referer` and
    `X-Title` headers from `app_url` / `app_title`. Building
    the model does NOT make a network call — only `agent.run()` does.

    ``max_tokens`` is set explicitly (rather than relying on the downstream
    default of 4096) because the governed-SQL system prompt is large and
    Anthropic models will truncate tool-call JSON mid-string when the output
    budget is too tight. ``temperature=0.0`` keeps tool-call JSON
    deterministic. ``extra_body`` mirrors ``max_tokens`` as a workaround for
    Pydantic AI < 2.0 max_tokens → max_completion_tokens mapping issues
    (pydantic-ai#5186 / PR #5926).
    """
    s = get_settings()
    settings_dict: dict[str, Any] = {
        "max_tokens": s.openrouter_max_tokens,
        "temperature": 0.0,
        "extra_body": {"max_tokens": s.openrouter_max_tokens},
    }
    if s.openrouter_provider:
        settings_dict["openrouter_provider"] = {
            "order": [s.openrouter_provider],
            "allow_fallbacks": True,
        }
        settings_dict["openrouter_cache_instructions"] = True
        settings_dict["openrouter_cache_tool_definitions"] = True
    kwargs: dict[str, Any] = {
        "provider": OpenRouterProvider(
            api_key=s.openrouter_api_key,
            app_url="https://github.com/nickth3man/basketball-data-emporium",
            app_title="Basketball Data Chatbot",
        ),
        "settings": settings_dict,
    }
    return OpenRouterModel(s.openrouter_model, **kwargs)


_agent: Agent[AgentDeps, Plan] | None = None
_agent_lock = threading.Lock()


def get_agent(model: Model | None = None) -> Agent[AgentDeps, Plan]:
    """Return the lazy singleton `Agent`.

    Parameters
    ----------
    model
        Optional model override. Pass a `TestModel` (or any compatible
        `Model`) here in tests to avoid live OpenRouter calls. The
        override only takes effect on the **first** call; subsequent
        calls return the cached agent (so tests that need a fresh
        override must call `reset_agent_for_tests()` first).
    """
    global _agent
    if _agent is None:
        with _agent_lock:
            if _agent is None:
                _agent = _build_agent(model)
    return _agent


def _build_agent(
    model: Model | None = None,
) -> Agent[AgentDeps, Plan]:
    """Build a fresh agent (called by `get_agent` on first invocation).

    Live OpenRouter requests use provider-native structured output so the
    plan union is sent as a response-format schema rather than an output
    tool call. Injected test models retain the tool output transport because
    Pydantic AI's ``TestModel`` does not support native structured output.
    """
    m = model if model is not None else _build_model()
    output_type = NativeOutput(Plan) if model is None else ToolOutput(Plan, name="final_result")  # type: ignore[arg-type]
    raw: Agent[AgentDeps, Plan] = cast(
        "Agent[AgentDeps, Plan]",
        Agent(
            m,
            output_type=output_type,
            deps_type=AgentDeps,
            retries={"output": 5, "tools": 5},
            system_prompt=(),
        ),
    )
    agent = raw

    @agent.system_prompt
    def _system_prompt(ctx: RunContext[AgentDeps]) -> str:
        """Inject the schema context into the system prompt.

        Phase 3.7: branches on ``Settings.governed_sql_mode``. When the flag
        is on AND the catalog loaded, the governed-SQL prompt is used (the
        agent may write SQL against catalog models). Otherwise the legacy
        template-only prompt is used unchanged. The branch is evaluated per
        turn so flipping the env var takes effect on the next process start
        (settings are ``lru_cache``-d at module scope).
        """
        schema_text = ctx.deps.schema_context.as_prompt_text()
        catalog_summary = (
            _catalog_summary(ctx.deps.catalog) if ctx.deps.catalog is not None else "(not loaded)"
        )
        return SYSTEM_PROMPT_TEMPLATE_GOVERNED.format(
            schema_context=schema_text, catalog_summary=catalog_summary
        )

    # TestModel keeps the legacy tool surface so existing template fixtures
    # remain executable; only live governed agents hide deprecated tools.
    _register_tools(agent)
    return agent


def reset_agent_for_tests() -> None:
    """Drop the cached singleton (test helper only)."""
    global _agent
    with _agent_lock:
        _agent = None


def _summarize_params(model_cls: type[BaseModel]) -> list[dict[str, Any]]:
    """Render a `params_model`'s fields as a list of `{name, type, default?}`.

    Used by the `list_templates` tool to show the agent what each
    template expects without forcing a `get_template_detail` round-trip
    per candidate. Returns `[]` if the model has no fields.

    NOTE: Pydantic v2 represents "no default" (required field) as the
    `PydanticUndefined` sentinel, NOT `None`. We must exclude it — if a
    `PydanticUndefined` value leaks into the tool-return dict, Pydantic AI
    cannot JSON-serialize the tool result and the whole turn crashes with
    `PydanticSerializationError: Unable to serialize unknown type:
    PydanticUndefinedType`.
    """
    out: list[dict[str, Any]] = []
    try:
        fields = model_cls.model_fields
    except AttributeError:
        return out
    for name, f in fields.items():
        type_name = getattr(f.annotation, "__name__", str(f.annotation))
        entry: dict[str, Any] = {"name": name, "type": type_name}
        if f.default is not PydanticUndefined and f.default is not None:
            entry["default"] = f.default
        elif f.default_factory is not None and f.default_factory is not PydanticUndefined:
            with contextlib.suppress(Exception):
                entry["default"] = f.default_factory()
        if f.description:
            entry["description"] = f.description
        out.append(entry)
    return out


def _register_tools(agent: Agent[AgentDeps, Plan]) -> None:
    """Register all five agent tools on `agent`.

    Tool bodies:
    * `list_templates` — sync; reads from `ctx.deps.registry`.
    * `get_template_detail` — sync; raises `ModelRetry` on unknown id
      so the model self-corrects ("Tool bodies may raise
      `ModelRetry` to feed errors back.").
    * `lookup_player`, `lookup_team`, `lookup_season` — async because
      `ctx.deps.db.execute` is async. All use parameterized queries
      (`$name` placeholders) — never string interpolation.

    The `_tool_*` wrappers are sync/async module-level functions so
    they're introspectable from tests; the decorator calls them as
    methods on the agent's tool registry.
    """

    @agent.tool
    def list_models(ctx: RunContext[AgentDeps]) -> list[dict]:
        """List the semantic catalog's business models (governed-SQL path).

        Returns
        -------
        list of dict
            One entry per model with ``model``, ``description``, ``grain``,
            ``base_table``, ``synonyms`` (first 5), and ``example_questions``
            (first 3). Empty list if the catalog is not loaded.
        """
        catalog = ctx.deps.catalog
        if catalog is None:
            return []
        out: list[dict] = []
        for name in catalog.list_models():
            m = catalog.get_model(name)
            out.append(
                {
                    "model": m.model,
                    "description": m.description,
                    "grain": m.grain,
                    "base_table": m.base_table.name,
                    "synonyms": list(m.synonyms)[:5],
                    "example_questions": list(m.example_questions)[:3],
                }
            )
        return out

    @agent.tool
    def get_model_detail(ctx: RunContext[AgentDeps], model: str) -> dict:
        """Get full detail for one semantic catalog model (governed-SQL path).

        Parameters
        ----------
        model
            The model name (e.g. ``'player_season'``). Discover valid names
            via ``list_models``.

        Returns
        -------
        dict
            Full model metadata: ``model``, ``description``, ``grain``,
            ``base_table`` (``{name, alias}``), ``dimensions`` (list of
            ``{name, expr, description}``), ``measures`` (list of
            ``{name, expr, description, additivity}``), ``joins`` (list of
            ``{name, model, type, left_on, right_on}``), ``caveats``,
            ``synonyms``, ``example_questions``.

        Raises
        ------
        ModelRetry
            If the catalog is not loaded or ``model`` is unknown. This
            feeds the error back so the model calls ``list_models`` first.
        """
        catalog = ctx.deps.catalog
        if catalog is None:
            raise ModelRetry("The semantic catalog is not loaded. Governed SQL is unavailable.")
        try:
            m = catalog.get_model(model)
        except KeyError:
            raise ModelRetry(
                f"Unknown model {model!r}. Call list_models first to discover valid names."
            ) from None
        return {
            "model": m.model,
            "description": m.description,
            "grain": m.grain,
            "base_table": {"name": m.base_table.name, "alias": m.base_table.alias},
            "dimensions": [
                {
                    "name": d.name,
                    "type": _catalog_field_type(d.name, d.expr, d.description, measure=False),
                    "expr": d.expr,
                    "description": d.description,
                }
                for d in m.dimensions
            ],
            "measures": [
                {
                    "name": me.name,
                    "type": _catalog_field_type(me.name, me.expr, me.description, measure=True),
                    "expr": me.expr,
                    "description": me.description,
                    "additivity": me.additivity,
                }
                for me in m.measures
            ],
            "joins": [
                {
                    "name": j.name,
                    "model": j.model,
                    "type": j.type,
                    "left_on": j.left_on,
                    "right_on": j.right_on,
                }
                for j in m.joins
            ],
            "caveats": list(m.caveats),
            "synonyms": list(m.synonyms),
            "example_questions": list(m.example_questions),
        }

    @agent.tool
    async def list_warehouse_tables(ctx: RunContext[AgentDeps]) -> list[str]:
        """List approved live warehouse tables when the catalog lacks coverage."""
        allowed_sources = sorted(
            t
            for t in ALLOWED_TABLES_FOR_AGENT
            if not t.startswith(("dim_", "fact_", "mart_", "analytics_"))
        )
        result = await ctx.deps.db.execute(
            """SELECT DISTINCT table_name FROM information_schema.columns
               WHERE table_schema = 'main' AND (table_name LIKE 'dim_%' OR table_name LIKE 'fact_%'
                 OR table_name LIKE 'mart_%' OR table_name LIKE 'analytics_%'
                 OR table_name = ANY($allowed_sources))
               ORDER BY table_name""",
            {"allowed_sources": allowed_sources},
        )
        return [str(row["table_name"]) for row in result.rows]

    @agent.tool
    async def describe_table(ctx: RunContext[AgentDeps], table: str) -> list[dict]:
        """Describe an approved live warehouse table's columns and types."""
        if not (
            table.startswith(("dim_", "fact_", "mart_", "analytics_"))
            or table in ALLOWED_TABLES_FOR_AGENT
        ):
            raise ModelRetry("Only approved warehouse tables may be inspected.")
        result = await ctx.deps.db.execute(
            """SELECT column_name, data_type FROM information_schema.columns
               WHERE table_schema = 'main' AND table_name = $table ORDER BY ordinal_position""",
            {"table": table},
        )
        if not result.rows:
            raise ModelRetry(f"Unknown approved warehouse table {table!r}.")
        return [
            {"name": str(row["column_name"]), "type": str(row["data_type"])} for row in result.rows
        ]

    @agent.tool
    async def preview(ctx: RunContext[AgentDeps], table: str, n: int = 5) -> list[dict]:
        """Preview up to five rows from an approved table after describing it."""
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
            raise ModelRetry(f"Invalid table identifier: {table!r}")
        if not (
            table.startswith(("dim_", "fact_", "mart_", "analytics_"))
            or table in ALLOWED_TABLES_FOR_AGENT
        ):
            raise ModelRetry("Only approved warehouse tables may be previewed.")
        if not 1 <= n <= 5:
            raise ModelRetry("preview n must be between 1 and 5.")
        # The table name is checked against a fixed identifier policy before interpolation.
        result = await ctx.deps.db.execute(f'SELECT * FROM "{table}" LIMIT {n}')
        return list(result.rows)

    @agent.tool
    async def lookup_player(ctx: RunContext[AgentDeps], name: str) -> list[dict]:
        """Resolve a free-text player name to `player_id`(s) via `dim_player`.

        Parameters
        ----------
        name
            The free-text name fragment (substring, case-insensitive).
            E.g. ``"Stephen Curry"``, ``"Curry"``, ``"lebron"``.

        Returns
        -------
        list of dict
            Top 10 matches with ``player_id`` (BIGINT as int) and
            ``full_name``. Empty list if no match.
        """
        result = await ctx.deps.db.execute(
            """
            SELECT player_id, full_name
            FROM dim_player
            WHERE full_name ILIKE $pattern
            ORDER BY full_name
            LIMIT 10
            """,
            {"pattern": f"%{name}%"},
        )
        return [
            {"player_id": int(r["player_id"]), "full_name": str(r["full_name"])}
            for r in result.rows
            if r.get("player_id") is not None and r.get("full_name") is not None
        ]

    @agent.tool
    async def lookup_team(ctx: RunContext[AgentDeps], name: str) -> list[dict]:
        """Resolve a free-text team name to `team_id`(s) via `dim_team`.

        Matches against ``full_name``, ``nickname``, ``city``, and
        ``abbreviation`` (case-insensitive substring on each). Returns
        up to 10 matches with ``team_id``, ``abbreviation``,
        ``nickname``, ``city``.
        """
        result = await ctx.deps.db.execute(
            """
            SELECT team_id, abbreviation, nickname, city
            FROM dim_team
            WHERE full_name ILIKE $pattern
               OR nickname   ILIKE $pattern
               OR city       ILIKE $pattern
               OR abbreviation ILIKE $pattern
            ORDER BY full_name
            LIMIT 10
            """,
            {"pattern": f"%{name}%"},
        )
        return [
            {
                "team_id": int(r["team_id"]),
                "abbreviation": str(r.get("abbreviation", "")),
                "nickname": str(r.get("nickname", "")),
                "city": str(r.get("city", "")),
            }
            for r in result.rows
            if r.get("team_id") is not None
        ]

    @agent.tool
    async def lookup_season(ctx: RunContext[AgentDeps], phrase: str) -> str:
        """Resolve a season phrase to a canonical `season_year` VARCHAR.

        Handles, with a documented rule per case:

        * ``"YYYY-YY"`` (short form) → returned as-is, e.g. ``"2015-16"``.
        * ``"YYYY-YYYY"`` (long form) → folded to ``"YYYY-YY"``,
          e.g. ``"2015-2016"`` → ``"2015-16"``.
        * ``"YYYY"`` alone → ambiguous; we follow the NBA/BBR convention
          that a year refers to the season that **ends** in that year.
          So ``"2016"`` → ``"2015-16"`` (the 2016 Finals were the
          2015-16 season). If the implied season isn't in the warehouse,
          we raise `ModelRetry` so the model can ask the user.
        * ``"last season"`` / ``"this season"`` / ``"current"`` /
          ``"now"`` → the most recent `season_year` in
          ``mart_player_season`` (whichever is highest lexicographically
          — works because the canonical format is ``"YYYY-YY"`` with
          fixed-width 2-digit suffix).
        * Anything else → `ModelRetry` so the model self-corrects.

        Returns
        -------
        str
            The canonical season_year (e.g. ``"2015-16"``).

        Raises
        ------
        ModelRetry
            If the phrase cannot be parsed, or the resolved season is
            not present in the warehouse.
        """
        normalized = phrase.strip()
        if not normalized:
            raise ModelRetry("Empty season phrase.")

        if re.fullmatch(r"\d{4}-\d{2}", normalized):
            return normalized

        m = re.fullmatch(r"(\d{4})-(\d{4})", normalized)
        if m:
            head, tail = m.group(1), m.group(2)
            short = f"{head}-{tail[-2:]}"
            return short

        m = re.fullmatch(r"(\d{4})", normalized)
        if m:
            year = int(m.group(1))
            implied = f"{year - 1}-{str(year)[-2:]}"
            return implied

        relative = normalized.lower()
        relative_set = {
            "last season",
            "this season",
            "current season",
            "current",
            "now",
            "most recent",
        }
        if relative in relative_set:
            result = await ctx.deps.db.execute(
                """
                SELECT season_year
                FROM mart_player_season
                WHERE season_year IS NOT NULL
                GROUP BY season_year
                ORDER BY season_year DESC
                LIMIT 1
                """
            )
            if not result.rows:
                raise ModelRetry(
                    "Could not resolve 'last season' — mart_player_season has no rows."
                )
            return str(result.rows[0]["season_year"])

        raise ModelRetry(
            f"Could not parse season phrase {phrase!r}. Expected 'YYYY-YY', 'YYYY-YYYY', "
            f"'YYYY', 'last season', or 'this season'."
        )


async def make_deps() -> AgentDeps:
    """Build the default `AgentDeps` for a turn.

    Async because `get_schema_context()` is async (it hits the warehouse).
    Routes call this once per turn; tests may build their own deps and
    pass them to `agent.run(deps=...)` directly.

    The semantic catalog (``catalog``) is loaded synchronously via
    :func:`load_catalog`; the loader is module-cached and cheap on a
    warm cache. A load failure must NOT crash server startup, so the
    call is wrapped in a broad ``except Exception`` — on failure we
    log a warning and leave ``catalog=None``. The governed-SQL branch
    (Phase 4 wiring, landed in 3.3b) interprets ``catalog=None`` as a
    "catalog not loaded" / not-answerable signal.
    """
    schema = await get_schema_context()
    catalog: SemanticCatalog | None = None
    try:
        catalog = load_catalog()
    except Exception as exc:
        logger.warning("semantic catalog failed to load; AgentDeps.catalog=None: %s", exc)
    return AgentDeps(
        schema_context=schema,
        db=get_db(),
        catalog=catalog,
    )


def keep_last_messages_with_tools(
    messages: list,
    n: int = 20,
) -> list:
    """Trim a Pydantic AI ``ModelMessage`` history to the last ``n`` items.

    Pydantic AI rejects a ``message_history`` that contains a
    ``ToolReturnPart`` orphaned from its preceding ``ToolCallPart`` (or
    vice versa); the LLM provider needs the pair intact to interpret the
    tool call's result. A naive ``messages[-n:]`` slice can cleave such
    a pair in half.

    Algorithm
    ---------
    1. If ``len(messages) <= n`` return as-is (no trim needed).
    2. Compute the initial cut point at ``len(messages) - n``.
    3. Build a ``tool_call_id -> ToolCallPart-index`` map from every
       message in the input.
    4. While the message at the cut boundary contains a
       ``ToolReturnPart`` whose matching ``ToolCallPart`` is in the
       dropped prefix, back the cut up by one (keeping the pair intact).
       Each step re-checks the new boundary; chains resolve in O(k) for
       k dropped pairs.
    5. Return ``messages[cut:]``.

    Defensive
    ---------
    * Falls back to a plain last-``n`` slice when the part types can't
      be detected (e.g. pydantic-ai version drift renamed the classes).
      The class-identity checks (``isinstance(part, ToolCallPart)``)
      catch ImportError-like failures by failing ``isinstance`` to
      ``False`` rather than raising.
    * No IO. Pure function. Returns a new list (slicing returns a new
      list in CPython), so callers can mutate freely.

    Parameters
    ----------
    messages
        The full ``list[ModelMessage]`` to trim. Each message must
        expose a ``.parts`` iterable of part objects (Pydantic AI
        ``ModelMessage`` shape).
    n
        Target maximum number of messages to keep. Default ``20``;
        matched by the pipeline / route callers.

    Returns
    -------
    list
        A slice of ``messages`` of length ``<= n`` whose first element
        (if any) does not contain an orphaned tool-return pair.
    """
    if n <= 0:
        return []
    if len(messages) <= n:
        return list(messages)

    cut = len(messages) - n

    call_locations: dict[str, int] = {}
    for i, msg in enumerate(messages):
        parts = getattr(msg, "parts", ()) or ()
        for part in parts:
            if isinstance(part, ToolCallPart):
                tool_call_id = getattr(part, "tool_call_id", None)
                if tool_call_id is not None:
                    call_locations[tool_call_id] = i

    while cut > 0:
        boundary = messages[cut]
        parts = getattr(boundary, "parts", ()) or ()
        has_orphan = False
        for part in parts:
            if isinstance(part, ToolReturnPart):
                tool_call_id = getattr(part, "tool_call_id", None)
                call_idx = call_locations.get(tool_call_id) if tool_call_id else None
                if call_idx is not None and call_idx < cut:
                    has_orphan = True
                    break
        if not has_orphan:
            break
        cut -= 1

    return list(messages[cut:])


async def run_agent(
    user_prompt: str,
    *,
    deps: AgentDeps | None = None,
    model: Model | None = None,
    message_history: list | None = None,
) -> Plan:
    """Run the agent on `user_prompt` and return the typed `Plan`.

    Convenience helper used by tests and (in Phase 4) the chat route.
    `deps` and `model` default to the live production wiring; tests
    inject fakes to avoid network calls.

    Note: this helper DOES make a live OpenRouter call when `model` is
    `None`. Tests must pass `TestModel(...)` via `get_agent(model=...)`
    first (so the singleton uses the fake) and `make_deps()`-style deps
    pointing at a test DB.

    `message_history` is an optional pass-through to
    ``agent.run(message_history=...)``: a list of Pydantic AI
    ``ModelMessage`` objects from a previous turn. ``None`` (the
    default) preserves the original single-turn behaviour — existing
    call sites are unaffected.
    """
    agent = get_agent(model=model)
    actual_deps = deps if deps is not None else await make_deps()
    kwargs: dict[str, Any] = {"deps": actual_deps}
    if message_history is not None:
        kwargs["message_history"] = message_history
    result = await agent.run(user_prompt, **kwargs)
    return result.output


__all__ = [
    "Plan",
    "ClarifyPlan",
    "NotAnswerablePlan",
    "SqlPlan",
    "Clarification",
    "ResultContract",
    "AgentDeps",
    "SYSTEM_PROMPT_TEMPLATE_GOVERNED",
    "get_agent",
    "reset_agent_for_tests",
    "make_deps",
    "run_agent",
    "keep_last_messages_with_tools",
]
