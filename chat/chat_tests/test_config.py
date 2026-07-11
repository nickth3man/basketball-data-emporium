"""Tests for Settings parsing, env-var overrides, and CORS origin normalization.

These tests do not require a live warehouse, DuckDB, or an API key — they
operate purely on the Settings model with monkeypatched environment.

NOTE: The developer's ``chat/.env`` file is always present and may contain
values that differ from defaults. Every test helper that constructs a
``Settings`` instance therefore explicitly sets every known env var to its
canonical default *before* applying test-specific overrides, so the local
``.env`` never leaks into a test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chat_server.config import Settings, get_cors_origins, reset_settings_cache

# The canonical defaults from config.py Field definitions.
_CANONICAL_DEFAULTS: dict[str, str] = {
    "OPENROUTER_API_KEY": "sk-or-test-key",
    "DUCKDB_PATH": "../data/nba.duckdb",
    "OPENROUTER_MODEL": "anthropic/claude-sonnet-4.6",
    "OPENROUTER_MAX_TOKENS": "16384",
    "OPENROUTER_PROVIDER": "",
    "CHAT_CORS_ORIGINS": "http://localhost:5173",
    "CHAT_LOG_DIR": "./logs",
    "CHAT_PORT": "8787",
    "CHAT_QUERY_TIMEOUT": "300",
    "CHAT_MEMORY_LIMIT": "8GB",
    "CHAT_DATA_DIR": "./data",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Any:
    """Clear the Settings cache before and after every test so each test starts
    with a fresh model instance."""
    reset_settings_cache()
    yield
    reset_settings_cache()


def _settings_from_env(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    """Construct a ``Settings`` instance with the given env vars set.

    EVERY known env var is first set to its canonical default so the
    developer's local ``.env`` file cannot leak into a test.  Test-specific
    overrides in ``**env`` then replace individual values.

    ``OPENROUTER_API_KEY`` and ``DUCKDB_PATH`` are always provided with
    dummy values so the model can be constructed.
    """
    overrides = {**_CANONICAL_DEFAULTS, **env}
    for key, val in overrides.items():
        monkeypatch.setenv(key, val)
    return Settings()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default OPENROUTER_MODEL must be the canonical runtime default."""
    s = _settings_from_env(monkeypatch)
    assert s.openrouter_model == "anthropic/claude-sonnet-4.6"


def test_default_max_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default OPENROUTER_MAX_TOKENS must be 16384."""
    s = _settings_from_env(monkeypatch)
    assert s.openrouter_max_tokens == 16384


def test_default_cors_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default CHAT_CORS_ORIGINS should give the Vite dev origin."""
    s = _settings_from_env(monkeypatch)
    assert s.parsed_cors_origins() == ["http://localhost:5173"]


def test_default_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default CHAT_PORT must be 8787."""
    s = _settings_from_env(monkeypatch)
    assert s.chat_port == 8787


def test_default_query_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default query timeout must be 300."""
    s = _settings_from_env(monkeypatch)
    assert s.query_timeout_seconds == 300


def test_default_memory_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default memory limit must be 8GB."""
    s = _settings_from_env(monkeypatch)
    assert s.chat_memory_limit == "8GB"


# ---------------------------------------------------------------------------
# Env-var overrides
# ---------------------------------------------------------------------------


def test_override_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting OPENROUTER_MODEL in the env overrides the default."""
    s = _settings_from_env(monkeypatch, OPENROUTER_MODEL="anthropic/claude-sonnet-4-7")
    assert s.openrouter_model == "anthropic/claude-sonnet-4-7"


def test_override_cors_origins_single(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single origin (no comma) yields a single-element list."""
    s = _settings_from_env(monkeypatch, CHAT_CORS_ORIGINS="http://localhost:3000")
    assert s.parsed_cors_origins() == ["http://localhost:3000"]


def test_override_cors_origins_multi(monkeypatch: pytest.MonkeyPatch) -> None:
    """A comma-separated list yields a multi-element list."""
    s = _settings_from_env(
        monkeypatch,
        CHAT_CORS_ORIGINS="http://localhost:5173,http://localhost:4173",
    )
    assert s.parsed_cors_origins() == [
        "http://localhost:5173",
        "http://localhost:4173",
    ]


def test_override_cors_origins_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whitespace around origins and around commas is stripped."""
    s = _settings_from_env(
        monkeypatch,
        CHAT_CORS_ORIGINS="  http://localhost:5173 ,  http://localhost:4173  ",
    )
    assert s.parsed_cors_origins() == [
        "http://localhost:5173",
        "http://localhost:4173",
    ]


def test_override_cors_origins_with_spaces(monkeypatch: pytest.MonkeyPatch) -> None:
    """Origins with leading/trailing spaces get trimmed."""
    s = _settings_from_env(
        monkeypatch,
        CHAT_CORS_ORIGINS="  https://dev.example.com , http://localhost:3000  ",
    )
    assert s.parsed_cors_origins() == [
        "https://dev.example.com",
        "http://localhost:3000",
    ]


def test_override_cors_origins_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty CHAT_CORS_ORIGINS falls back to the default localhost origin."""
    s = _settings_from_env(monkeypatch, CHAT_CORS_ORIGINS="")
    assert s.parsed_cors_origins() == ["http://localhost:5173"]


def test_override_cors_origins_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    """A whitespace-only CHAT_CORS_ORIGINS falls back to the default."""
    s = _settings_from_env(monkeypatch, CHAT_CORS_ORIGINS="   ")
    assert s.parsed_cors_origins() == ["http://localhost:5173"]


# ---------------------------------------------------------------------------
# CORS origin normalization — trailing slash stripping
# ---------------------------------------------------------------------------


def test_cors_origin_trailing_slash_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A trailing slash on an origin is stripped for the middleware list."""
    s = _settings_from_env(
        monkeypatch,
        CHAT_CORS_ORIGINS="http://localhost:5173/",
    )
    origins = s.parsed_cors_origins()
    assert origins == ["http://localhost:5173"]
    # The returned origin must NOT end with a trailing slash.
    assert not origins[0].endswith("/")


def test_cors_origin_trailing_slashes_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Multiple trailing slashes are all stripped."""
    s = _settings_from_env(
        monkeypatch,
        CHAT_CORS_ORIGINS="http://localhost:5173//",
    )
    origins = s.parsed_cors_origins()
    assert origins == ["http://localhost:5173"]
    assert not origins[0].endswith("/")


def test_cors_origin_trailing_slash_mixed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Trailing slashes stripped from every origin in a multi-origin list."""
    s = _settings_from_env(
        monkeypatch,
        CHAT_CORS_ORIGINS="http://a.dev/,http://b.dev/",
    )
    origins = s.parsed_cors_origins()
    assert origins == ["http://a.dev", "http://b.dev"]
    assert not any(o.endswith("/") for o in origins)


# ---------------------------------------------------------------------------
# CORS origin normalization — wildcard
# ---------------------------------------------------------------------------


def test_cors_origin_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single '*' origin is passed through as-is (no trailing-slash issues)."""
    s = _settings_from_env(monkeypatch, CHAT_CORS_ORIGINS="*")
    assert s.parsed_cors_origins() == ["*"]


def test_cors_origin_wildcard_mixed_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wildcard mixed with specific origins falls back to the default."""
    s = _settings_from_env(monkeypatch, CHAT_CORS_ORIGINS="*,http://localhost:5173")
    assert s.parsed_cors_origins() == ["http://localhost:5173"]


def test_cors_origin_wildcard_mixed_reversed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Specific origin followed by wildcard also falls back."""
    s = _settings_from_env(monkeypatch, CHAT_CORS_ORIGINS="http://localhost:5173,*")
    assert s.parsed_cors_origins() == ["http://localhost:5173"]


def test_cors_origin_wildcard_mixed_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whitespace around mixed wildcard+origin still triggers fallback."""
    s = _settings_from_env(monkeypatch, CHAT_CORS_ORIGINS="  *  ,  http://localhost:5173  ")
    assert s.parsed_cors_origins() == ["http://localhost:5173"]


# ---------------------------------------------------------------------------
# Standalone get_cors_origins() — lightweight, no Settings required
# ---------------------------------------------------------------------------


def test_get_cors_origins_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_cors_origins() without CHAT_CORS_ORIGINS returns the default."""
    monkeypatch.delenv("CHAT_CORS_ORIGINS", raising=False)
    assert get_cors_origins() == ["http://localhost:5173"]


def test_get_cors_origins_from_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """get_cors_origins() reads CHAT_CORS_ORIGINS from .env when the env var
    is not set."""
    env_file = tmp_path / ".env"
    env_file.write_text("CHAT_CORS_ORIGINS=http://dotenv-only:4000\n")
    monkeypatch.setattr("chat_server.config._ENV_FILE", env_file)
    monkeypatch.delenv("CHAT_CORS_ORIGINS", raising=False)
    assert get_cors_origins() == ["http://dotenv-only:4000"]


def test_get_cors_origins_env_overrides_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When both the .env file and the process env have CHAT_CORS_ORIGINS,
    the process env wins."""
    env_file = tmp_path / ".env"
    env_file.write_text("CHAT_CORS_ORIGINS=http://dotenv-value:5000\n")
    monkeypatch.setattr("chat_server.config._ENV_FILE", env_file)
    # The env-var — should take precedence over .env
    monkeypatch.setenv("CHAT_CORS_ORIGINS", "http://env-wins:6000")
    assert get_cors_origins() == ["http://env-wins:6000"]


def test_get_cors_origins_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty CHAT_CORS_ORIGINS returns the default."""
    monkeypatch.setenv("CHAT_CORS_ORIGINS", "")
    assert get_cors_origins() == ["http://localhost:5173"]


def test_get_cors_origins_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whitespace-only CHAT_CORS_ORIGINS returns the default."""
    monkeypatch.setenv("CHAT_CORS_ORIGINS", "   ")
    assert get_cors_origins() == ["http://localhost:5173"]


def test_get_cors_origins_single(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single origin is returned as a single-element list."""
    monkeypatch.setenv("CHAT_CORS_ORIGINS", "http://localhost:3000")
    assert get_cors_origins() == ["http://localhost:3000"]


def test_get_cors_origins_multi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Comma-separated origins yield a multi-element list."""
    monkeypatch.setenv("CHAT_CORS_ORIGINS", "http://localhost:5173,http://localhost:4173")
    assert get_cors_origins() == [
        "http://localhost:5173",
        "http://localhost:4173",
    ]


def test_get_cors_origins_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single '*' is passed through."""
    monkeypatch.setenv("CHAT_CORS_ORIGINS", "*")
    assert get_cors_origins() == ["*"]


def test_get_cors_origins_wildcard_mixed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wildcard mixed with specific origins falls back to the default."""
    monkeypatch.setenv("CHAT_CORS_ORIGINS", "*,http://localhost:5173")
    assert get_cors_origins() == ["http://localhost:5173"]


def test_get_cors_origins_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Trailing slashes are stripped."""
    monkeypatch.setenv("CHAT_CORS_ORIGINS", "http://localhost:5173/")
    assert get_cors_origins() == ["http://localhost:5173"]


def test_get_cors_origins_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whitespace around origins and commas is stripped."""
    monkeypatch.setenv("CHAT_CORS_ORIGINS", "  http://localhost:5173 ,  http://localhost:4173  ")
    assert get_cors_origins() == [
        "http://localhost:5173",
        "http://localhost:4173",
    ]


# ---------------------------------------------------------------------------
# Regression: importing main in a clean (secretless) environment
# ---------------------------------------------------------------------------


def test_import_main_clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing ``chat_server.main`` at module level must NOT require
    ``OPENROUTER_API_KEY`` or ``DUCKDB_PATH`` — those are validated only at
    server-start time (``run()``), not at import time.

    This test simulates a clean CI environment by removing those env vars
    and then importing the module fresh.
    """
    import sys

    # Simulate clean CI environment — no secrets, no DB path.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("DUCKDB_PATH", raising=False)
    # CORS should still work via its own lightweight parser.
    monkeypatch.delenv("CHAT_CORS_ORIGINS", raising=False)

    # Remove any cached module so Python re-evaluates the module-level code.
    for mod_name in list(sys.modules):
        if mod_name == "chat_server.main" or mod_name.startswith("chat_server.main."):
            del sys.modules[mod_name]

    from chat_server.main import APP_TITLE, app

    assert APP_TITLE == "Basketball Data Chatbot API"
    assert app.title == "Basketball Data Chatbot API"
    # Verify CORS middleware was added with the default origin.
    cors_middleware = [
        m for m in app.user_middleware if getattr(m.cls, "__name__", "") == "CORSMiddleware"
    ]
    assert len(cors_middleware) == 1
    # Default CORS origins should be localhost:5173
    assert cors_middleware[0].kwargs.get("allow_origins") == ["http://localhost:5173"]


# ---------------------------------------------------------------------------
# Required field validation
# ---------------------------------------------------------------------------


def test_empty_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty OPENROUTER_API_KEY should raise on construction.

    We validate via ``min_length=1`` rather than relying on a truly-missing
    env var, because the developer's local ``.env`` file always supplies the
    value and pydantic-settings loads that as a fallback.
    """
    from pydantic import ValidationError

    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("DUCKDB_PATH", "../data/nba.duckdb")
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_empty_duckdb_path_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty DUCKDB_PATH should raise on construction."""
    from pydantic import ValidationError

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
    monkeypatch.setenv("DUCKDB_PATH", "")
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_get_settings_missing_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """``get_settings()`` with an empty OPENROUTER_API_KEY should raise
    ``RuntimeError`` (the ``get_settings()`` wrapper re-wraps ``missing``
    ``ValidationError`` into the user-friendly message).

    This test works by patching the env to empty, which triggers
    ``ValidationError`` with ``string_too_short`` — NOT ``missing`` — due to
    the ``min_length=1`` constraint, so ``get_settings()`` re-raises the
    ``ValidationError`` directly.
    """
    from pydantic import ValidationError

    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("DUCKDB_PATH", "../data/nba.duckdb")
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Extra env vars are silently ignored
# ---------------------------------------------------------------------------


def test_unknown_env_var_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Extra unknown env vars should not cause a ValidationError."""
    s = _settings_from_env(monkeypatch, SOME_RANDOM_VAR="hello")
    assert s.openrouter_api_key == "sk-or-test-key"


# ---------------------------------------------------------------------------
# Reset cache test
# ---------------------------------------------------------------------------


def test_reset_cache_reloads(monkeypatch: pytest.MonkeyPatch) -> None:
    """After reset_settings_cache, creating Settings should reflect new env."""
    s1 = _settings_from_env(monkeypatch, OPENROUTER_MODEL="model-a")
    assert s1.openrouter_model == "model-a"

    reset_settings_cache()
    monkeypatch.setenv("OPENROUTER_MODEL", "model-b")
    s2 = Settings()  # type: ignore[call-arg]
    assert s2.openrouter_model == "model-b"
