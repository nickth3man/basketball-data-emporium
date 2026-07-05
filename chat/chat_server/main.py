"""FastAPI application entry point.

Run locally:

    uv run uvicorn chat_server.main:app --port 8787 --reload

Or as a script:

    uv run python -m chat_server.main

Phase 0 serves only the meta routes (`/api/health`, `/api/config`). Phase 1
adds the data layer; Phase 2 adds sessions + logging; subsequent phases
add the chat agent and SSE.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .log_retention import sweep_all
from .logging_setup import setup_logging
from .routes import chat, meta, sessions

log = logging.getLogger(__name__)

# Vite dev server origin (see PLAN §6.1); the production-built frontend
# statically serves from the same host so CORS only matters in dev.
_VITE_DEV_ORIGIN = "http://localhost:5173"

APP_VERSION = "0.1.0"
APP_TITLE = "Basketball Data Chatbot API"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan.

    Phase 2: configure the JSONL + redacting root logger once on startup.
    Phase 1+ will additionally open the DuckDB pool, warm the template
    registry, and (Phase 3) construct the Pydantic AI agent. Teardown
    handlers (closing the pool, flushing logs) will be added when the
    corresponding resources land.

    Phase 7: run the 7-day log-retention sweep immediately after logging
    is configured. The sweep is best-effort and never raises (PLAN §7.10)
    so a read-only disk or a locked file cannot prevent startup.
    """
    setup_logging()
    # 7-day rolling retention (PLAN §7.10). sweep_all swallows errors
    # internally and returns a {subdir: count} map we surface as one
    # log line — the per-file IO errors it sees are already logged at
    # WARNING inside sweep_logs.
    try:
        result = sweep_all()
        total = sum(result.values())
        log.info("log retention sweep removed %d files: %s", total, result)
    except Exception:  # noqa: BLE001 - belt-and-braces: startup must never fail
        log.exception("log retention sweep failed; continuing startup")
    yield


app = FastAPI(title=APP_TITLE, version=APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_VITE_DEV_ORIGIN],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Meta routes (PLAN §7.9) and session routes (PLAN §7.9 / §7.10) —
# mounted under `/api` so the paths in each route module are bare
# (`/health`, `/config`, `/sessions`, `/debug/artifacts/{id}`).
app.include_router(meta.router, prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(chat.router, prefix="/api")


@app.get("/", tags=["meta"])
def root() -> dict[str, str]:
    """Tiny index so `curl http://localhost:8787/` returns something useful."""
    return {"name": APP_TITLE, "docs": "/docs"}


def run() -> None:
    """Programmatic entry point used by `python -m chat_server.main`."""
    settings = get_settings()
    uvicorn.run(
        "chat_server.main:app",
        host="127.0.0.1",
        port=settings.chat_port,
        reload=False,
    )


if __name__ == "__main__":
    run()
