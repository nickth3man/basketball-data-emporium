"""CORS configuration for browser access to the sidecar."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


DEFAULT_ALLOWED_ORIGINS = (
    "http://127.0.0.1:3000",
    "http://localhost:3000",
)


def _csv_env(name: str, default: tuple[str, ...]) -> list[str]:
    raw = os.environ.get(name)
    if raw is None:
        return list(default)
    values = [value.strip() for value in raw.split(",") if value.strip()]
    return values or list(default)


def configure_cors(app: FastAPI) -> None:
    """Install environment-driven CORS middleware.

    ``BASKETBALL_DATA_CORS_ORIGINS`` accepts a comma-separated allow-list. Local
    development defaults to the two common Next.js origins; production should
    set a narrower value explicitly.
    """
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_csv_env("BASKETBALL_DATA_CORS_ORIGINS", DEFAULT_ALLOWED_ORIGINS),
        allow_methods=_csv_env("BASKETBALL_DATA_CORS_METHODS", ("GET", "OPTIONS")),
        allow_headers=_csv_env(
            "BASKETBALL_DATA_CORS_HEADERS", ("Accept", "Content-Type")
        ),
        allow_credentials=False,
        max_age=int(os.environ.get("BASKETBALL_DATA_CORS_MAX_AGE", "600")),
    )
