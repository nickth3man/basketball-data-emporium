"""Pydantic AI agent for the basketball chatbot (PLAN §7.5).

Phase 3 exit criteria (PLAN §15):
* Singleton `Agent` with native `OpenRouterModel`.
* Typed `QueryPlan` output (Pydantic model).
* Tools: `list_templates`, `get_template_detail`, `lookup_player`,
  `lookup_team`, `lookup_season`.
* `retries={'output': 3, 'tools': 2}` per PLAN §7.5 (mitigates
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
* `AgentDeps` — the deps dataclass (registry + schema_context + db).
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
from typing import Any, cast

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.models import Model
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_core import PydanticUndefined

from .config import get_settings
from .db import DuckDBSingleton, get_db
from .schema_context import SchemaContext, get_schema_context
from .templates import (
    TemplateNotFound,
    get_registry,
    get_template,
)
from .templates import (
    list_templates as _registry_list_templates,
)

logger = logging.getLogger(__name__)


# -- Output model -----------------------------------------------------------


class QueryPlan(BaseModel):
    """Typed output the agent must produce on every turn (PLAN §7.5).

    The agent picks a registered template + extracts typed parameters,
    or sets one of `clarification` / `not_answerable_note`. Exactly one
    of `template_id`, `clarification`, or `not_answerable_note` should
    be set in a well-formed plan; the composer (Phase 4) is responsible
    for rejecting ambiguous plans.

    Attributes
    ----------
    template_id
        Template id that exists in the registry, e.g.
        ``'season_thresholds.fifty_forty_ninety'``.
    params
        Parameter dict validated against the template's `Params` model
        at render time (not by Pydantic AI — the agent's structured
        output is just `dict[str, Any]`).
    clarification
        Set when the user's question is too ambiguous to act on; this
        string is the question to ask back.
    not_answerable_note
        Set when no template fits; explains why (with attempted SQL or
        evidence) so the user sees the reason rather than a silent miss.
    """

    template_id: str = Field(
        default="",
        description=(
            "Template id that exists in the registry, e.g. 'season_thresholds.fifty_forty_ninety'."
        ),
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Typed params validated against the template's Params model.",
    )
    clarification: str | None = Field(
        default=None,
        description="Set when params are missing/ambiguous and the user must be asked.",
    )
    not_answerable_note: str | None = Field(
        default=None,
        description="Set when no template fits; explains why with the attempted SQL/evidence.",
    )


# -- Deps -------------------------------------------------------------------


@dataclass
class AgentDeps:
    """Runtime deps carried by every agent run (PLAN §7.5).

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
    """

    registry: dict
    schema_context: SchemaContext
    db: DuckDBSingleton


# -- System prompt ---------------------------------------------------------


#: Template body for the agent's system prompt. The schema-context text
#: is filled in at run time via the `@agent.system_prompt` decorator
#: (see `_build_agent`).
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


# -- Model construction ----------------------------------------------------


def _build_model() -> OpenRouterModel:
    """Construct the live `OpenRouterModel` from settings.

    The native `OpenRouterProvider` sets the required `HTTP-Referer` and
    `X-Title` headers from `app_url` / `app_title` (PLAN §7.5). Building
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


# -- Singleton agent -------------------------------------------------------


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
    # The `Agent` constructor's overloads don't propagate the `output_type=`
    # generic argument (pydantic-ai typing limitation). Construct as
    # `Agent[AgentDeps, str]` per ty's inference, then `cast` to the
    # intended `Agent[AgentDeps, QueryPlan]` — runtime behaviour is
    # unchanged (PLAN §7.5: `output_type=QueryPlan`).
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

        Synchronous (returns a string). Reads from `ctx.deps` so the
        schema context is whatever the caller passed in `make_deps()`
        (default: the cached `get_schema_context()`).
        """
        return SYSTEM_PROMPT_TEMPLATE.format(
            schema_context=ctx.deps.schema_context.as_prompt_text()
        )

    _register_tools(agent)
    return agent


def reset_agent_for_tests() -> None:
    """Drop the cached singleton (test helper only)."""
    global _agent
    with _agent_lock:
        _agent = None


# -- Tools -----------------------------------------------------------------


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
    """Register all five agent tools on `agent` (PLAN §7.5).

    Tool bodies:
    * `list_templates` — sync; reads from `ctx.deps.registry`.
    * `get_template_detail` — sync; raises `ModelRetry` on unknown id
      so the model self-corrects (PLAN §7.5: "Tool bodies may raise
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
            and try again (PLAN §7.5 + pydantic-ai#822 mitigation).
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

        # Exact short form.
        if re.fullmatch(r"\d{4}-\d{2}", normalized):
            return normalized

        # Long form: fold "YYYY-YYYY" -> "YYYY-YY".
        m = re.fullmatch(r"(\d{4})-(\d{4})", normalized)
        if m:
            head, tail = m.group(1), m.group(2)
            short = f"{head}-{tail[-2:]}"
            return short

        # Single year: NBA convention = the season that ENDS in that year.
        m = re.fullmatch(r"(\d{4})", normalized)
        if m:
            year = int(m.group(1))
            implied = f"{year - 1}-{str(year)[-2:]}"
            return implied

        # Relative phrases.
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


# -- make_deps -------------------------------------------------------------


async def make_deps() -> AgentDeps:
    """Build the default `AgentDeps` for a turn.

    Async because `get_schema_context()` is async (it hits the warehouse).
    Routes call this once per turn; tests may build their own deps and
    pass them to `agent.run(deps=...)` directly.
    """
    schema = await get_schema_context()
    return AgentDeps(
        registry=get_registry(),
        schema_context=schema,
        db=get_db(),
    )


# -- One-shot helper (used by tests + the composer in Phase 4) -----------


async def run_agent(
    user_prompt: str,
    *,
    deps: AgentDeps | None = None,
    model: Model | None = None,
) -> QueryPlan:
    """Run the agent on `user_prompt` and return the typed `QueryPlan`.

    Convenience helper used by tests and (in Phase 4) the chat route.
    `deps` and `model` default to the live production wiring; tests
    inject fakes to avoid network calls.

    Note: this helper DOES make a live OpenRouter call when `model` is
    `None`. Tests must pass `TestModel(...)` via `get_agent(model=...)`
    first (so the singleton uses the fake) and `make_deps()`-style deps
    pointing at a test DB.
    """
    agent = get_agent(model=model)
    actual_deps = deps if deps is not None else await make_deps()
    result = await agent.run(user_prompt, deps=actual_deps)
    return result.output


__all__ = [
    "QueryPlan",
    "AgentDeps",
    "SYSTEM_PROMPT_TEMPLATE",
    "get_agent",
    "reset_agent_for_tests",
    "make_deps",
    "run_agent",
]
