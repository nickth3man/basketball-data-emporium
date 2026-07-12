"""Report builder (EVALS.md §5).

Produces the one-line report the suite prints at the end of every run.
The line carries every metric the spec calls out so the nightly run
JSON-diff against the previous baseline lands cleanly:

    mode_accuracy, warn_rate, over_clarify_count (target 0),
    table_accuracy, gold_pass_rate, repair_invocation_rate,
    query_ref_source_split (catalog|warehouse)

For step 1 (this step) the last two are placeholders -- they belong to
later migration steps (5 and 6 respectively). The report keeps the
fields so the schema is stable across steps and downstream parsers
don't have to special-case the early runs.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from dataclasses import dataclass

from .layer1 import Layer1Result
from .layer2 import Layer2Result


def _pct(num: int, denom: int) -> str:
    """Render ``num/denom`` as a percentage string (``"--"`` when denom is 0)."""
    if denom == 0:
        return "--"
    return f"{(100.0 * num / denom):.1f}%"


@dataclass(frozen=True)
class _ReportCounts:
    total: int
    mode_accurate: int
    warns: int
    over_clarify: int
    tables_checked: int
    tables_passed: int
    gold_total: int
    gold_passed: int


def _count_results(
    layer1: list[Layer1Result],
    layer2: list[Layer2Result],
) -> _ReportCounts:
    hard_fails = sum(result.hard_fail for result in layer1)
    warns = sum(result.warn for result in layer1)
    table_results = [
        result.tables_check for result in layer1 if result.tables_check in {"pass", "fail"}
    ]
    graded_gold = [result for result in layer2 if not result.skipped]
    return _ReportCounts(
        total=len(layer1),
        mode_accurate=len(layer1) - hard_fails - warns,
        warns=warns,
        over_clarify=sum(result.over_clarify_fail for result in layer1),
        tables_checked=len(table_results),
        tables_passed=table_results.count("pass"),
        gold_total=len(graded_gold),
        gold_passed=sum(result.passed for result in graded_gold),
    )


def build_report(
    layer1_results: Iterable[Layer1Result],
    layer2_results: Iterable[Layer2Result] = (),
) -> str:
    """Build + print + return the single-line eval report.

    Parameters
    ----------
    layer1_results
        One ``Layer1Result`` per replayed row.
    layer2_results
        One ``Layer2Result`` per row with gold pinned (rows without gold
        contribute nothing to ``gold_pass_rate``).

    Returns
    -------
    str
        The rendered one-line report. Always 7 fields, comma-separated,
        so downstream parsers can split on ``,`` and never have to
        special-case the early-run placeholder values.
    """
    l1 = list(layer1_results)
    l2 = list(layer2_results)
    counts = _count_results(l1, l2)

    # Step 1 placeholders. The schema is fixed so step 5 / step 6 just
    # fill these in -- no downstream parser changes needed.
    repair_invocation_rate = "n/a — lands step 5"
    query_ref_source_split = "n/a — lands step 6"

    fields = [
        f"mode_accuracy={_pct(counts.mode_accurate, counts.total)}",
        f"warn_rate={_pct(counts.warns, counts.total)}",
        f"over_clarify_count={counts.over_clarify}",
        f"table_accuracy={_pct(counts.tables_passed, counts.tables_checked)}",
        f"gold_pass_rate={_pct(counts.gold_passed, counts.gold_total)}",
        f"repair_invocation_rate={repair_invocation_rate}",
        f"query_ref_source_split={query_ref_source_split}",
    ]
    line = ", ".join(fields)
    print(line, file=sys.stdout)
    return line


__all__ = ["build_report"]
