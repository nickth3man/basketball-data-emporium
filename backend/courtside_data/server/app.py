"""FastAPI application factory, exception mapping, and CLI entry point.

This module owns the canonical mapping from Python exceptions to the
JSON error envelope that the frontend expects:

    { "detail": { "code": "...", "message": "...", "detail": {...} } }

`frontend/src/lib/api-errors.ts:5` names `_map_exception` as the
source of truth for that envelope, and
`frontend/scripts/generate-api-types.ts:25` references the `app`
symbol directly. Keep both names stable.

The CLI entry point is registered as the `courtside-data` script in
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

from courtside_data import __version__
from courtside_data.schemas.common import ApiError
from courtside_data.server.errors import (
    CourtsideError,
    InternalError,
)
from courtside_data.server.routes import catalog as catalog_route
from courtside_data.server.routes import players as players_route
from courtside_data.server.routes import status as status_route
from courtside_data.server.routes import teams as teams_route

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exception mapping
#
# Defined first so `_build_app()` can reference the handlers at module
# import time. The module-level `app` singleton is constructed during
# import (uvicorn needs it to be importable by name), so every name
# it references must already exist on the module.
# ---------------------------------------------------------------------------


def _serialize_error(exc: CourtsideError) -> ApiError:
    """Build the `ApiError` payload for a domain exception."""
    return ApiError(code=exc.code, message=exc.message, detail=exc.detail)


async def _map_exception(
    request: Request,  # noqa: ARG001 — kept for FastAPI signature parity
    exc: Exception,
) -> JSONResponse:
    """Translate a `CourtsideError` into the standard JSON envelope.

    Registered as the FastAPI exception handler for `CourtsideError`
    and its subclasses. Status comes from the exception's `status`
    attribute; for uncaught `CourtsideError` we still produce a
    well-formed envelope (and never leak the Python repr).

    Also registered as a fallback for non-`CourtsideError` exceptions
    that the framework hands to the most specific class match; in
    practice the unhandled-catch-all in `_map_exception_unhandled`
    catches them first, but the same code path is safe either way.
    """
    if isinstance(exc, CourtsideError):
        payload = _serialize_error(exc)
        return JSONResponse(
            status_code=exc.status,
            content={"detail": payload.model_dump(exclude_none=True)},
        )

    # Defensive fallback: a non-`CourtsideError` reached this handler
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
        title="Courtside Data API",
        version=__version__,
        description=(
            "Read-only HTTP API over the Courtside DuckDB store. "
            "Phase 1 implements only the `/api/status` liveness endpoint."
        ),
    )

    # TODO P0-BE-01: configure CORS before this sidecar is treated as
    # browser-ready. The Next app calls the API from `127.0.0.1:3000`, which is
    # a different origin than the sidecar on `127.0.0.1:8765`. Add
    # `configure_cors(app)` from `server/cors.py` once allowed origins are
    # environment-driven and covered by a browser/E2E test.

    # Domain exception → { detail: ApiError } envelope.
    app.add_exception_handler(CourtsideError, _map_exception)

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
        prog="courtside-data",
        description="Courtside Data FastAPI sidecar.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"courtside-data {__version__}",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the FastAPI server (uvicorn).")
    serve.add_argument(
        "--host",
        default=os.environ.get("COURTSIDE_HOST", "127.0.0.1"),
        help="Bind host (default: 127.0.0.1).",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("COURTSIDE_PORT", "8765")),
        help="Bind port (default: 8765).",
    )
    serve.add_argument(
        "--reload",
        action="store_true",
        default=os.environ.get("COURTSIDE_RELOAD", "").lower() in {"1", "true", "yes"},
        help="Enable autoreload (dev only).",
    )
    serve.add_argument(
        "--log-level",
        default=os.environ.get("COURTSIDE_LOG_LEVEL", "info"),
        help="Uvicorn log level (default: info).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the `courtside-data` console script."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "serve":
        # TODO P0-OPS-01: add port ownership/readiness handling to the dev
        # runner. A stale process on :8765 can serve an old OpenAPI spec and
        # poison frontend codegen. The CLI or root dev script should either own
        # the process lifecycle or fail with a clear "port already in use by
        # PID ..." diagnostic.
        # Use uvicorn's programmatic runner so we keep one path to
        # `app` regardless of whether the user typed
        # `courtside-data serve` or `uvicorn courtside_data.server.app:app`.
        uvicorn.run(
            "courtside_data.server.app:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level=args.log_level,
        )
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2  # unreachable; argparse exits first


if __name__ == "__main__":
    sys.exit(main())
