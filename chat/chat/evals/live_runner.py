"""Bounded-concurrent helpers for live eval replays.

Live providers have different quotas, so ``CHAT_EVAL_CONCURRENCY`` controls
the number of in-flight replays. Results retain CSV input order regardless of
the order in which providers complete requests.
"""

from __future__ import annotations

import asyncio
import os
import random
import re
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from pydantic_ai.exceptions import ModelHTTPError

from .loader import EvalRow
from .replay import ReplayResult

# OpenRouter officially states "No limits on paid models" — the practical
# ceiling is upstream provider capacity, not an OpenRouter quota. For paid
# gpt-oss-120b (19 providers with automatic fallback), 50 is a safe starting
# point. Raise to 100 if 429/503 rate stays low; lower if upstream throttles.
DEFAULT_CONCURRENCY = 50
_POSITIVE_INTEGER = re.compile(r"[1-9][0-9]*\Z")
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 1.5
_BACKOFF_CAP_SECONDS = 20.0

type Replay = Callable[[EvalRow, Path], Awaitable[ReplayResult]]
type Reporter = Callable[[str], None]


class ReplayFailureError(RuntimeError):
    """A replay exception annotated with the CSV conversation id."""

    def __init__(self, conversation_id: str) -> None:
        super().__init__(f"{conversation_id}: replay failed")


def _status_code(exc: BaseException) -> int | None:
    """Extract an HTTP status code when a provider exposes one."""
    value = getattr(exc, "status_code", None)
    if value is None:
        response = getattr(exc, "response", None)
        value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _is_retryable(exc: BaseException) -> bool:
    """Return whether an exception is a transient provider failure."""
    if isinstance(exc, (TimeoutError, ConnectionError, asyncio.TimeoutError)):
        return True
    status = _status_code(exc)
    return isinstance(exc, ModelHTTPError) and (
        status == 429 or status is not None and status >= 500
    )


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Read a provider's Retry-After header, accepting seconds or an HTTP date."""
    headers: Any = getattr(exc, "headers", None)
    if headers is None:
        headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return None
    value = headers.get("Retry-After") or headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError, IndexError, OverflowError):
            return None


def _backoff_seconds(attempt: int, exc: BaseException) -> float:
    """Calculate capped exponential backoff, honoring Retry-After when supplied."""
    retry_after = _retry_after_seconds(exc)
    if retry_after is not None:
        return min(retry_after, _BACKOFF_CAP_SECONDS)
    delay = min(_BACKOFF_BASE_SECONDS * (2**attempt), _BACKOFF_CAP_SECONDS)
    return delay * random.uniform(0.75, 1.25)


def get_concurrency(value: str | None = None) -> int:
    """Return a strictly positive env cap, falling back to the safe default.

    Passing ``value`` makes this deterministic in tests; production callers
    omit it to read ``CHAT_EVAL_CONCURRENCY``. Whitespace, signs, decimals,
    and zero are deliberately invalid so misconfigured runs do not silently
    select an unexpected cap.
    """
    raw = os.environ.get("CHAT_EVAL_CONCURRENCY") if value is None else value
    if raw is None or not _POSITIVE_INTEGER.fullmatch(raw):
        return DEFAULT_CONCURRENCY
    return int(raw)


def _stderr_reporter(message: str) -> None:
    """Write a live-eval progress message without buffering it."""
    print(message, file=sys.stderr, flush=True)


def _get_reporter(reporter: Reporter | None) -> Reporter | None:
    """Return the injected reporter or the enabled default terminal reporter."""
    if reporter is not None:
        return reporter
    if os.environ.get("CHAT_EVAL_PROGRESS") == "0":
        return None
    return _stderr_reporter


async def replay_rows_ordered(
    rows: Sequence[EvalRow],
    sessions_root: Path,
    replay: Replay,
    *,
    cache: dict[str, ReplayResult] | None = None,
    concurrency: int | None = None,
    reporter: Reporter | None = None,
) -> list[ReplayResult]:
    """Replay rows with a semaphore and return results in input order.

    Cached conversation ids are not replayed again. Retryable provider failures
    become tagged infrastructure results after the retry budget is exhausted;
    non-retryable failures are reported in CSV order through an
    ``ExceptionGroup`` rather than being lost behind the first concurrent
    failure. By default, terminal progress is written to stderr; set
    ``CHAT_EVAL_PROGRESS=0`` to suppress it. ``reporter`` permits deterministic
    callers and tests to receive the same messages without writing to stderr.
    """
    limit = get_concurrency() if concurrency is None else concurrency
    if limit <= 0:
        raise ValueError("concurrency must be a positive integer")

    replay_cache = cache if cache is not None else {}
    semaphore = asyncio.Semaphore(limit)
    progress_reporter = _get_reporter(reporter)
    total = len({row.conversation_id for row in rows if row.conversation_id not in replay_cache})
    completed_count = 0

    def report(message: str) -> None:
        if progress_reporter is not None:
            progress_reporter(f"[live-eval] {message}")

    def report_completion(conversation_id: str) -> None:
        nonlocal completed_count
        completed_count += 1
        report(f"{completed_count}/{total} complete ({conversation_id})")

    async def replay_one(row: EvalRow) -> ReplayResult:
        if row.conversation_id in replay_cache:
            return replay_cache[row.conversation_id]
        for attempt in range(_MAX_ATTEMPTS):
            try:
                async with semaphore:
                    result = await replay(row, sessions_root)
            except Exception as exc:
                if not _is_retryable(exc):
                    report(f"failed ({row.conversation_id})")
                    report_completion(row.conversation_id)
                    raise ReplayFailureError(row.conversation_id) from exc
                if attempt == _MAX_ATTEMPTS - 1:
                    result = ReplayResult(
                        session_id="",
                        infrastructure_error=(
                            f"{type(exc).__name__} after {_MAX_ATTEMPTS} attempts: {exc}"
                        ),
                    )
                    report(f"failed ({row.conversation_id})")
                    break
                delay = _backoff_seconds(attempt, exc)
                report(f"retry ({row.conversation_id}) attempt {attempt + 1} delay {delay:.2f}s")
                await asyncio.sleep(delay)
            else:
                break
        replay_cache[row.conversation_id] = result
        report_completion(row.conversation_id)
        return result

    # One task per id prevents duplicate CSV rows from bypassing the cache
    # while their first replay is still in flight.
    tasks_by_id: dict[str, asyncio.Task[ReplayResult]] = {}
    for row in rows:
        if row.conversation_id not in replay_cache and row.conversation_id not in tasks_by_id:
            tasks_by_id[row.conversation_id] = asyncio.create_task(replay_one(row))
    completed = await asyncio.gather(*tasks_by_id.values(), return_exceptions=True)
    failures = [result for result in completed if isinstance(result, Exception)]
    if failures:
        raise ExceptionGroup("live eval replay failures", failures)

    return [replay_cache[row.conversation_id] for row in rows]


__all__ = [
    "DEFAULT_CONCURRENCY",
    "ReplayFailureError",
    "get_concurrency",
    "replay_rows_ordered",
]
