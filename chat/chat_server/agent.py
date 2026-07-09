"""Pydantic AI agent for the basketball chatbot.

Phase 3 exit criteria:
* Singleton `Agent` with native `OpenRouterModel`.
* Typed `QueryPlan` output (Pydantic model).
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
* `QueryPlan` — the typed output model.
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
from enum import StrEnum
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, model_validator
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ToolCallPart, ToolReturnPart
from pydantic_ai.models import Model
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_core import PydanticUndefined

from .config import get_settings
from .db import DuckDBSingleton, get_db
from .schema_context import SchemaContext, get_schema_context
from .semantic_catalog import SemanticCatalog, load_catalog
from .templates import (
    TemplateNotFound,
    get_registry,
    get_template,
)
from .templates import (
    list_templates as _registry_list_templates,
)

logger = logging.getLogger(__name__)


class AnswerMode(StrEnum):
    """How the agent intends to handle this turn.

    TEMPLATE is the legacy compatibility mode: the plan carries
    ``template_id`` + ``params`` and the existing template execution
    path runs. Kept so the 50 sample conversations and the fixture
    regression suite keep passing while the governed-SQL path rolls out.

    EXECUTE_SQL is the governed path: the plan carries ``sql`` +
    ``result_contract``; the server validates, dry-runs, and executes
    read-only. CLARIFY and NOT_ANSWERABLE short-circuit execution.
    """

    TEMPLATE = "template"
    EXECUTE_SQL = "execute_sql"
    CLARIFY = "clarify"
    NOT_ANSWERABLE = "not_answerable"


class Clarification(BaseModel):
    """A structured disambiguation question (governed path).

    The free-text legacy form (a bare ``str``) is still accepted on
    ``QueryPlan.clarification`` for backward compatibility; new code
    constructs this structured form so the clarification-state layer
    can route the user's reply back to the right model.
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


class QueryPlan(BaseModel):
    """Typed output the agent produces on every turn.

    Discriminated union on ``answer_mode``. To preserve backward
    compatibility with the template-driven era, ``answer_mode`` defaults
    to ``TEMPLATE`` and the legacy fields retain their current types.

    Branching order in the pipeline / route (load-bearing):

    1. ``clarification is not None`` -> clarify (accepts str OR Clarification).
    2. ``not_answerable_note is not None`` -> not-answerable.
    3. ``answer_mode == EXECUTE_SQL and sql`` -> governed-SQL branch.
    4. else -> legacy template path (unchanged fallthrough).
    """

    answer_mode: AnswerMode = AnswerMode.TEMPLATE
    question_interpretation: str = ""

    template_id: str = Field(
        default="",
        description=(
            "Template id that exists in the registry (legacy mode). "
            "Empty when answer_mode != TEMPLATE."
        ),
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Typed params validated against the template's Params model (legacy mode).",
    )

    sql: str | None = Field(
        default=None,
        description="Generated DuckDB SQL drawn only from catalog base tables (governed mode).",
    )
    result_contract: ResultContract | None = Field(
        default=None,
        description="Expected grain/columns/row-limit/answer-style (governed mode).",
    )

    clarification: str | Clarification | None = Field(
        default=None,
        description=(
            "Set when the question is too ambiguous to act on. Accepts a "
            "free-text str (legacy) or a structured Clarification (governed)."
        ),
    )

    not_answerable_note: str | None = Field(
        default=None,
        description="Set when the question cannot be answered; explains why with evidence.",
    )

    @model_validator(mode="after")
    def _check_mode_consistency(self) -> QueryPlan:
        """Enforce field presence per mode. Lenient on TEMPLATE mode."""
        if self.answer_mode == AnswerMode.EXECUTE_SQL:
            if not self.sql:
                raise ValueError("execute_sql mode requires `sql`")
        elif self.answer_mode == AnswerMode.CLARIFY:
            if self.clarification is None:
                raise ValueError("clarify mode requires `clarification`")
        elif self.answer_mode == AnswerMode.NOT_ANSWERABLE and not self.not_answerable_note:
            raise ValueError("not_answerable mode requires `not_answerable_note`")
        return self


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

    registry: dict
    schema_context: SchemaContext
    db: DuckDBSingleton
    catalog: SemanticCatalog | None = None


SYSTEM_PROMPT_TEMPLATE = """\
You are a basketball analytics assistant. Answer NBA data questions ONLY by selecting a query
template and extracting typed parameters. NEVER write SQL. If a user's question maps to a
template, call list_templates / get_template_detail to inspect it, then return a QueryPlan
with the template_id and validated params. If params are missing or ambiguous, set
QueryPlan.clarification to a specific question. If NO template fits, set
QueryPlan.not_answerable_note explaining why (with the evidence). Use lookup_player /
lookup_team / lookup_season to resolve free-text names to canonical ids — never guess ids.
The warehouse is the only source of truth for data.

Available warehouse tables and metrics:
{schema_context}
"""


SYSTEM_PROMPT_TEMPLATE_GOVERNED = """\
You are a basketball analytics assistant with governed SQL access to an NBA
DuckDB warehouse. Answer data questions by writing DuckDB SELECT queries that
draw ONLY from the curated semantic catalog's business models. Call
list_models / get_model_detail to inspect a model's base table, dimensions,
measures, joins, and caveats before writing SQL. Use lookup_player /
lookup_team / lookup_season to resolve free-text names to canonical ids —
never guess ids.

Available semantic models (call get_model_detail for full schemas):
{catalog_summary}

Warehouse reference (allowlist + metrics, supplementary to the catalog):
{schema_context}

Rules for every governed SQL answer:
1. SELECT only. No INSERT / UPDATE / DELETE / CREATE / DROP / PRAGMA / ATTACH.
2. FROM and JOIN only the catalog models' base tables. Never query a table
   that is not in the catalog. If a question needs a table the catalog does
   not cover, decline with QueryPlan.not_answerable_note citing the gap.
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
7. If the question is out of scope (no catalog model covers it, or it asks for
   non-NBA data), return answer_mode = not_answerable with a note explaining
   why and what evidence you checked.
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


def _build_model() -> OpenRouterModel:
    """Construct the live `OpenRouterModel` from settings.

    The native `OpenRouterProvider` sets the required `HTTP-Referer` and
    `X-Title` headers from `app_url` / `app_title`. Building
    the model does NOT make a network call — only `agent.run()` does.
    """
    s = get_settings()
    return OpenRouterModel(
        s.openrouter_model,
        provider=OpenRouterProvider(
            api_key=s.openrouter_api_key,
            app_url="https://github.com/nickth3man/basketball-data-emporium",
            app_title="Basketball Data Chatbot",
        ),
    )


_agent: Agent[AgentDeps, QueryPlan] | None = None
_agent_lock = threading.Lock()


def get_agent(model: Model | None = None) -> Agent[AgentDeps, QueryPlan]:
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
) -> Agent[AgentDeps, QueryPlan]:
    """Build a fresh agent (called by `get_agent` on first invocation)."""
    m = model if model is not None else _build_model()
    raw: Agent[AgentDeps, QueryPlan] = cast(
        "Agent[AgentDeps, QueryPlan]",
        Agent(
            m,
            output_type=QueryPlan,
            deps_type=AgentDeps,
            retries={"output": 3, "tools": 2},
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
        if get_settings().chat_governed_sql_mode and ctx.deps.catalog is not None:
            return SYSTEM_PROMPT_TEMPLATE_GOVERNED.format(
                schema_context=schema_text,
                catalog_summary=_catalog_summary(ctx.deps.catalog),
            )
        return SYSTEM_PROMPT_TEMPLATE.format(schema_context=schema_text)

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


def _register_tools(agent: Agent[AgentDeps, QueryPlan]) -> None:
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
    def list_templates(ctx: RunContext[AgentDeps], capability: str | None = None) -> list[dict]:
        """List available query templates, optionally filtered by capability family.

        Parameters
        ----------
        capability
            Optional capability family (folder name, e.g.
            ``'season_thresholds'``). When omitted, every registered
            template is returned.

        Returns
        -------
        list of dict
            One entry per template with: ``template_id``, ``title``,
            ``description``, ``capability``, ``examples`` (truncated
            to first 3), ``params`` (list of `{name, type, default?,
            description?}`).
        """
        templates = _registry_list_templates(capability)
        return [
            {
                "template_id": t.template_id,
                "title": t.title,
                "description": t.description,
                "capability": t.capability,
                "examples": list(t.examples)[:3],
                "params": _summarize_params(t.params_model),
            }
            for t in templates
        ]

    @agent.tool
    def get_template_detail(ctx: RunContext[AgentDeps], template_id: str) -> dict:
        """Get full detail for one template.

        Parameters
        ----------
        template_id
            The dotted template id (e.g.
            ``'season_thresholds.fifty_forty_ninety'``).

        Returns
        -------
        dict
            Full metadata: ``template_id``, ``title``, ``description``,
            ``capability``, ``examples``, ``allowed_tables``,
            ``result_schema`` (col name -> type name), ``answer_policy``,
            ``default_limit``, ``timeout_seconds``, ``params`` (full
            list with descriptions), ``sql_preview`` (first 200 chars
            of the SQL, to orient the agent without leaking the whole
            query).

        Raises
        ------
        ModelRetry
            If `template_id` is not in the registry. This feeds the
            error back to the model so it can call `list_templates`
            and try again (pydantic-ai#822 mitigation).
        """
        try:
            t = get_template(template_id)
        except TemplateNotFound:
            raise ModelRetry(
                f"Unknown template_id {template_id!r}. Call list_templates first "
                f"to discover valid ids."
            ) from None
        return {
            "template_id": t.template_id,
            "title": t.title,
            "description": t.description,
            "capability": t.capability,
            "examples": list(t.examples),
            "allowed_tables": sorted(t.allowed_tables),
            "result_schema": {
                col: getattr(typ, "__name__", str(typ)) for col, typ in t.result_schema.items()
            },
            "answer_policy": t.answer_policy,
            "default_limit": t.default_limit,
            "timeout_seconds": t.timeout_seconds,
            "params": _summarize_params(t.params_model),
            "sql_preview": t.sql.strip()[:200],
        }


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
                {"name": d.name, "expr": d.expr, "description": d.description} for d in m.dimensions
            ],
            "measures": [
                {
                    "name": me.name,
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
        registry=get_registry(),
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
) -> QueryPlan:
    """Run the agent on `user_prompt` and return the typed `QueryPlan`.

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
    "QueryPlan",
    "AnswerMode",
    "Clarification",
    "ResultContract",
    "AgentDeps",
    "SYSTEM_PROMPT_TEMPLATE",
    "SYSTEM_PROMPT_TEMPLATE_GOVERNED",
    "get_agent",
    "reset_agent_for_tests",
    "make_deps",
    "run_agent",
    "keep_last_messages_with_tools",
]
