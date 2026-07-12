"""Layer 1 — plan grading (EVALS.md §1).

Layer 1 is deterministic: it grades the agent's plan OBJECT against the
row's expected mode + acceptable set. It runs first (cheapest) and gates
every other layer. It does NOT execute the plan's SQL — that's Layer 2.

Pipeline interface
------------------
``run_turn`` (chat_server.pipeline) does not surface the typed
``QueryPlan`` object in its events (the plan is internal to the
pipeline), so callers reconstruct the plan-equivalent facts we need
from the event stream before calling ``grade_plan``:

* ``mode`` -- derived from the event sequence (``ClarificationNeeded``
  present -> clarify; ``QueryStarted`` -> execute_sql; otherwise
  not_answerable).
* ``sql`` -- taken from the ``QueryStarted.sql`` payload.
* ``gate_pass`` -- ``validate_governed_sql(plan.sql, db, catalog).valid``
  for execute_sql plans; ``None`` for other modes.
* ``tables_referenced`` -- ``QueryStarted.query_ref.tables`` for execute_sql plans.

The grader is pure: no IO, no LLM, no event streaming. ``Layer1Result``
is the only return type so the report module can aggregate cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass

from .loader import EvalRow

#: Literal mode tokens used throughout the grader. Mirrors the
#: ``AnswerMode`` enum values for the modes that appear in
#: ``EvalRow.expected_answer_mode_turn1`` + ``acceptable_modes_turn1``.
_MODE_EXECUTE_SQL = "execute_sql"
_MODE_CLARIFY = "clarify"
_MODE_NOT_ANSWERABLE = "not_answerable"
_MODE_TEMPLATE = "template"  # v1 legacy bucket — outside the CSV's acceptable set


@dataclass(frozen=True)
class Layer1Result:
    """Outcome of grading one plan against one row.

    Attributes
    ----------
    mode
        The mode the grader attributed to the plan (``"execute_sql"`` /
        ``"clarify"`` / ``"not_answerable"`` / ``"template"``).
    hard_fail
        True iff assertion 1 OR assertion 4 from EVALS.md §1 tripped:
        the plan's mode is outside the row's acceptable set, OR the
        agent over-clarified on an execute_sql-only row.
    warn
        True iff assertion 2 tripped: the mode is acceptable but not the
        preferred one for this row. A warn does not flip ``hard_fail``.
    over_clarify_fail
        True iff assertion 4 tripped (a clarify plan on an execute_sql-only
        row). Tracked separately so the report can surface the
        "single most important metric" directly.
    tables_check
        One of ``"pass"``, ``"fail"``, ``"skipped"``. ``"skipped"`` when
        the row has no pinned ``expected_tables`` (still ``TODO_VERIFY``)
        so the assertion cannot fail on missing data. Only set for
        execute_sql plans.
    gate_pass
        True iff ``validate_governed_sql`` accepted the plan's SQL
        against the catalog. Only set for execute_sql plans; ``False``
        when the gate rejected the SQL.
    reason
        Human-readable summary; one short sentence so the report can
        embed it inline.
    """

    mode: str
    hard_fail: bool
    warn: bool
    over_clarify_fail: bool
    tables_check: str
    gate_pass: bool
    reason: str


def _normalise_mode(mode: str) -> str:
    """Coerce a raw mode string to the canonical lowercase token.

    The CSV uses lowercase tokens throughout (``execute_sql`` /
    ``clarify`` / ``not_answerable``); ``AnswerMode`` values are
    lowercase by default. Defensive: case-fold + strip so a future
    refactor that capitalises one side of the boundary doesn't silently
    break the grader.
    """
    return (mode or "").strip().lower()


def _grade_tables(
    mode: str,
    gate_pass: bool | None,
    tables_referenced: set[str] | None,
    row: EvalRow,
) -> str:
    """Grade the governed gate and expected-table intersection."""
    if mode != _MODE_EXECUTE_SQL:
        return "skipped"
    if gate_pass is False:
        return "fail"

    expected_tables_raw = (row.expected_tables or "").strip()
    if not expected_tables_raw or expected_tables_raw.upper().startswith("TODO_VERIFY"):
        return "skipped"

    expected_tables = {table.strip() for table in expected_tables_raw.split("|") if table.strip()}
    referenced_tables = tables_referenced or set()
    return "pass" if expected_tables & referenced_tables else "fail"


def grade_plan(plan: dict, row: EvalRow) -> Layer1Result:
    """Grade the plan object extracted from the pipeline's event stream.

    Parameters
    ----------
    plan
        Dict produced by the replay layer. Required keys:

        * ``mode`` (``str``) -- one of ``"execute_sql"`` / ``"clarify"``
          / ``"not_answerable"`` / ``"template"``.
        * ``sql`` (``str | None``) -- the rendered SQL for execute_sql
          + template plans; ``None`` for clarify / not_answerable.
        * ``gate_pass`` (``bool | None``) -- ``validate_governed_sql``
          verdict for execute_sql plans; ``None`` otherwise.
        * ``tables_referenced`` (``set[str] | None``) -- set extracted
          from the gate's ``ValidationReport``; ``None`` for non-SQL
          plans.

    row
        The CSV row this plan is being graded against.

    Returns
    -------
    Layer1Result
        Always populated; never raises. ``hard_fail`` OR ``warn`` (or
        both) is the expected signal; ``reason`` carries the rationale.
    """
    raw_mode = _normalise_mode(str(plan.get("mode") or ""))
    gate_pass = plan.get("gate_pass")
    tables_referenced = plan.get("tables_referenced")

    acceptable = {m.strip().lower() for m in row.acceptable_modes_turn1}
    expected = _normalise_mode(row.expected_answer_mode_turn1)

    # Assertion 4 (over-clarify guard): the CSV pins 37 rows to
    # execute_sql-only; a clarify plan on those rows is the "single most
    # important metric" per EVALS.md §1. We compute this BEFORE the
    # hard-fail check so the report can attribute the fail to either
    # bucket (acceptable-miss vs over-clarify) cleanly.
    over_clarify_fail = bool(acceptable == {_MODE_EXECUTE_SQL} and raw_mode == _MODE_CLARIFY)

    # Assertion 1: mode is in the row's acceptable set. ``TEMPLATE`` is
    # outside every CSV row's acceptable set by design (the v2 eval
    # universe is governed SQL + clarify + not_answerable), so any
    # template-mode plan is automatically a hard fail.
    in_acceptable = raw_mode in acceptable

    # Assertion 2: mode matches the preferred ``expected`` mode. Only
    # meaningful when the plan is still within the acceptable set; if
    # assertion 1 already tripped, the warn is noise.
    mode_matches_expected = bool(expected) and raw_mode == expected
    warn = bool(in_acceptable and expected and not mode_matches_expected)

    # Assertion 3 (execute_sql only): gate passes and the referenced tables
    # intersect the pinned expected set. Unpinned TODO rows are skipped.
    tables_check = _grade_tables(raw_mode, gate_pass, tables_referenced, row)

    # A gate failure on an execute_sql plan means the model tried to write
    # SQL but it didn't pass validation. When ``not_answerable`` is in the
    # acceptable modes, treat this as the model's inability to produce
    # valid SQL under current catalog coverage — not a hard fail.
    gate_fail_acceptable = "not_answerable" in acceptable
    hard_fail = (
        (not in_acceptable)
        or over_clarify_fail
        or (tables_check == "fail" and not gate_fail_acceptable)
    )

    reason = _build_reason(
        raw_mode=raw_mode,
        expected=expected,
        acceptable=acceptable,
        in_acceptable=in_acceptable,
        warn=warn,
        over_clarify=over_clarify_fail,
        tables_check=tables_check,
    )

    return Layer1Result(
        mode=raw_mode,
        hard_fail=hard_fail,
        warn=warn,
        over_clarify_fail=over_clarify_fail,
        tables_check=tables_check,
        gate_pass=bool(gate_pass) if gate_pass is not None else False,
        reason=reason,
    )


def _build_reason(
    *,
    raw_mode: str,
    expected: str,
    acceptable: set[str],
    in_acceptable: bool,
    warn: bool,
    over_clarify: bool,
    tables_check: str,
) -> str:
    """Render the single-line reason string for the report.

    Centralised so every failure mode produces a comparable phrase.
    """
    if over_clarify:
        return (
            f"over-clarify guard tripped: clarify on execute_sql-only row "
            f"(acceptable={sorted(acceptable)})"
        )
    if not in_acceptable:
        return f"mode {raw_mode!r} not in acceptable={sorted(acceptable)}"
    if tables_check == "fail":
        return "execute_sql plan failed the gate or table check"
    if warn:
        return f"mode {raw_mode!r} accepted but differs from expected {expected!r}"
    return f"mode {raw_mode!r} matches expected {expected!r}"


__all__ = ["Layer1Result", "grade_plan"]
