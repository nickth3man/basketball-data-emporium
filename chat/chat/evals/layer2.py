"""Layer 2 — result grading (EVALS.md §1).

Layer 2 asserts that every gold key value appears somewhere in the
result set the plan produced. It runs against the warehouse snapshot the
orchestrator curated (gold_sql + gold_key_values) — never against
Wikipedia. The suite is gated on gold population; rows whose gold is
empty are SKIPPED, not failed.

Matching rules (EVALS.md §1 verbatim)
--------------------------------------
* Names are matched case-insensitively after normalization (strip +
  collapse internal whitespace). The same normalization is applied to
  BOTH the gold value and each candidate cell so a gold "LeBron James"
  matches a cell "  lebron  JAMES".
* Numbers match to the precision given in the gold (a gold of ``30.1``
  accepts ``30.12`` — i.e. ``abs(actual - gold_rounded) < 0.5 * 10^-p``
  where ``p`` is the number of decimals in the gold).
* Ordered golds (prefixed ``ordered:``) assert set membership by
  default and order only when prefixed ``ordered:``.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Protocol, cast

from .loader import EvalRow


class _QueryResultLike(Protocol):
    columns: list[str]
    rows: list[dict]


@dataclass(frozen=True)
class Layer2Result:
    """Outcome of grading one result set against one row's gold values.

    Attributes
    ----------
    pass_
        ``True`` iff every required gold value was found in the result
        set. Aliased to ``pass`` (the spec keyword) via ``__getattr__``.
    matched
        The gold values that were found (in gold order). Useful for
        diagnostics in the report.
    missing
        The gold values that were NOT found (in gold order).
    skipped
        ``True`` when the row has no pinned gold (orchestrator has not
        run the snapshot for it yet).
    reason
        Human-readable one-liner; used by the report.
    """

    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    skipped: bool = False
    reason: str = ""
    _pass: bool = False

    @property
    def passed(self) -> bool:
        """Boolean verdict (success). Use this name to avoid clashing with ``pass``."""
        return self._pass

    def __getattr__(self, name: str):  # pragma: no cover - shim only
        # Spec asks for ``pass``; ``dataclasses`` would conflict with
        # the Python keyword, so we expose a shim.
        if name == "pass":
            return self._pass
        raise AttributeError(name)


# -- normalisation --------------------------------------------------------


_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_name(value: object) -> str:
    """Normalise a string-ish value for case-insensitive name matching.

    Strip + collapse internal whitespace runs to a single space + lower
    case. Non-string values pass through repr() so the matcher still
    works on numbers / booleans (those go through the numeric path
    instead).
    """
    if not isinstance(value, str):
        return "" if value is None else str(value)
    return _WHITESPACE_RE.sub(" ", value.strip()).lower()


# -- gold parsing ---------------------------------------------------------


def parse_gold(raw: str) -> tuple[bool, list[str]]:
    """Parse a ``gold_key_values`` cell into ``(ordered, values)``.

    The cell format is a ``|``-separated list of normalized strings
    (see ``snapshot_golds.py`` for the producer side). An empty cell
    means "no gold pinned" -- the caller should SKIP rather than fail.

    The ``ordered:`` prefix is documented in EVALS.md §1: by default the
    grader asserts set membership (order doesn't matter); when the cell
    is prefixed ``ordered:`` the grader additionally checks that the
    matched values appear in the result set in the same order.

    Returns ``(ordered, values)`` where ``values`` is the (possibly
    empty) list of trimmed, non-empty gold tokens. ``ordered`` is True
    iff the prefix was present.
    """
    text = (raw or "").strip()
    if not text:
        return False, []
    ordered = text.startswith("ordered:")
    if ordered:
        text = text[len("ordered:") :].strip()
    values = [token.strip() for token in text.split("|") if token.strip()]
    return ordered, values


# -- numeric matching -----------------------------------------------------


_DECIMAL_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def _is_int_like(token: str) -> bool:
    return bool(re.fullmatch(r"-?\d+", token))


def _decimals_of(token: str) -> int:
    """Return the number of decimal digits in a numeric token.

    Returns 0 for integers (a gold of ``30`` accepts any value whose
    rounded-to-int equals 30, so the precision there is 0). Non-numeric
    tokens return 0 by convention so the caller falls back to string
    matching.
    """
    if "." not in token:
        return 0
    head, _, tail = token.partition(".")
    if not head.lstrip("-").isdigit() or not tail.isdigit():
        return 0
    return len(tail)


def _numeric_matches(gold_token: str, candidate: object) -> bool:
    """True iff ``candidate`` matches ``gold_token`` to the gold's precision.

    Strategy: parse both as floats, then compare with a tolerance of
    ``0.5 * 10^-p`` where ``p`` is the gold's decimal precision. This
    mirrors the spec's "30.1 accepts 30.12" example.
    """
    if candidate is None:
        return False
    if isinstance(candidate, bool):
        return False
    if isinstance(candidate, (int, float)):
        actual = float(candidate)
    else:
        text = str(candidate).strip()
        if not _DECIMAL_RE.match(text):
            return False
        actual = float(text)
    try:
        gold_val = float(gold_token)
    except ValueError:
        return False
    p = _decimals_of(gold_token)
    tol = 0.5 * (10.0**-p) if p > 0 else 0.5
    return abs(actual - gold_val) < tol


# -- result-set helpers ---------------------------------------------------


def _flatten_cells(columns: list[str], rows: list[dict]) -> list[object]:
    """Flatten ``(columns, rows)`` into a flat list of cell values.

    Order: row-major, column-minor (matches the visual order of a
    table). One entry per cell, no column-name metadata; the matcher
    doesn't need it.
    """
    out: list[object] = []
    for row in rows:
        for col in columns:
            if col in row:
                out.append(row[col])
    return out


def _flatten_name_index(columns: list[str], rows: list[dict]) -> list[str]:
    """Flatten string cells (normalised) -- the name-match index."""
    cells = _flatten_cells(columns, rows)
    return [_normalize_name(c) for c in cells if isinstance(c, str)]


def _flatten_number_cells(columns: list[str], rows: list[dict]) -> list[object]:
    """Flatten numeric cells (preserving type) -- the number-match index."""
    out: list[object] = []
    for row in rows:
        for col in columns:
            v = row.get(col)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append(v)
            elif isinstance(v, str) and _DECIMAL_RE.match(v.strip()):
                out.append(v.strip())
    return out


def _flatten_string_cells(columns: list[str], rows: list[dict]) -> list[str]:
    """Flatten string cells (raw, not normalised) -- the ordered-match index."""
    out: list[str] = []
    for row in rows:
        for col in columns:
            v = row.get(col)
            if isinstance(v, str):
                out.append(v)
    return out


# -- grader ---------------------------------------------------------------


def _partition_golds(
    golds: list[str],
    name_index: list[str],
    number_cells: list[object],
    rows: list[dict],
    columns: list[str],
) -> tuple[list[str], list[str]]:
    matched: list[str] = []
    missing: list[str] = []
    for gold in golds:
        target = matched if _gold_in_set(gold, name_index, number_cells, rows, columns) else missing
        target.append(gold)
    return matched, missing


def _result_reason(golds: list[str], missing: list[str], passed_order: bool, ordered: bool) -> str:
    if missing:
        return f"missing {len(missing)} of {len(golds)} gold values: {missing[:3]}"
    if not passed_order:
        return "all gold values present but order not preserved"
    suffix = " (order preserved)" if ordered else ""
    return f"all {len(golds)} gold values matched{suffix}"


def grade_result(
    rows: list[dict],
    columns: list[str],
    row: EvalRow,
) -> Layer2Result:
    """Grade ``rows`` (columns + values) against ``row.gold_key_values``.

    Returns a ``Layer2Result`` whose ``skipped`` is True when the row
    has no gold pinned -- callers should treat skipped as a non-result
    (don't include in pass-rate denominators).

    The matching order for each gold value is:

    1. Numeric match (if the gold token parses as a number).
    2. Case-insensitive normalised name match (if either side is a
       string).
    3. Exact-string fallback.

    For ``ordered:`` golds the matched values must appear in the
    result-set cell stream in the same order; this is enforced AFTER
    membership is confirmed so a partial match is still a failure.
    """
    ordered, golds = parse_gold(row.gold_key_values)
    if not golds:
        return Layer2Result(
            matched=[],
            missing=[],
            skipped=True,
            reason="no gold_key_values pinned; skipping",
            _pass=True,
        )

    if not rows:
        return Layer2Result(
            matched=[],
            missing=list(golds),
            skipped=False,
            reason="empty result set; no gold values can match",
            _pass=False,
        )

    name_index = _flatten_name_index(columns, rows)
    num_cells = _flatten_number_cells(columns, rows)
    string_cells = _flatten_string_cells(columns, rows)

    matched, missing = _partition_golds(golds, name_index, num_cells, rows, columns)

    passed_membership = not missing
    passed_order = True
    if ordered and passed_membership:
        passed_order = _ordered_preserved(
            matched, string_cells, name_index, num_cells, rows, columns
        )

    return Layer2Result(
        matched=matched,
        missing=missing,
        skipped=False,
        reason=_result_reason(golds, missing, passed_order, ordered),
        _pass=passed_membership and passed_order,
    )


def _gold_in_set(
    gold: str,
    name_index: list[str],
    num_cells: list[object],
    rows: list[dict],
    columns: list[str],
) -> bool:
    """True iff ``gold`` matches any cell in the result set."""
    # 1. Numeric path.
    if _DECIMAL_RE.match(gold):
        for cell in num_cells:
            if _numeric_matches(gold, cell):
                return True
    # 2. Name (case-insensitive whitespace-folded) path.
    needle = _normalize_name(gold)
    if needle and needle in name_index:
        return True
    # 3. Exact-string fallback (handles non-normalised punctuation tokens).
    for cell in _flatten_cells(columns, rows):
        if not isinstance(cell, str):
            continue
        if cell == gold:
            return True
    return False


def _ordered_preserved(
    matched: list[str],
    string_cells: list[str],
    name_index: list[str],
    num_cells: list[object],
    rows: list[dict],
    columns: list[str],
) -> bool:
    """Walk the result-set cell stream and verify matched golds appear in order.

    For each gold token we greedily consume the first cell that matches
    it. If a later gold's match appears earlier in the stream than the
    previous gold's match, the order check fails.
    """
    del name_index, num_cells  # name/number checks already validated membership
    cursor = 0
    flat = string_cells
    # For non-string golds (purely numeric) we still need to scan
    # numeric cells; build a unified cell stream for the order check.
    if any(_DECIMAL_RE.match(g) for g in matched):
        flat = [_stringify(c) for c in _flatten_cells(columns, rows)]
    for gold in matched:
        idx = _find_match_in_stream(flat, gold, cursor)
        if idx is None:
            return False
        cursor = idx + 1
    return True


def _find_match_in_stream(stream: list[str], gold: str, start: int) -> int | None:
    """Find the first stream cell at or after ``start`` matching ``gold``."""
    needle = _normalize_name(gold)
    for i in range(start, len(stream)):
        cell = stream[i]
        if _DECIMAL_RE.match(gold) and _DECIMAL_RE.match(cell) and _numeric_matches(gold, cell):
            return i
        if needle and _normalize_name(cell) == needle:
            return i
        if cell == gold:
            return i
    return None


def _stringify(value: object) -> str:
    """Render a cell value as a string for the order-check stream."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


# -- SQL execution helper -------------------------------------------------


def execute_plan_sql(sql: str, db) -> tuple[list[str], list[dict]]:
    """Run ``sql`` via the read-only DuckDB singleton.

    Thin async wrapper around ``DuckDBSingleton.execute`` so the replay
    layer can capture the (columns, rows) tuple the Layer-2 grader
    expects. Returns ``(columns, rows)`` where ``rows`` is a list of
    ``{column: value}`` dicts (DuckDB converts BigInt to Python int via
    ``convert_rows``).

    Any exception (parse error, dry-run failure, runtime error) is
    re-raised so the caller can attribute the failure to the SQL, not
    to the grader.
    """
    coro = db.execute(sql)
    result = cast(
        _QueryResultLike,
        asyncio.run(coro) if asyncio.iscoroutine(coro) else coro,
    )
    columns, rows = list(result.columns), list(result.rows)
    return columns, rows


__all__ = [
    "Layer2Result",
    "grade_result",
    "parse_gold",
    "execute_plan_sql",
]
