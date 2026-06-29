"""Shared fixtures for the OpenAPI drift gate tests.

The committed TypeScript types file lives in the frontend repo. We
expose its absolute path as a session-scoped fixture so the drift test
can read it for comparison.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def committed_types_path() -> str:
    """Absolute path to the committed `frontend/src/lib/openapi-types.ts`."""
    return os.path.normpath(
        os.path.join(
            os.path.dirname(__file__),  # backend/tests/openapi/
            "..",  # backend/tests/
            "..",  # backend/
            "..",  # repo root
            "frontend",
            "src",
            "lib",
            "openapi-types.ts",
        )
    )


@pytest.fixture(scope="session")
def frontend_dir() -> str:
    """Absolute path to the `frontend/` directory (where `npx` lives)."""
    return os.path.normpath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "..",
            "frontend",
        )
    )
