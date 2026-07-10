"""Answer composer: turns a `QueryResult` into a concise grounded answer.

The composer's contract is small on purpose:

* ``compose_governed(result_contract, result, sql, model_name)`` —
  governed-SQL happy-path formatter. Dispatches on
  ``ResultContract.answer_style`` (``ranked_list``, ``single_value``,
  ``count``, ``prose``, ``table``).
* ``compose_not_answerable(note, attempted_sql=None)`` — explicit
  "I could not answer this, here's the evidence" path.

Design notes
------------
* ``answer_style`` drives the dispatch. Adding a new style means adding a
  branch here and a test in ``chat_tests/test_composer.py``.
* The composer never inspects ``result.columns`` to guess a schema — it
  reads the values by name.
* The composer never builds SQL and never touches the warehouse; all
  numbers in the answer are grounded in ``result.rows``. The caller
  (route layer) is responsible for surface-level guarantees (the
  validation, allowlists, row caps).
* ``reasoning_summary`` is a short human-readable description of what was
  done — *not* the model's chain-of-thought. The pipeline never emits CoT;
  we only ever report our own structured summary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from chat_server.agent import ResultContract
from chat_server.db import QueryResult


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


def compose_governed(
    result_contract: ResultContract,
    result: QueryResult,
    sql: str,
    model_name: str | None = None,
    question_interpretation: str = "",
) -> ComposedAnswer:
    """Convert a governed-SQL ``QueryResult`` into a grounded answer.

    Mirrors :func:`compose` -- the pipeline can consume either return
    value uniformly. The dispatch is on
    :attr:`ResultContract.answer_style` rather than on
    :attr:`Template.answer_policy`; the legacy policy formatters
    (``ranked_list`` / ``single_value`` / ``count``) are reused where
    they fit by passing a thin shim whose only attribute is ``title``.
    New answer styles introduced by the governed path (``prose``,
    ``table``) get dedicated formatters.

    Parameters
    ----------
    result_contract
        What the agent said the query would return (``grain``,
        ``columns``, ``row_limit``, ``answer_style``). Only
        ``grain`` and ``answer_style`` drive the dispatch; ``columns``
        is informational.
    result
        The ``QueryResult`` from the runner (same shape as the legacy
        caller's ``result``).
    sql
        The SQL that produced the result. Surfaced as provenance in
        ``reasoning_summary`` -- mirrors how
        :func:`compose_not_answerable` records ``attempted_sql``.
    model_name
        Optional semantic-model name (e.g. ``"player_season"``). When
        supplied, it appears in ``answer`` text (where the style
        supports prose detail) and as a :class:`Citation` whose
        ``table_name`` carries it -- the legacy cite-by-table-name
        pattern extended with a model-name fallback for governed
        answers.
    question_interpretation
        The agent's plain-English reading of the user's question
        (``QueryPlan.question_interpretation``). When non-empty, it is
        prepended to the answer as a transparent preamble so the user
        sees how the agent interpreted subjective terms (e.g. what
        "similar" was taken to mean) and can redirect if it doesn't
        match their intent. Empty string adds no preamble.

    Returns
    -------
    ComposedAnswer
        Always non-empty (with a graceful "no data" message for empty
        ``result.rows``); one :class:`Citation` per known provenance
        source; ``reasoning_summary`` carries the rendered SQL plus a
        short run summary (``model=... style=... rows=N``).
    """
    preamble = _interpretation_preamble(question_interpretation)
    if not result.rows:
        return ComposedAnswer(
            answer=preamble + "No data returned.",
            citations=[Citation(table_name=model_name)] if model_name else [],
            reasoning_summary=_governed_reasoning_summary(
                result_contract, result, sql, model_name, note="empty result"
            ),
        )

    title = result_contract.grain or _fallback_title(model_name)
    answer = _dispatch_governed(result_contract, title, result, model_name)
    citations = [Citation(table_name=model_name)] if model_name else []
    return ComposedAnswer(
        answer=preamble + answer,
        citations=citations,
        reasoning_summary=_governed_reasoning_summary(result_contract, result, sql, model_name),
    )


def _interpretation_preamble(question_interpretation: str) -> str:
    """Render the agent's interpretation as a lead-in to the answer.

    Returns ``""`` when the interpretation is empty/whitespace so the
    common case (no subjective term, nothing to surface) adds no noise.
    Otherwise produces a single line followed by a blank-line separator
    so the data body reads cleanly below it. The phrasing is deliberately
    neutral ("I read your question as...") so the user knows this is the
    agent's reading and can correct it on the next turn.
    """
    text = (question_interpretation or "").strip()
    if not text:
        return ""
    return f"I read your question as: {text}\n\n"


def _dispatch_governed(
    result_contract: ResultContract,
    title: str,
    result: QueryResult,
    model_name: str | None,
) -> str:
    """Route to the per-style formatter; fall back to the generic one.

    Dispatches on :attr:`ResultContract.answer_style`. Reuses the
    legacy per-policy formatters via a thin ``title`` shim for the
    styles that map 1:1 (``ranked_list`` / ``single_value`` /
    ``count``); the governed-only styles (``prose`` / ``table``) get
    dedicated formatters; anything unrecognized falls back to the
    legacy ``_format_generic`` helper.
    """
    style = result_contract.answer_style
    if style == "ranked_list":
        return _format_ranked_list(title, result)
    if style == "single_value":
        return _format_single_value(result)
    if style == "count":
        return _format_count(result)
    if style == "prose":
        return _format_prose(result_contract, result, model_name)
    if style == "table":
        return _format_table(result_contract, result, model_name)
    return _format_generic(result)


def _format_prose(
    result_contract: ResultContract,
    result: QueryResult,
    model_name: str | None,
) -> str:
    """Prose-style answer: a short paragraph summarising the result.

    Surfaces the ``grain``, row count, optional sample column list,
    and the model name when known. If only one row is present and a
    single column exists, the prose collapses to ``"<col> = <value>"``-
    style text (mirrors :func:`_format_single_value`'s intent without
    the exact-phrase overlap so callers can branch on style).
    """
    n = len(result.rows)
    cols = (
        ", ".join(result_contract.columns)
        if result_contract.columns
        else (", ".join(result.columns) if result.columns else "")
    )
    head = f"{result_contract.grain or 'Results'}"
    if model_name:
        head = f"{head} (from the {model_name} semantic model)"
    sample = f" sample columns: {cols}" if cols else ""
    return f"{head}: {n} row{'s' if n != 1 else ''}{sample}."


def _format_table(
    result_contract: ResultContract,
    result: QueryResult,
    model_name: str | None,
) -> str:
    """Table-style answer: a compact "returned N rows (columns: ...)" summary.

    Distinct from :func:`_format_generic` only by the explicit style
    assertion at the start of the answer text, so the UI can tell a
    table-style answer apart from a generic fallback that happens to
    be in tabular form.
    """
    n = len(result.rows)
    cols = (
        ", ".join(result_contract.columns) if result_contract.columns else ", ".join(result.columns)
    )
    head = f"Table (rows={n}"
    if model_name:
        head = f"{head}, model={model_name}"
    head = f"{head}): {n} row{'s' if n != 1 else ''}"
    return f"{head} (columns: {cols})." if cols else f"{head}."


def _governed_reasoning_summary(
    result_contract: ResultContract,
    result: QueryResult,
    sql: str,
    model_name: str | None,
    *,
    note: str | None = None,
) -> str:
    """One-line description of the governed run; carries SQL provenance.

    Mirrors :func:`_reasoning_summary_for`'s field shape (``model=``,
    ``style=``, ``rows=``) so the legacy audit-trail format stays
    additive. ``sql=`` is appended as a short prefix (180 chars) --
    keeps ``reasoning_summary`` short while still exposing provenance
    to the assertion in the Stage 3.3a test suite. The optional
    ``note`` is appended when present (e.g. ``"empty result"``).
    """
    parts = [
        f"model={model_name or 'unknown'}",
        f"style={result_contract.answer_style}",
        f"rows={result.row_count}",
    ]
    if result.truncated:
        parts.append("truncated=true")
    summary = " ".join(parts)
    if sql:
        summary = f"sql={sql[:180]} {summary}"
    if note:
        summary = f"{summary} note={note}"
    return summary


def _fallback_title(model_name: str | None) -> str:
    """Title used when ``ResultContract.grain`` is empty."""
    return f"results from the {model_name} semantic model" if model_name else "governed query"


def _format_ranked_list(title: str, result: QueryResult) -> str:
    """Top-N list format with one-line per row.

    Picks the first five rows (or fewer) and joins them with ", ". If
    more rows exist, an ellipsis ("…") is appended to make the cut-off
    obvious to readers. Every number is sourced from ``result.rows``;
    missing fields degrade gracefully to a name-only entry.
    """
    top_n = 5
    rows = result.rows[:top_n]
    total = len(result.rows)
    head = f"{total} result{'s' if total != 1 else ''} for {title}"
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


def _format_single_value(result: QueryResult) -> str:
    """``"<column> = <value>"`` for a one-row result. Template unused."""
    row = result.rows[0]
    col = result.columns[0]
    return f"{col} = {row[col]}"


def _format_count(result: QueryResult) -> str:
    """Plain row-count answer."""
    n = len(result.rows)
    return f"{n} matching row{'s' if n != 1 else ''}."


def _format_generic(result: QueryResult) -> str:
    """Fallback for unknown policies. Lists columns so the reader knows the shape."""
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


__all__ = [
    "Citation",
    "ComposedAnswer",
    "compose_governed",
    "compose_not_answerable",
]
