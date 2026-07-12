"""Application configuration loaded from environment / .env.

Imported lazily via `get_settings()`. Required env vars (validated on first
access, not on import, so that the package can be imported in environments
without secrets configured):

    OPENROUTER_API_KEY      — required
    DUCKDB_PATH             — required
    OPENROUTER_MODEL        — default "anthropic/claude-sonnet-4.6"
    OPENROUTER_MAX_TOKENS   — default 16384
    CHAT_CORS_ORIGINS       — default "http://localhost:5173"
    CHAT_LOG_DIR            — default "./logs"
    CHAT_PORT               — default 8787
    CHAT_QUERY_TIMEOUT      — default 300 (seconds; watchdog budget for governed SQL execution)
    CHAT_MEMORY_LIMIT       — default "8GB" (DuckDB memory_limit, applied at connection open)
    CHAT_DATA_DIR           — default "./data" (visible session store root)

See also :func:`get_cors_origins` — a lightweight parser that reads
``CHAT_CORS_ORIGINS`` first from the process environment (highest
precedence), then from ``chat/.env`` via ``dotenv_values``, without
building the full ``Settings`` object.  Safe to call at module-import
time in environments without secrets configured.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values
from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
_DEFAULT_CORS_ORIGIN = "http://localhost:5173"


def _parse_cors_origins(raw: str | None) -> list[str]:
    """Normalize a comma-separated CORS value using the application rules."""
    if not raw or not raw.strip():
        return [_DEFAULT_CORS_ORIGIN]

    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    if "*" not in origins:
        return [origin.rstrip("/") for origin in origins]
    return ["*"] if len(origins) == 1 else [_DEFAULT_CORS_ORIGIN]


class Settings(BaseSettings):
    """Typed view of the chatbot's runtime config."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    openrouter_api_key: str = Field(..., min_length=1)
    openrouter_model: str = Field(default="anthropic/claude-sonnet-4.6")
    openrouter_provider: str = Field(
        default="",
        description="Optional OpenRouter provider slug to prefer (e.g. 'deepinfra'). "
        "When empty, OpenRouter auto-routes across all providers. "
        "See https://openrouter.ai/docs/guides/routing/provider-selection",
    )
    openrouter_max_tokens: int = Field(
        default=16384,
        ge=256,
        le=128000,
        description="Max output tokens for OpenRouter calls. Higher values prevent "
        "tool-call JSON truncation when the system prompt is large.",
    )
    duckdb_path: str = Field(..., min_length=1)
    chat_log_dir: str = Field(default="./logs")
    chat_port: int = Field(default=8787, ge=1, le=65535)
    query_timeout_seconds: int = Field(default=300, ge=1)
    chat_memory_limit: str = Field(default="8GB", min_length=1)
    chat_data_dir: str = Field(default="./data")

    #: Comma-separated CORS origins. Each origin is stripped of trailing
    #: slashes and whitespace. ``"*"`` is only valid as the sole origin;
    #: mixing it with specific origins falls back to the default.
    #: Example:
    #: ``CHAT_CORS_ORIGINS="http://localhost:5173,http://localhost:4173"``
    chat_cors_origins: str = Field(
        default=_DEFAULT_CORS_ORIGIN,
        description="Comma-separated list of allowed CORS origins",
    )

    @field_validator("chat_cors_origins", mode="before")
    @classmethod
    def _normalize_cors_origins(cls, v: str) -> str:
        """Strip whitespace around commas; the raw string is stored but
        consumers should call :meth:`parsed_cors_origins` for the typed list."""
        if not v or not v.strip():
            return _DEFAULT_CORS_ORIGIN
        return ",".join(origin.strip() for origin in v.split(",") if origin.strip())

    def parsed_cors_origins(self) -> list[str]:
        """Return the CORS origins as a normalized list, each entry stripped
        of trailing slashes so FastAPI's CORS middleware works correctly.

        ``"*"`` is only valid as the sole origin — mixing it with specific
        origins falls back to ``["http://localhost:5173"]``.
        """
        return _parse_cors_origins(self.chat_cors_origins)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached `Settings` instance, validating required fields.

    Raises `RuntimeError` (with a clear message) when `OPENROUTER_API_KEY` or
    `DUCKDB_PATH` is missing. Cached for the life of the process.
    """
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        missing = sorted(err["loc"][0] for err in exc.errors() if err["type"].startswith("missing"))
        if missing:
            raise RuntimeError(
                "Missing required environment variable(s): "
                + ", ".join(str(name) for name in missing)
                + ". Copy chat/.env.example to chat/.env and fill them in."
            ) from exc
        raise


def reset_settings_cache() -> None:
    """Clear the cached settings; test helper only."""
    get_settings.cache_clear()


def get_cors_origins() -> list[str]:
    """Lightweight CORS origins parser — does **not** build the full
    ``Settings`` object (and therefore does **not** require
    ``OPENROUTER_API_KEY`` or ``DUCKDB_PATH``).

    Reads ``CHAT_CORS_ORIGINS`` with the following precedence:

    1. Process environment (``os.environ``) — highest priority.
    2. ``chat/.env`` file (via ``dotenv_values``) — respects the same
       ``.env`` that ``Settings`` loads via pydantic-settings.
    3. Built-in default (``"http://localhost:5173"``).

    Applies the same normalisation as :meth:`Settings.parsed_cors_origins`,
    and is safe to call at module-import time in environments without
    secrets configured.

    Rules:

    - Empty or whitespace-only input → ``["http://localhost:5173"]``.
    - ``"*"`` as the sole origin → ``["*"]``.
    - ``"*"`` mixed with specific origins → fall back to the default (unsafe
      configuration is silently corrected).
    - Non-wildcard origins have trailing slashes stripped.
    """
    raw = os.environ.get("CHAT_CORS_ORIGINS")
    if raw is None:
        dotenv_vals = dotenv_values(str(_ENV_FILE))
        raw = dotenv_vals.get("CHAT_CORS_ORIGINS")
    return _parse_cors_origins(raw)
