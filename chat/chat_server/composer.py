"""Answer composer: turns a `QueryResult` into a concise grounded answer.

Phase 3 (this module) implements the minimal per-policy formatters needed
to land the Phase 3 exit criterion: "Non-streaming ``POST /api/chat``
returns a plan + (for one template) executes it and returns the answer."

The composer's contract is small on purpose (PLAN §7.5 #8):

* ``compose(template, result, plan_template_id)`` — happy-path formatter.
* ``compose_not_answerable(note, attempted_sql=None)`` — explicit
  "I could not answer this, here's the evidence" path.

Per-template enrichment (metric citations, gap caveats, custom headlines,
prose re-writes) lands in later phases. The policies supported here are
``ranked_list``, ``single_value``, ``count``; anything else falls back to
a generic "returned N rows" summary so an unknown policy never crashes
the route.

Design notes
------------
* ``answer_policy`` is a string on the template; we dispatch on equality.
  Adding a new policy means adding a branch here, the template's
  ``ANSWER_POLICY`` constant, and a test in ``chat_tests/test_composer.py``.
* The composer never inspects ``result.columns`` to guess a schema — it
  reads the values by name (``row["full_name"]``, ``row["avg_pts"]``, …).
  Templates that need different column names should ship their own
  formatter in a later phase.
* The composer never builds SQL and never touches the warehouse; all
  numbers in the answer are grounded in ``result.rows``. The caller
  (route layer) is responsible for surface-level guarantees (template
  validation, allowlists, row caps).
* ``reasoning_summary`` is a short human-readable description of what was
  done — *not* the model's chain-of-thought. The pipeline never emits CoT;
  we only ever report our own structured summary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from chat_server.db import QueryResult
from chat_server.templates import Template


@dataclass(frozen=True)
class Citation:
    """One provenance citation attached to a composed answer.

    Attributes
    ----------
    table_name
        Warehouse table the answer draws on. One `Citation` per table is
        typical; rare per-metric or per-gap citations land in Phase 4.
    metric_key
        Set when the answer cites a specific ``meta_metric_definition``
        row. Empty for Phase 3.
    gap_key
        Set when the answer surfaces a known warehouse gap
        (``meta_known_gap``). Empty for Phase 3.
    """

    table_name: str | None = None
    metric_key: str | None = None
    gap_key: str | None = None


@dataclass
class ComposedAnswer:
    """The composer's output: a grounded answer plus citations + flags.

    Attributes
    ----------
    answer
        Concise human-readable answer text. Always non-empty for the
        happy path; ``not_answerable_note`` carries the message for the
        explicit not-answerable path (and ``answer`` mirrors it for
        storage symmetry — the persisted assistant message is the note).
    citations
        Provenance citations for the tables / metrics / gaps the answer
        draws on. May be empty for clarification / not-answerable paths.
    not_answerable
        True iff the turn was a transparent not-answerable response.
    not_answerable_note
        The original ``note`` from the not-answerable path. ``None`` for
        happy-path answers.
    reasoning_summary
        Short human-readable summary (NOT model CoT). Describes what
        happened — e.g. "ranked 5 rows by avg_pts desc".
    """

    answer: str
    citations: list[Citation] = field(default_factory=list)
    not_answerable: bool = False
    not_answerable_note: str | None = None
    reasoning_summary: str | None = None


# --- public API ----------------------------------------------------------


def compose(
    template: Template,
    result: QueryResult,
    plan_template_id: str,
) -> ComposedAnswer:
    """Convert ``result`` into a grounded answer per ``template.answer_policy``.

    The ``plan_template_id`` is accepted for symmetry/logging — every
    Phase 3 template is registered under a stable id so we don't use it
    to switch behaviour here, but downstream code (Phase 4 streaming) may.

    Parameters
    ----------
    template
        The template whose SQL was executed to produce ``result``. Only
        ``template.title``, ``template.answer_policy``, and
        ``template.allowed_tables`` are read.
    result
        The ``QueryResult`` from the runner (columns + rows + row_count).
    plan_template_id
        The ``template_id`` from the agent's ``QueryPlan``. Must equal
        ``template.template_id``; we don't enforce that here so the
        composer stays policy-free.

    Returns
    -------
    ComposedAnswer
        The answer text, plus one ``Citation`` per allowlisted table.
    """
    del plan_template_id  # unused in Phase 3; reserved for Phase 4 per-template enrichment
    answer = _format_empty(template) if not result.rows else _dispatch(template, result)
    citations = [Citation(table_name=t) for t in sorted(template.allowed_tables)]
    return ComposedAnswer(
        answer=answer,
        citations=citations,
        reasoning_summary=_reasoning_summary_for(template, result),
    )


def compose_not_answerable(
    note: str,
    attempted_sql: str | None = None,
) -> ComposedAnswer:
    """Build a transparent not-answerable-with-evidence answer.

    The caller passes the original ``note`` (e.g. "no template fits",
    "params invalid", "warehouse missing column X"); we wrap it so the
    persisted assistant message and the UI's not-answerable surface
    share a single source of truth.

    Parameters
    ----------
    note
        The human-readable reason. Echoed into ``answer`` so the JSONL
        session log carries the full message.
    attempted_sql
        Optional SQL the caller attempted (rendered from a template).
        When set, surfaces in ``reasoning_summary`` so the UI can
        render a "here's what we tried" disclosure without exposing the
        model's chain-of-thought.

    Returns
    -------
    ComposedAnswer
        ``not_answerable=True``; ``answer`` and ``not_answerable_note``
        both equal ``note``; ``reasoning_summary`` is a brief description
        of what was attempted (or ``None`` if no SQL was attached).
    """
    reasoning: str | None = None
    if attempted_sql:
        reasoning = f"Not answerable: {note}. Attempted SQL: {attempted_sql}"
    return ComposedAnswer(
        answer=note,
        citations=[],
        not_answerable=True,
        not_answerable_note=note,
        reasoning_summary=reasoning,
    )


# --- internal dispatch ---------------------------------------------------


def _dispatch(template: Template, result: QueryResult) -> str:
    """Route to the per-policy formatter; fall back to the generic one."""
    policy = template.answer_policy
    if policy == "ranked_list":
        return _format_ranked_list(template, result)
    if policy == "single_value":
        return _format_single_value(template, result)
    if policy == "count":
        return _format_count(template, result)
    return _format_generic(template, result)


def _format_empty(template: Template) -> str:
    """Stable text for a zero-row result so the answer is never empty."""
    del template  # unused
    return "No rows matched the query."


def _format_ranked_list(template: Template, result: QueryResult) -> str:
    """Top-N list format with one-line per row.

    Picks the first five rows (or fewer) and joins them with ", ". If
    more rows exist, an ellipsis ("…") is appended to make the cut-off
    obvious to readers. Every number is sourced from ``result.rows``;
    missing fields degrade gracefully to a name-only entry.
    """
    top_n = 5
    rows = result.rows[:top_n]
    total = len(result.rows)
    head = f"{total} result{'s' if total != 1 else ''} for {template.title}"
    parts = [_format_row_compact(row) for row in rows]
    body = ", ".join(parts)
    if total > top_n:
        body += ", …"
    return f"{head}: {body}."


def _format_row_compact(row: dict[str, Any]) -> str:
    """Compact "Name (season, X.X)" representation for one ranked row.

    Field selection prefers the names used by the Phase 1 50-40-90
    template; other templates degrade to "name" → whatever the row
    supplies under common aliases. Values are rounded to one decimal
    for readability; the full precision is preserved in the result
    payload (and visible in the table panel).
    """
    name = row.get("full_name") or row.get("player_name") or row.get("name") or "?"
    season = row.get("season_year") or row.get("season")
    pts = row.get("avg_pts") or row.get("ppg") or row.get("points")
    fragments: list[str] = []
    if season:
        fragments.append(str(season))
    if pts is not None:
        fragments.append(f"{_fmt_num(pts)} PPG")
    if fragments:
        return f"{name} ({', '.join(fragments)})"
    return str(name)


def _format_single_value(template: Template, result: QueryResult) -> str:
    """``"<column> = <value>"`` for a one-row result. Template unused."""
    del template  # unused
    row = result.rows[0]
    col = result.columns[0]
    return f"{col} = {row[col]}"


def _format_count(template: Template, result: QueryResult) -> str:
    """Plain row-count answer."""
    del template  # unused
    n = len(result.rows)
    return f"{n} matching row{'s' if n != 1 else ''}."


def _format_generic(template: Template, result: QueryResult) -> str:
    """Fallback for unknown policies. Lists columns so the reader knows the shape."""
    del template  # unused
    n = len(result.rows)
    if n == 0:
        return "No rows returned."
    cols = ", ".join(result.columns)
    return f"Returned {n} row{'s' if n != 1 else ''} (columns: {cols})."


def _fmt_num(value: Any) -> str:
    """Render a number compactly: one decimal for floats, plain str for ints/strs."""
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def _reasoning_summary_for(template: Template, result: QueryResult) -> str:
    """One-line description of what the runner did (never model CoT)."""
    parts = [
        f"template={template.template_id}",
        f"policy={template.answer_policy}",
        f"rows={result.row_count}",
    ]
    if result.truncated:
        parts.append("truncated=true")
    return " ".join(parts)


__all__ = [
    "Citation",
    "ComposedAnswer",
    "compose",
    "compose_not_answerable",
]
