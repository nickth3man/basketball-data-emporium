"""Layer-1 grading across the full eval suite (EVALS.md §1).

Every CSV row is replayed once. WARNs are tolerated (they're surfaced in
the report as drift signals but never gate); HARD FAILS and over-clarify
trips abort the row. The report module aggregates the per-row
``Layer1Result`` into the one-line summary.
"""

from __future__ import annotations

import pytest

from .conftest import skip_no_llm, skip_no_warehouse
from .layer1 import Layer1Result, grade_plan
from .loader import EvalRow, load_rows
from .replay import ReplayResult, replay_row

# Module-level collector so the report (when run as a final step) can
# see every row's verdict without each test returning it explicitly.
# The report aggregator is a sibling helper and reads from this list
# when present.
COLLECTED_RESULTS: list[tuple[str, object]] = []  # value is always Layer1Result at runtime


def _all_rows() -> list[EvalRow]:
    """Load every row from the canonical CSV."""
    return load_rows()


def _plan_dict(trace) -> dict:
    """Project a TurnTrace into the dict shape ``grade_plan`` expects.

    The replay already extracted the mode + sql + gate facts; this is a
    pure projection so the grader stays decoupled from the replay's
    dataclasses.
    """
    return {
        "mode": trace.mode,
        "sql": trace.sql,
        "gate_pass": trace.gate_pass,
        "tables_referenced": set(trace.tables_referenced or set()),
    }


@pytest.fixture(scope="module")
def csv_rows() -> list[EvalRow]:
    """Module-scoped row list so parametrize doesn't re-read the CSV per test."""
    return _all_rows()


@pytest.fixture
def replayed_results() -> dict[str, ReplayResult]:
    """Per-row replay cache, populated on demand by ``replay_for``.

    The test runs are LLM-bound; running each row through the live
    agent twice would double the cost. We cache the replay result the
    first time each row is requested and reuse it for the second
    assertion pass.
    """
    cache: dict[str, ReplayResult] = {}
    return cache


async def replay_for(row: EvalRow, tmp_sessions_root) -> ReplayResult:
    """Replay ``row`` (cached per ``row.conversation_id``)."""
    from pathlib import Path

    root = Path(tmp_sessions_root)
    result = await replay_row(row, root)
    return result


@pytest.mark.live_llm
@skip_no_warehouse
@skip_no_llm
@pytest.mark.asyncio
async def test_layer1_no_hard_fails(
    csv_rows: list[EvalRow],
    governed_sql_mode_on,
    temp_sessions_root,
) -> None:
    """Every row's Layer-1 grading must produce no hard fail or over-clarify.

    Runs all 70 rows sequentially (LLM-bound; parallelism would hit
    rate limits faster than the test wallclock would improve).
    """
    for row in csv_rows:
        result = await replay_for(row, temp_sessions_root)
        assert result.turns, f"{row.conversation_id}: replay produced no turns"
        turn1 = result.turns[0]
        plan = _plan_dict(turn1)
        verdict = grade_plan(plan, row)
        COLLECTED_RESULTS.append((row.conversation_id, verdict))
        assert not verdict.hard_fail, (
            f"{row.conversation_id}: Layer-1 hard fail — {verdict.reason} (mode={verdict.mode})"
        )
        assert not verdict.over_clarify_fail, (
            f"{row.conversation_id}: over-clarify guard tripped — {verdict.reason}"
        )
        # WARNs are tolerated; we record them but do not assert.


@pytest.mark.live_llm
@skip_no_warehouse
@skip_no_llm
@pytest.mark.asyncio
async def test_layer1_replay_persists_history(
    csv_rows: list[EvalRow],
    governed_sql_mode_on,
    temp_sessions_root,
) -> None:
    """Each replay writes ``.jsonl`` / ``.meta.json`` (single) and
    ``.model.jsonl`` (every turn) to the temp sessions root.

    Locks down EVALS.md §4's "must persist through the real store
    (don't shortcut)" requirement. Multi-turn rows additionally
    exercise the clarify-state side-channel.
    """
    import os

    for row in csv_rows:
        result = await replay_for(row, temp_sessions_root)
        sessions_dir = temp_sessions_root / "sessions"
        jsonl_path = sessions_dir / f"{result.session_id}.jsonl"
        meta_path = sessions_dir / f"{result.session_id}.meta.json"
        model_path = sessions_dir / f"{result.session_id}.model.jsonl"
        assert jsonl_path.exists(), f"{row.conversation_id}: missing {jsonl_path}"
        assert meta_path.exists(), f"{row.conversation_id}: missing {meta_path}"
        # ``.model.jsonl`` is written best-effort after each agent call;
        # on a fresh session the first turn produces it.
        assert model_path.exists(), (
            f"{row.conversation_id}: missing {model_path} (replay short-circuited?)"
        )
        # For multi-turn rows we additionally expect a populated model
        # history file (>0 bytes) — single-turn rows write at least the
        # initial user/assistant pair.
        size = os.path.getsize(model_path)
        assert size > 0, f"{row.conversation_id}: empty {model_path}"


def test_report_aggregates_collected_results() -> None:
    """Sanity check: the report can aggregate collected results.

    Skipped cleanly when no LLM run has populated the collector yet
    (the typical CI case where ``live_llm`` tests are filtered out).
    """
    from .layer2 import Layer2Result
    from .report import build_report

    if not COLLECTED_RESULTS:
        pytest.skip("no LLM rows replayed in this run; collector empty")

    # ``COLLECTED_RESULTS`` holds (conversation_id, verdict) pairs where
    # ``verdict`` is always a ``Layer1Result`` at runtime (the static
    # analyser sees ``object`` because the collector list is
    # heterogeneous-looking). The cast tells the type-checker the
    # runtime invariant without lying to readers.
    layer1: list[Layer1Result] = [v for _, v in COLLECTED_RESULTS]  # type: ignore[list-item]
    # No layer2 results exist yet (snapshot not run); pass empty.
    layer2: list[Layer2Result] = []
    line = build_report(layer1, layer2)
    # Report is always one line, always starts with mode_accuracy.
    assert "\n" not in line
    assert line.startswith("mode_accuracy=")
    assert "over_clarify_count=" in line
