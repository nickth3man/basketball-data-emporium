"""Export the ChatEvent Pydantic union JSON schema to frontend/src/generated/sse-events.schema.json.

Run from ``chat/``::

    uv run python scripts/export_sse_schema.py

CI drift guard: regenerate the file and run
``git diff --exit-code chat/frontend/src/generated/sse-events.schema.json``.
The snapshot is committed under
``chat/frontend/src/generated/sse-events.schema.json`` so the frontend drift
test can iterate the discriminator's event names without needing a live
backend process.

Why sort_keys + indent=2
------------------------
Identical Pydantic-built dicts should produce byte-identical JSON across
CI runs and developer machines; without stable ordering a regenerated
schema could trigger spurious diffs even when nothing semantic changed.
"""

from __future__ import annotations

import json
from pathlib import Path

from chat_server.events import export_json_schema


def main() -> None:
    """Dump the Pydantic-discriminated union JSON Schema to the committed snapshot."""
    schema = export_json_schema()
    out = (
        Path(__file__).resolve().parent.parent
        / "frontend"
        / "src"
        / "generated"
        / "sse-events.schema.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    # Same shape of log line as export_openapi.py so CI logs read uniformly.
    defs = schema.get("$defs", {})
    mapping = schema.get("discriminator", {}).get("mapping", {})
    print(f"wrote {out} ({len(defs)} $defs, {len(mapping)} event names)")


if __name__ == "__main__":
    main()
