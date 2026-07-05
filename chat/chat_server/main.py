"""FastAPI application entry point.

Run locally:

    uv run uvicorn chat_server.main:app --port 8787 --reload

Or as a script:

    uv run python -m chat_server.main

Phase 0 serves only the meta routes (`/api/health`, `/api/config`). Phase 1
adds the data layer; subsequent phases add sessions, the chat agent, and SSE.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routes import meta

# Vite dev server origin (see PLAN §6.1); the production-built frontend
# statically serves from the same host so CORS only matters in dev.
_VITE_DEV_ORIGIN = "http://localhost:5173"

APP_VERSION = "0.1.0"
APP_TITLE = "Basketball Data Chatbot API"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — Phase 0 has nothing to initialize.

    Phase 1+ will open the DuckDB pool, warm the template registry, and
    construct the Pydantic AI agent here.
    """
    yield


app = FastAPI(title=APP_TITLE, version=APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_VITE_DEV_ORIGIN],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Meta routes (see PLAN §7.9) — mounted under `/api` so the paths in
# `routes/meta.py` are bare (`/health`, `/config`).
app.include_router(meta.router, prefix="/api")


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
