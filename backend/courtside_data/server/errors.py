"""Domain exception hierarchy and FastAPI handlers.

The eight codes are stable and 1:1 with
`frontend/src/lib/api-errors.ts:8-16`. Each one maps to a Python
exception class with a fixed HTTP status; `_map_exception()` (in
`courtside_data.server.app`) wraps any of these into the FastAPI
`{ detail: { code, message, detail } }` envelope consumed by the
frontend's `parseApiError` helper.

Codes
-----
* `invalid_search`    (400) — search term malformed
* `bad_request`       (400) — generic request validation failure
* `invalid_player`    (404) — player identifier does not resolve
* `invalid_team`      (404) — team identifier does not resolve
* `invalid_season`    (404) — season identifier does not resolve
* `rate_limit_jailed`  (429) — client has been throttled
* `schema_drift`      (500) — DuckDB schema no longer matches contract
* `internal_error`    (500) — uncaught exception / DB unreachable

The catch-all handler in `app.py` turns every uncaught `Exception` into
`internal_error` so the server never leaks a stack trace as the raw
body — the JSON envelope is always well-formed.
"""

from __future__ import annotations

from typing import Any

# Stable error codes. Keep in sync with the union in
# `frontend/src/lib/api-errors.ts:8-16`.
INVALID_SEARCH = "invalid_search"
BAD_REQUEST = "bad_request"
INVALID_PLAYER = "invalid_player"
INVALID_TEAM = "invalid_team"
INVALID_SEASON = "invalid_season"
RATE_LIMIT_JAILED = "rate_limit_jailed"
SCHEMA_DRIFT = "schema_drift"
INTERNAL_ERROR = "internal_error"

ALL_CODES: frozenset[str] = frozenset(
    {
        INVALID_SEARCH,
        BAD_REQUEST,
        INVALID_PLAYER,
        INVALID_TEAM,
        INVALID_SEASON,
        RATE_LIMIT_JAILED,
        SCHEMA_DRIFT,
        INTERNAL_ERROR,
    }
)


class CourtsideError(Exception):
    """Base class for every domain exception.

    Subclasses set `code` and `status`. The optional `detail` dict is
    surfaced verbatim in the `ApiError.detail` field (e.g.
    `{"identifier": "jamesle01"}` for `invalid_player`).
    """

    code: str = INTERNAL_ERROR
    status: int = 500

    def __init__(
        self,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class InvalidSearchError(CourtsideError):
    code = INVALID_SEARCH
    status = 400


class BadRequestError(CourtsideError):
    code = BAD_REQUEST
    status = 400


class InvalidPlayerError(CourtsideError):
    code = INVALID_PLAYER
    status = 404


class InvalidTeamError(CourtsideError):
    code = INVALID_TEAM
    status = 404


class InvalidSeasonError(CourtsideError):
    code = INVALID_SEASON
    status = 404


class RateLimitJailedError(CourtsideError):
    code = RATE_LIMIT_JAILED
    status = 429

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        merged: dict[str, Any] = dict(detail or {})
        if retry_after is not None:
            merged.setdefault("retry_after", retry_after)
        super().__init__(message, detail=merged or None)
        self.retry_after = retry_after


class SchemaDriftError(CourtsideError):
    code = SCHEMA_DRIFT
    status = 500


class InternalError(CourtsideError):
    code = INTERNAL_ERROR
    status = 500


__all__ = [
    "ALL_CODES",
    "BadRequestError",
    "CourtsideError",
    "InternalError",
    "InvalidPlayerError",
    "InvalidSearchError",
    "InvalidSeasonError",
    "InvalidTeamError",
    "RateLimitJailedError",
    "SchemaDriftError",
]
