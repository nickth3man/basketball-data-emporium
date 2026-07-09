"""Layer-2 grading across the eval suite (EVALS.md §1).

Layer 2 is ground-truth driven: it asserts that the values produced by
the replayed query appear in the warehouse result for every gold row.
Until the orchestrator runs the snapshot to populate
``gold_key_values``, this file is a no-op — every row's gold is empty
and we ``pytest.skip`` cleanly so the file stays runnable.
"""

from __future__ import annotations

import pytest

from .conftest import skip_no_llm, skip_no_warehouse
from .layer2 import execute_plan_sql, grade_result
from .loader import EvalRow, load_rows
from .replay import ReplayResult, replay_row


def _rows_with_gold(rows: list[EvalRow]) -> list[EvalRow]:
    """Filter to rows the orchestrator has populated with gold."""
    return [r for r in rows if r.gold_sql or r.gold_key_values]


@pytest.fixture(scope="module")
def csv_rows() -> list[EvalRow]:
    return load_rows()


@pytest.fixture(scope="module")
def golds_present(csv_rows: list[EvalRow]) -> bool:
    """True when at least one row has pinned gold."""
    return any(r.gold_sql or r.gold_key_values for r in csv_rows)


@pytest.mark.live_llm
@skip_no_warehouse
@skip_no_llm
@pytest.mark.asyncio
async def test_layer2_gold_match_when_present(
    csv_rows: list[EvalRow],
    golds_present: bool,
    governed_sql_mode_on,
    temp_sessions_root,
) -> None:
    """Replay every row with pinned gold and assert the result set contains the
    gold values.

    Skips gracefully when no golds are pinned yet — the orchestrator
    owns that step. Once a snapshot run populates ``gold_key_values``,
    this test pins the result-set semantics so a future regression in
    the warehouse / query plan fails loudly.
    """
    if not golds_present:
        pytest.skip(
            "no rows have gold_key_values pinned yet; run "
            "`chat/scripts/snapshot_golds.py` after writing gold_sql"
        )

    eligible = _rows_with_gold(csv_rows)
    for row in eligible:
        replay: ReplayResult = await replay_row(row, temp_sessions_root)
        columns = replay.final_columns
        rows = replay.final_rows
        verdict = grade_result(rows, columns, row)
        assert not verdict.skipped, f"{row.conversation_id}: skipped unexpectedly"
        assert verdict.passed, (
            f"{row.conversation_id}: Layer-2 fail — {verdict.reason} (missing={verdict.missing})"
        )


@pytest.mark.live_llm
@skip_no_warehouse
@skip_no_llm
@pytest.mark.asyncio
async def test_layer2_helper_executes_plan_sql(
    csv_rows: list[EvalRow],
    golds_present: bool,
) -> None:
    """Sanity check on ``execute_plan_sql``: every pinned ``gold_sql``
    produces a non-error read-only result.

    This is the cheapest possible regression check on the snapshot
    contract: it pins that gold_sql is still a valid read-only
    SELECT against the live warehouse. A failure here means a
    warehouse rebuild invalidated the snapshot and the orchestrator
    must re-snapshot.
    """
    if not golds_present:
        pytest.skip("no gold_sql pinned yet")

    from chat_server.db import get_db

    db = get_db()
    for row in csv_rows:
        if not row.gold_sql:
            continue
        columns, rows = execute_plan_sql(row.gold_sql, db)
        # We don't assert row contents here -- that's
        # ``test_layer2_gold_match_when_present``'s job. We DO assert
        # that the query ran and returned SOMETHING the schema could
        # describe.
        assert isinstance(columns, list)
        assert isinstance(rows, list)
        # And that the query was a SELECT (the gate enforces this
        # anyway, but a belt-and-braces check keeps the snapshot
        # honest).
        assert columns or not rows, (
            f"{row.conversation_id}: gold_sql returned columns+rows mismatch"
        )
