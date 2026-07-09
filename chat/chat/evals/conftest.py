"""Shared fixtures for the eval test suite (EVALS.md).

The eval suite sits between two regimes:

* **CI** runs `pytest -m "not live_llm"` and must stay green without
  OpenRouter or the warehouse. The ``live_llm`` marker names the
  expensive half; the fixtures here skip it cleanly when keys / files
  are missing.
* **Nightly / pre-merge** runs the full suite. It needs a real LLM key,
  a real warehouse, and a temp ``data/sessions/`` root per run so
  multi-turn replays don't bleed into the operator's visible history.

Skip semantics mirror ``chat_tests/conftest.py`` (``skip_no_db`` /
``db``) so anyone moving between the two packages feels at home.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from chat_server.config import get_settings, reset_settings_cache

# --- skip helpers --------------------------------------------------------


def _skip_flag_set() -> bool:
    """True when CI has explicitly disabled DB tests."""
    return os.environ.get("CHAT_SKIP_DB_TESTS", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _db_available() -> bool:
    """True when the warehouse file exists and the skip flag is unset."""
    if _skip_flag_set():
        return False
    try:
        path = get_settings().duckdb_path
    except Exception:
        return False
    return bool(path) and os.path.exists(path)


#: Skip marker for tests that need a live warehouse. Mirrors the
#: ``chat_tests.conftest.skip_no_db`` naming so a future doc cross-link
#: is trivial.
skip_no_warehouse = pytest.mark.skipif(not _db_available(), reason="warehouse not available")


def _llm_key_present() -> bool:
    """True when ``OPENROUTER_API_KEY`` is set to a non-placeholder value.

    The .env.example ships ``sk-or-...`` as a placeholder; we treat any
    value that contains ``...`` (the placeholder convention) as absent
    so a fresh checkout doesn't pretend it has credentials.
    """
    try:
        key = get_settings().openrouter_api_key
    except Exception:
        return False
    return bool(key) and "..." not in key


#: Skip marker for tests that hit OpenRouter. Applied at the test
#: level via ``@pytest.mark.live_llm``; the marker doubles as a
#: selector for ``pytest -m live_llm`` runs.
skip_no_llm = pytest.mark.skipif(not _llm_key_present(), reason="OPENROUTER_API_KEY missing")


# --- fixtures ------------------------------------------------------------


@pytest.fixture(scope="session")
def warehouse_path() -> str:
    """The configured DuckDB path (resolved from settings)."""
    return get_settings().duckdb_path


@pytest.fixture
def governed_sql_mode_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Force ``chat_governed_sql_mode=True`` for the duration of one test.

    The eval baseline runs governed (per EVALS.md); without this flag
    the agent falls back to the legacy template prompt and the
    plan-object grading is meaningless. We set the env var and reset
    the cached settings + agent singletons so the change takes effect
    on the next ``get_agent()`` / ``get_settings()`` call.
    """
    monkeypatch.setenv("CHAT_GOVERNED_SQL_MODE", "1")
    reset_settings_cache()
    # The agent singleton captures the prompt at first build; force a
    # rebuild so the next call sees the new flag.
    try:
        from chat_server.agent import reset_agent_for_tests

        reset_agent_for_tests()
    except Exception:  # pragma: no cover - defensive
        pass
    try:
        yield
    finally:
        # ``monkeypatch.setenv`` reverses itself, but the settings
        # cache + agent singleton outlive the test -- clear both.
        reset_settings_cache()
        try:
            from chat_server.agent import reset_agent_for_tests

            reset_agent_for_tests()
        except Exception:  # pragma: no cover
            pass


@pytest.fixture
def temp_sessions_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """A session-scoped-ish temp directory the eval replay can use as
    ``data/sessions/``.

    We point the real ``Settings.chat_data_dir`` at ``tmp_path`` for the
    duration of the test and reset the cached settings + store
    singleton. The store's ``__init__`` creates ``<root>/sessions/`` so
    we don't need to pre-create anything.
    """
    monkeypatch.setenv("CHAT_DATA_DIR", str(tmp_path))
    reset_settings_cache()
    # Drop the cached store + agent singletons so the next ``get_store``
    # rebuilds them rooted at our temp dir.
    try:
        from chat_server.sessions import reset_store_for_tests

        reset_store_for_tests()
    except Exception:  # pragma: no cover
        pass
    try:
        from chat_server.agent import reset_agent_for_tests

        reset_agent_for_tests()
    except Exception:  # pragma: no cover
        pass

    yield tmp_path

    reset_settings_cache()
    try:
        from chat_server.sessions import reset_store_for_tests

        reset_store_for_tests()
    except Exception:  # pragma: no cover
        pass
    try:
        from chat_server.agent import reset_agent_for_tests

        reset_agent_for_tests()
    except Exception:  # pragma: no cover
        pass


__all__ = [
    "skip_no_warehouse",
    "skip_no_llm",
    "governed_sql_mode_on",
    "temp_sessions_root",
]
