"""Export the FastAPI app's OpenAPI schema to frontend/openapi.json.

Run from ``chat/``::

    uv run python scripts/export_openapi.py

CI drift guard: regenerate the file and run
``git diff --exit-code frontend/openapi.json``. The snapshot is committed
under ``chat/frontend/openapi.json`` so ``openapi-typescript`` can type the
frontend REST client against the committed schema without a live backend.
"""

from __future__ import annotations

import json
from pathlib import Path

from chat_server.main import app


def main() -> None:
    """Dump ``app.openapi()`` to ``frontend/openapi.json`` (stable JSON)."""
    schema = app.openapi()
    # Resolve to <chat>/frontend/openapi.json regardless of cwd so the
    # command works from anywhere inside the repo.
    out = Path(__file__).resolve().parent.parent / "frontend" / "openapi.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    # Sort keys + indent so diffs are stable across re-runs (CI drift guard).
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(schema.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
