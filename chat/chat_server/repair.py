"""Governed-SQL repair loop (PLAN §Guardrails / Stage 3.4).

When the agent's first ``QueryPlan`` SQL passes the catalog gate but
fails to actually resolve against the warehouse (stale column reference,
typoed identifier, join key that doesn't exist, ...) the pipeline needs
a single shot at self-correction before giving up.

Design lineage
--------------
This module is the Stage 3.4 implementation of two classical patterns
in LLM-driven SQL generation:

* **MAC-SQL Refiner** -- one bounded repair round where the model gets
  the failed SQL + the engine error + a curated schema, and emits a
  corrected SQL. We cap the loop at ``MAX_ROUND = 1`` so a misbehaving
  model can't spin (PLAN: "reprompt once").
* **DIN-SQL 7-bullet fix-it rules** -- the seven ``Fix-it rules`` in
  ``prompts/repair.txt`` (re-read intent, verify every table/column,
  check join keys + grain, replace fabricated identifiers, preserve
  intent, honor additivity, decline when unfixable from the schema
  alone). Those rules are paraphrased into :data:`REFINER_PREAMBLE`
  rather than loaded verbatim, because the structured agent returns a
  ``QueryPlan`` (not raw SQL) and our refiner message must be
  compatible with that output shape.

Output-shape contract (load-bearing)
------------------------------------
The refiner reuses the SAME structured ``Agent`` that produced the
initial plan. So the model emits another ``QueryPlan``, not a free-text
SQL string. The preamble explicitly tells the model to set
``answer_mode = execute_sql`` and fill ``sql`` with the corrected
query. If the model determines the SQL is unfixable from the schema
alone, the preamble directs it to emit
``answer_mode = not_answerable`` with a one-line note -- which the
caller detects via :func:`repair_sql` returning ``None``.

This module never raises; a repair attempt that blows up (model
crash, validation exception, network error) degrades to ``None`` so
the caller can fall back to a not-answerable response rather than
crash the turn.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, cast

from .agent import AnswerMode, QueryPlan

if TYPE_CHECKING:
    from .agent import Agent, AgentDeps
    from .semantic_catalog import SemanticCatalog

log = logging.getLogger(__name__)


#: Maximum number of repair rounds. The PLAN says "reprompt once" so we
#: cap the loop at one re-prompt -- a misbehaving model can't spin the
#: turn indefinitely. Bumping this would require also revisiting the
#: streaming-event ordering in the pipeline.
MAX_ROUND: int = 1


#: Instruction preamble appended to the refiner user message. The
#: preamble tells the structured agent what shape its output must take
#: (``answer_mode = execute_sql`` with a corrected ``sql`` field, OR
#: ``answer_mode = not_answerable`` with a one-line note), then
#: paraphrases the DIN-SQL 7-bullet fix-it rules so the model has a
#: concrete checklist. The closing line ("do NOT guess") is the
#: load-bearing safety valve: a refiner that can't recover from the
#: schema alone must decline, not hallucinate.
REFINER_PREAMBLE: str = (
    "The SQL below failed to dry-run against the warehouse. "
    "You are being asked to fix it. Emit a new QueryPlan whose "
    "answer_mode is execute_sql and whose sql field carries the "
    "corrected DuckDB SQL. Use only the curated catalog models "
    "listed under 'Schema' below; do NOT invent tables or columns. "
    "Apply these fix-it rules:\n"
    "  1. Re-read the question and confirm the corrected SQL still "
    "answers the same intent.\n"
    "  2. Verify every referenced table and column exists in the "
    "'Schema' section; never invent names.\n"
    "  3. Check join keys and grain: every join must use a documented "
    "left_on / right_on pair from the catalog, and the resulting row "
    "grain must match the question.\n"
    "  4. Replace any fabricated or hallucinated identifier with the "
    "canonical name from the schema (use the lookup_player / "
    "lookup_team / lookup_season tools as needed).\n"
    "  5. Preserve the original intent (same metrics, same filters, "
    "same ordering); only change what is required to make it run.\n"
    "  6. Honor additivity: if the failure is a fan / chasm trap or a "
    "non-additive sum, switch to the catalog's declared non-additive "
    "path or pre-aggregate the fanned side in a subquery.\n"
    "  7. If the SQL is unfixable from the schema alone, emit a "
    "QueryPlan with answer_mode = not_answerable and a one-line note "
    "explaining why; do NOT guess new tables or columns."
)


#: Lightweight regex for pulling base-table names out of a free-form
#: SQL string. Used by :func:`build_refiner_message` to figure out
#: which catalog models to surface in the condensed schema context.
#: Matches ``FROM <ident>`` and ``JOIN <ident>`` (case-insensitive,
#: tolerates whitespace).
_BASE_TABLE_RE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)


def _models_for_sql(sql: str, catalog: SemanticCatalog) -> list[str]:
    """Return the catalog model names whose base tables appear in ``sql``.

    The SQL is scanned for ``FROM <ident>`` / ``JOIN <ident>`` tokens;
    each token that matches a known ``base_table.name`` in the catalog
    is mapped back to the owning model. When ``sql`` mentions no
    catalog base tables (e.g. the SQL is so broken it doesn't even
    parse) we fall back to the full sorted model list so the refiner
    still gets a usable schema context.

    Parameters
    ----------
    sql
        The broken SQL the agent emitted.
    catalog
        The loaded semantic catalog. Only ``catalog.models`` is read.

    Returns
    -------
    list[str]
        Sorted catalog model names (deterministic ordering -- important
        for prompt-cache friendliness across repair attempts on the
        same SQL).
    """
    base_table_to_model: dict[str, str] = {
        model.base_table.name: model_name for model_name, model in catalog.models.items()
    }
    seen: set[str] = set()
    for match in _BASE_TABLE_RE.finditer(sql):
        candidate = match.group(1)
        if candidate in base_table_to_model:
            seen.add(base_table_to_model[candidate])
    if not seen:
        return catalog.list_models()
    return sorted(seen)


def _render_model_card(model) -> str:  # type: ignore[no-untyped-def]  -- BusinessModel
    """Render one catalog model as a compact, refiner-friendly text block.

    Format (deliberately terse to keep the prompt budget tight):

        <model>: <base_table.name> (alias <base_table.alias>) -- <description>
          dims: <dim.name> x N
          measures: <measure.name> (additivity) x N

    The description is truncated at 140 chars so a long model blurb
    doesn't blow up the prompt. Dims and measures are summarized by
    COUNT only -- the model already knows the column grammar (it wrote
    the original SQL); what it needs from the catalog is the SHAPE of
    what's available.
    """
    desc = model.description.strip().replace("\n", " ")
    if len(desc) > 140:
        desc = desc[:137] + "..."
    dim_names = ", ".join(d.name for d in model.dimensions) or "(none)"
    measure_names = ", ".join(f"{m.name}({m.additivity})" for m in model.measures) or "(none)"
    return (
        f"{model.model}: {model.base_table.name} (alias {model.base_table.alias}) -- {desc}\n"
        f"  dims: {dim_names}\n"
        f"  measures: {measure_names}"
    )


def build_refiner_message(
    question: str,
    broken_sql: str,
    error: str,
    catalog: SemanticCatalog,
) -> str:
    """Assemble the user message handed to the agent for one repair round.

    Shape
    -----
    The returned string is the entire user-prompt content for the
    refiner call -- it replaces the original question and is the only
    thing the structured agent sees for this turn. The format is:

        <REFINER_PREAMBLE>

        Question:
        <question>

        Failed SQL:
        <broken_sql>

        Engine error:
        <error>

        Schema (curated catalog models relevant to this SQL):
        <condensed model cards, one per line>

    The condensed schema lists only the catalog models whose base
    tables appear in ``broken_sql``; if none of them match we fall
    back to every model (so the refiner still has something to work
    with when the SQL is so malformed that no base table is
    recognizable).

    Parameters
    ----------
    question
        The user's original question. Echoed verbatim so the refiner
        can re-ground on intent (Fix-it rule 1).
    broken_sql
        The SQL the model emitted on the first attempt; failed
        ``EXPLAIN`` against the warehouse.
    error
        The engine error message (``str(DryRunError.original)``).
        Concise enough to fit in the prompt budget but specific
        enough that the model can act on it.
    catalog
        The semantic catalog used to build the condensed schema
        context. The loader is module-cached so this is cheap on the
        warm path.
    """
    models = _models_for_sql(broken_sql, catalog)
    schema_lines = [_render_model_card(catalog.models[name]) for name in models]
    schema_block = "\n".join(schema_lines) if schema_lines else "(catalog empty)"

    return (
        f"{REFINER_PREAMBLE}\n\n"
        f"Question:\n{question}\n\n"
        f"Failed SQL:\n{broken_sql}\n\n"
        f"Engine error:\n{error}\n\n"
        f"Schema (curated catalog models relevant to this SQL):\n{schema_block}"
    )


async def repair_sql(
    agent: Agent[AgentDeps, QueryPlan],
    deps: AgentDeps,
    *,
    question: str,
    broken_sql: str,
    error: str,
) -> QueryPlan | None:
    """One-shot repair round: re-prompt the structured agent with the failure.

    Mirrors the initial plan call (see ``chat_server.pipeline.run_turn``
    / ``chat_server.routes.chat.chat``): ``agent.run(message, deps=deps)``
    -- the SAME agent singleton, the SAME ``AgentDeps`` -- so the model
    sees the same system-prompt context it had on the first attempt
    (schema context, catalog, etc.). The user-prompt content is the
    refiner message built by :func:`build_refiner_message`.

    Returns
    -------
    QueryPlan | None
        ``None`` when the model declined (``answer_mode`` is
        ``clarify`` / ``not_answerable``, or the ``sql`` field is
        empty after a successful execute_sql) -- the caller degrades
        to a not-answerable response.

        ``None`` also when the model call itself blows up (network
        error, validation crash on the structured output, ...). A
        repair failure is best-effort; the turn must not crash.

        ``None`` when the catalog is unavailable (deps.catalog is
        None) -- the refiner cannot build a meaningful schema context
        and we'd rather degrade to not-answerable than guess.

        Otherwise: a fresh ``QueryPlan`` with
        ``answer_mode == execute_sql`` and a non-empty ``sql``.

    Notes
    -----
    The returned plan is NOT re-validated against the catalog here;
    the pipeline / route handler does that after we return so it can
    pick up the same error-display path it already uses for the
    initial validation failure.
    """
    catalog = deps.catalog
    if catalog is None:
        log.warning("repair_sql: catalog unavailable; degrading to not-answerable")
        return None

    message = build_refiner_message(
        question=question,
        broken_sql=broken_sql,
        error=error,
        catalog=catalog,
    )
    try:
        result = await agent.run(message, deps=deps)
    except Exception as exc:  # noqa: BLE001
        log.warning("repair_sql: agent.run raised; degrading to not-answerable: %s", exc)
        return None

    # The structured agent is built with ``output_type=QueryPlan``, so
    # ``result.output`` is a QueryPlan at runtime; the cast documents
    # the contract for the type checker (pydantic-ai's generic
    # propagation has the same limitation flagged in ``agent.py``).
    plan = cast(QueryPlan, result.output)
    # Decline paths: clarification / not_answerable -> caller degrades.
    if plan.answer_mode in (AnswerMode.CLARIFY, AnswerMode.NOT_ANSWERABLE):
        return None
    # execute_sql but empty SQL -> not a usable repair.
    if plan.answer_mode == AnswerMode.EXECUTE_SQL:
        if not plan.sql or not plan.sql.strip():
            return None
        return plan
    # TEMPLATE mode slipped through (model abandoned execute_sql) -> not
    # usable for a governed-SQL repair.
    return None


__all__ = [
    "MAX_ROUND",
    "REFINER_PREAMBLE",
    "build_refiner_message",
    "repair_sql",
]
