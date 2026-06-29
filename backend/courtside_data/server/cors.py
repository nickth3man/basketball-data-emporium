"""CORS configuration scaffold for browser access to the sidecar."""

from __future__ import annotations

from fastapi import FastAPI


def configure_cors(app: FastAPI) -> None:
    """Install CORS middleware once the allowed-origin policy is decided.

    TODO P0-BE-01: Configure API CORS for the Next.js origin.
    The frontend calls this sidecar from a separate origin during local
    development (`http://127.0.0.1:3000` -> `http://127.0.0.1:8765`).
    Add `CORSMiddleware` here with environment-driven origins, methods, and
    headers. Keep the function centralized so production can use a narrower
    allow-list than local development, and add a browser/E2E assertion that a
    real `fetch()` from the Next app succeeds.
    """
    _ = app

