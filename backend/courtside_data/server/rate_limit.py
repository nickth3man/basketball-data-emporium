"""Rate-limit jail scaffold."""

from __future__ import annotations


def is_rate_limit_jailed(client_key: str) -> bool:
    """Return whether a caller is currently jailed.

    TODO P2-BE-06: Implement rate-limit jail if it becomes product-relevant.
    The error code and frontend retry behavior exist, but there is no shared
    cross-request state. If enabled, this needs a deterministic client key,
    storage with TTL, retry-after calculation, and contract tests that verify
    `rate_limit_jailed` envelopes.
    """
    _ = client_key
    return False

