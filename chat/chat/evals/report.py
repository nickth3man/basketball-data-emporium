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

from .layer1 import Layer1Result
from .layer2 import Layer2Result


def _pct(num: int, denom: int) -> str:
    """Render ``num/denom`` as a percentage string (``"--"`` when denom is 0)."""
    if denom == 0:
        return "--"
    return f"{(100.0 * num / denom):.1f}%"


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

    total = len(l1)
    hard_fails = sum(1 for r in l1 if r.hard_fail)
    warns = sum(1 for r in l1 if r.warn)
    over_clarify = sum(1 for r in l1 if r.over_clarify_fail)

    # Mode accuracy = fraction of rows where the mode matched the
    # preferred one (i.e. NOT a hard-fail and NOT a warn). Matches the
    # spec's "mode accuracy = fraction where mode==expected (ignoring
    # warn-only)" -- a row in ``warn`` is a drift signal even though
    # it's still in the acceptable set.
    mode_accurate = total - hard_fails - warns

    tables_checked = [r for r in l1 if r.tables_check in {"pass", "fail"}]
    tables_passed = sum(1 for r in tables_checked if r.tables_check == "pass")

    gold_total = sum(1 for r in l2 if not r.skipped)
    gold_passed = sum(1 for r in l2 if (not r.skipped) and r.passed)

    # Step 1 placeholders. The schema is fixed so step 5 / step 6 just
    # fill these in -- no downstream parser changes needed.
    repair_invocation_rate = "n/a — lands step 5"
    query_ref_source_split = "n/a — lands step 6"

    fields = [
        f"mode_accuracy={_pct(mode_accurate, total)}",
        f"warn_rate={_pct(warns, total)}",
        f"over_clarify_count={over_clarify}",
        f"table_accuracy={_pct(tables_passed, len(tables_checked))}",
        f"gold_pass_rate={_pct(gold_passed, gold_total)}",
        f"repair_invocation_rate={repair_invocation_rate}",
        f"query_ref_source_split={query_ref_source_split}",
    ]
    line = ", ".join(fields)
    print(line, file=sys.stdout)
    return line


__all__ = ["build_report"]
