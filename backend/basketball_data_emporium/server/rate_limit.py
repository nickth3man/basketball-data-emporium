"""Small in-process fixed-window rate limiter."""

from __future__ import annotations

import os
import threading
import time

from basketball_data_emporium.server.errors import RateLimitJailedError

_lock = threading.Lock()
_hits: dict[str, list[float]] = {}


def _limit_per_minute() -> int:
    raw = os.environ.get("BASKETBALL_DATA_RATE_LIMIT_PER_MINUTE", "0").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def check_rate_limit(client_key: str) -> None:
    """Raise ``RateLimitJailedError`` when the caller exceeds the limit."""
    limit = _limit_per_minute()
    if limit <= 0:
        return

    now = time.monotonic()
    floor = now - 60
    with _lock:
        recent = [ts for ts in _hits.get(client_key, []) if ts >= floor]
        if len(recent) >= limit:
            retry_after = max(1, int(60 - (now - recent[0])))
            _hits[client_key] = recent
            raise RateLimitJailedError("Rate limit exceeded", retry_after=retry_after)
        recent.append(now)
        _hits[client_key] = recent


def is_rate_limit_jailed(client_key: str) -> bool:
    """Compatibility helper for callers that need a boolean check."""
    try:
        check_rate_limit(client_key)
    except RateLimitJailedError:
        return True
    return False
