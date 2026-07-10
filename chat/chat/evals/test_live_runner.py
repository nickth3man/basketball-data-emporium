"""Deterministic coverage for the bounded live-eval runner."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from . import live_runner
from .live_runner import (
    DEFAULT_CONCURRENCY,
    get_concurrency,
    replay_rows_ordered,
)
from .loader import EvalRow
from .replay import ReplayResult


def _row(conversation_id: str) -> EvalRow:
    return EvalRow(
        conversation_id=conversation_id,
        turns="single",
        domain="test",
        era="test",
        teams_or_players="",
        user_initial_question="question",
        expected_answer_mode_turn1="execute_sql",
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, DEFAULT_CONCURRENCY),
        ("1", 1),
        ("12", 12),
        ("0", DEFAULT_CONCURRENCY),
        (" 8", DEFAULT_CONCURRENCY),
        ("+8", DEFAULT_CONCURRENCY),
        ("8.0", DEFAULT_CONCURRENCY),
        ("eight", DEFAULT_CONCURRENCY),
    ],
)
def test_get_concurrency_validates_strict_positive_integers(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: int
) -> None:
    monkeypatch.delenv("CHAT_EVAL_CONCURRENCY", raising=False)
    assert get_concurrency(value) == expected


def test_get_concurrency_reads_chat_eval_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAT_EVAL_CONCURRENCY", "3")
    assert get_concurrency() == 3


async def test_replay_rows_ordered_bounds_work_and_uses_cache() -> None:
    active = 0
    peak_active = 0
    called: list[str] = []

    async def replay(row: EvalRow, _root: Path) -> ReplayResult:
        nonlocal active, peak_active
        called.append(row.conversation_id)
        active += 1
        peak_active = max(peak_active, active)
        await asyncio.sleep({"first": 0.03, "second": 0.01, "third": 0.0}[row.conversation_id])
        active -= 1
        return ReplayResult(session_id=row.conversation_id.upper())

    rows = [_row("first"), _row("second"), _row("third"), _row("first")]
    results = await replay_rows_ordered(rows, Path("sessions"), replay, concurrency=2)

    assert [result.session_id for result in results] == ["FIRST", "SECOND", "THIRD", "FIRST"]
    assert called.count("first") == 1
    assert peak_active == 2


async def test_replay_rows_ordered_reports_unique_completions_in_completion_order() -> None:
    messages: list[str] = []

    async def replay(row: EvalRow, _root: Path) -> ReplayResult:
        await asyncio.sleep({"first": 0.03, "second": 0.01, "third": 0.0}[row.conversation_id])
        return ReplayResult(session_id=row.conversation_id)

    await replay_rows_ordered(
        [_row("first"), _row("second"), _row("third"), _row("first")],
        Path("sessions"),
        replay,
        concurrency=3,
        reporter=messages.append,
    )

    assert messages == [
        "[live-eval] 1/3 complete (third)",
        "[live-eval] 2/3 complete (second)",
        "[live-eval] 3/3 complete (first)",
    ]


async def test_replay_rows_ordered_marks_exhausted_infrastructure_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    async def replay(row: EvalRow, _root: Path) -> ReplayResult:
        nonlocal attempts
        if row.conversation_id == "broken":
            attempts += 1
            raise ConnectionError("provider unavailable")
        return ReplayResult(session_id=row.conversation_id)

    results = await replay_rows_ordered([_row("ok"), _row("broken")], Path("sessions"), replay)

    assert results[0].infrastructure_error is None
    assert results[1].infrastructure_error is not None
    assert "ConnectionError" in results[1].infrastructure_error
    assert attempts == 3


async def test_replay_rows_ordered_reports_retries_and_exhausted_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[str] = []
    monkeypatch.setattr(live_runner, "_backoff_seconds", lambda _attempt, _exc: 0.25)

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    async def replay(_row: EvalRow, _root: Path) -> ReplayResult:
        raise ConnectionError("provider unavailable")

    await replay_rows_ordered([_row("broken")], Path("sessions"), replay, reporter=messages.append)

    assert messages == [
        "[live-eval] retry (broken) attempt 1 delay 0.25s",
        "[live-eval] retry (broken) attempt 2 delay 0.25s",
        "[live-eval] failed (broken)",
        "[live-eval] 1/1 complete (broken)",
    ]


async def test_replay_rows_ordered_reports_non_retryable_failure() -> None:
    messages: list[str] = []

    async def replay(_row: EvalRow, _root: Path) -> ReplayResult:
        raise ValueError("invalid response")

    with pytest.raises(ExceptionGroup):
        await replay_rows_ordered([_row("bad")], Path("sessions"), replay, reporter=messages.append)

    assert messages == ["[live-eval] failed (bad)", "[live-eval] 1/1 complete (bad)"]


async def test_replay_rows_ordered_can_suppress_default_progress(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CHAT_EVAL_PROGRESS", "0")

    async def replay(row: EvalRow, _root: Path) -> ReplayResult:
        return ReplayResult(session_id=row.conversation_id)

    await replay_rows_ordered([_row("quiet")], Path("sessions"), replay)

    assert capsys.readouterr().err == ""
