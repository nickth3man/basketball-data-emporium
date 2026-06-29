"""FastAPI application factory, exception mapping, and CLI entry point.

This module owns the canonical mapping from Python exceptions to the
JSON error envelope that the frontend expects:

    { "detail": { "code": "...", "message": "...", "detail": {...} } }

`frontend/src/lib/api-errors.ts:5` names `_map_exception` as the
source of truth for that envelope, and
`frontend/scripts/generate-api-types.ts:25` references the `app`
symbol directly. Keep both names stable.

The CLI entry point is registered as the `basketball-data-emporium` script in
`pyproject.toml`. It accepts a `serve` subcommand (the only one for
Phase 1) and any other subcommands are reserved for future phases.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from basketball_data_emporium import __version__
from basketball_data_emporium.db.pool import get_pool
from basketball_data_emporium.queries.players import validate_featured_players
from basketball_data_emporium.queries.teams import validate_featured_teams
from basketball_data_emporium.schemas.common import ApiError
from basketball_data_emporium.server.errors import (
    BasketballDataEmporiumError,
    InternalError,
    RateLimitJailedError,
)
from basketball_data_emporium.server.cors import configure_cors
from basketball_data_emporium.server.rate_limit import check_rate_limit
from basketball_data_emporium.server.routes import catalog as catalog_route
from basketball_data_emporium.server.routes import players as players_route
from basketball_data_emporium.server.routes import status as status_route
from basketball_data_emporium.server.routes import teams as teams_route

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exception mapping
#
# Defined first so `_build_app()` can reference the handlers at module
# import time. The module-level `app` singleton is constructed during
# import (uvicorn needs it to be importable by name), so every name
# it references must already exist on the module.
# ---------------------------------------------------------------------------


def _serialize_error(exc: BasketballDataEmporiumError) -> ApiError:
    """Build the `ApiError` payload for a domain exception."""
    return ApiError(code=exc.code, message=exc.message, detail=exc.detail)


async def _map_exception(
    request: Request,  # noqa: ARG001 — kept for FastAPI signature parity
    exc: Exception,
) -> JSONResponse:
    """Translate a `BasketballDataEmporiumError` into the standard JSON envelope.

    Registered as the FastAPI exception handler for `BasketballDataEmporiumError`
    and its subclasses. Status comes from the exception's `status`
    attribute; for uncaught `BasketballDataEmporiumError` we still produce a
    well-formed envelope (and never leak the Python repr).

    Also registered as a fallback for non-`BasketballDataEmporiumError` exceptions
    that the framework hands to the most specific class match; in
    practice the unhandled-catch-all in `_map_exception_unhandled`
    catches them first, but the same code path is safe either way.
    """
    if isinstance(exc, BasketballDataEmporiumError):
        payload = _serialize_error(exc)
        return JSONResponse(
            status_code=exc.status,
            content={"detail": payload.model_dump(exclude_none=True)},
        )

    # Defensive fallback: a non-`BasketballDataEmporiumError` reached this handler
    # (e.g. someone added a bare `Exception` raise later). Wrap as
    # `internal_error` and never leak the Python repr.
    payload = ApiError(
        code=InternalError.code,
        message=str(exc) or exc.__class__.__name__,
    )
    return JSONResponse(
        status_code=InternalError.status,
        content={"detail": payload.model_dump(exclude_none=True)},
    )


async def _map_exception_unhandled(
    request: Request,  # noqa: ARG001
    exc: Exception,  # noqa: ARG001
) -> JSONResponse:
    """Catch-all: every uncaught `Exception` becomes `internal_error`.

    The traceback is logged server-side (so operators can diagnose),
    but the client gets a clean envelope and never sees the Python
    repr. The wire shape is identical to the handled-exception path.
    """
    logger.exception("Unhandled exception while serving %s", request.url.path)
    payload = ApiError(
        code=InternalError.code,
        message="Internal server error",
    )
    return JSONResponse(
        status_code=InternalError.status,
        content={"detail": payload.model_dump(exclude_none=True)},
    )


# ---------------------------------------------------------------------------
# App factory + singleton
# ---------------------------------------------------------------------------


def _build_app() -> FastAPI:
    """Construct the FastAPI instance and register handlers + routes."""
    app = FastAPI(
        title="Basketball Data Emporium API",
        version=__version__,
        description=(
            "Read-only HTTP API over the Basketball Data Emporium DuckDB store. "
            "Serves the Player and Team Hub over the curated DuckDB snapshot."
        ),
    )

    configure_cors(app)

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            forwarded_for = request.headers.get("x-forwarded-for")
            client_host = request.client.host if request.client else "unknown"
            client_key = (
                forwarded_for.split(",", 1)[0].strip() if forwarded_for else client_host
            )
            try:
                check_rate_limit(client_key)
            except RateLimitJailedError as exc:
                payload = _serialize_error(exc)
                return JSONResponse(
                    status_code=exc.status,
                    content={"detail": payload.model_dump(exclude_none=True)},
                )
        return await call_next(request)

    # Domain exception → { detail: ApiError } envelope.
    app.add_exception_handler(BasketballDataEmporiumError, _map_exception)

    # Catch-all for uncaught exceptions so we never leak a stack trace
    # as the raw body. Still goes through `_map_exception` so the
    # envelope is always well-formed.
    app.add_exception_handler(Exception, _map_exception_unhandled)

    app.include_router(catalog_route.router)
    app.include_router(players_route.router)
    app.include_router(status_route.router)
    app.include_router(teams_route.router)

    return app


# Singleton instance — uvicorn imports this module and looks up `app`.
app: FastAPI = _build_app()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="basketball-data-emporium",
        description="Basketball Data Emporium FastAPI sidecar.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"basketball-data-emporium {__version__}",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the FastAPI server (uvicorn).")
    serve.add_argument(
        "--host",
        default=os.environ.get("BASKETBALL_DATA_HOST", "127.0.0.1"),
        help="Bind host (default: 127.0.0.1).",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("BASKETBALL_DATA_PORT", "8765")),
        help="Bind port (default: 8765).",
    )
    serve.add_argument(
        "--reload",
        action="store_true",
        default=os.environ.get("BASKETBALL_DATA_RELOAD", "").lower()
        in {"1", "true", "yes"},
        help="Enable autoreload (dev only).",
    )
    serve.add_argument(
        "--log-level",
        default=os.environ.get("BASKETBALL_DATA_LOG_LEVEL", "info"),
        help="Uvicorn log level (default: info).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the `basketball-data-emporium` console script."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "serve":
        if os.environ.get(
            "BASKETBALL_DATA_SKIP_STARTUP_VALIDATION", ""
        ).lower() not in {"1", "true", "yes"}:
            pool = get_pool()
            with pool.connection() as conn:
                validate_featured_players(conn)
                validate_featured_teams(conn)
        # Use uvicorn's programmatic runner so we keep one path to
        # `app` regardless of whether the user typed
        # `basketball-data-emporium serve` or `uvicorn basketball_data_emporium.server.app:app`.
        uvicorn.run(
            "basketball_data_emporium.server.app:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level=args.log_level,
        )
        return 0

    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
