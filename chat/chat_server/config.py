"""Application configuration loaded from environment / .env.

Imported lazily via `get_settings()`. Required env vars (validated on first
access, not on import, so that the package can be imported in environments
without secrets configured):
    OPENROUTER_API_KEY   — required
    DUCKDB_PATH          — required
    OPENROUTER_MODEL     — default "anthropic/claude-sonnet-4.6"
    CHAT_LOG_DIR         — default "./logs"
    CHAT_PORT            — default 8787
    CHAT_QUERY_TIMEOUT   — default 300 (seconds; watchdog budget for governed SQL execution)
    CHAT_MEMORY_LIMIT    — default "8GB" (DuckDB memory_limit, applied at connection open)
    CHAT_DATA_DIR        — default "./data" (visible session store root)
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


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
    duckdb_path: str = Field(..., min_length=1)
    chat_log_dir: str = Field(default="./logs")
    chat_port: int = Field(default=8787, ge=1, le=65535)
    query_timeout_seconds: int = Field(default=300, ge=1)
    chat_memory_limit: str = Field(default="8GB", min_length=1)
    chat_data_dir: str = Field(default="./data")


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
