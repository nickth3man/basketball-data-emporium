"""JSON-fixture manifest for the chat test suite.

Mirrors the ``web/test/fixtures/manifest.ts`` pattern in spirit, but
uses a tiny manual glob (no Vite ``import.meta.glob`` in Python). A
future Phase 6 suite can drive every fixture from this loader; for now
Phase 1 just ships the 50-40-90 fixture and the integration test
references the template directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_FIXTURES_DIR = Path(__file__).resolve().parent


def _load_one(path: Path) -> dict[str, Any]:
    """Parse one fixture JSON file, returning the raw dict."""
    with path.open(encoding="utf-8") as fh:
        result: dict[str, Any] = json.load(fh)
    return result


def load_all_fixtures() -> list[dict[str, Any]]:
    """Return every ``*.json`` fixture in this directory."""
    return [_load_one(p) for p in sorted(_FIXTURES_DIR.glob("*.json"))]


def load_fixture(fixture_id: str) -> dict[str, Any] | None:
    """Return the fixture whose ``id`` matches, or ``None`` if absent."""
    for fx in load_all_fixtures():
        if fx.get("id") == fixture_id:
            return fx
    return None


__all__ = ["load_all_fixtures", "load_fixture"]
