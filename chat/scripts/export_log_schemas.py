"""Export JSON Schemas for the JSONL log files.

The SSE event union already has a dedicated exporter (``export_sse_schema.py``)
that writes ``frontend/src/generated/sse-events.schema.json``. This script
covers the **two other JSONL streams** so they can be validated by
``check-jsonschema`` (PLAN §14.1) and the pytest suite
(``chat_tests/test_observability.py``):

* **session-message** — one line of ``chat/data/sessions/<id>.jsonl``.
  Derived from the existing ``SessionMessage`` Pydantic model in
  ``chat_server/sessions.py`` so any field changes are picked up
  automatically.
* **model-log-entry** — one line of ``chat/logs/model/<date>/<id>.jsonl``.
  Hand-written: the writer (``pipeline._write_model_log``) is a plain
  ``dict`` so there is no Pydantic model to derive from. The schema pins
  the four stable keys ``turn_id``, ``ts``, ``template_id``, ``usage``
  plus the optional ``error`` field that the writer adds on failures.

Both outputs are written to ``chat_tests/fixtures/schemas/`` (a new
directory) and committed. Drift guards in CI call this script and
``git diff --exit-code`` the resulting JSON.

Run from ``chat/``::

    uv run python scripts/export_log_schemas.py
"""

from __future__ import annotations

import json
from pathlib import Path

from chat_server.sessions import SessionMessage


def _session_message_schema() -> dict:
    """Return the JSON Schema for one ``SessionMessage``.

    Built from the Pydantic model so a future field rename is caught by
    the drift guard without hand-editing the schema file.
    """
    return SessionMessage.model_json_schema()


def _model_log_entry_schema() -> dict:
    """Return the hand-written JSON Schema for one model-log entry.

    Mirrors ``chat_server.pipeline._write_model_log``:

    * ``turn_id`` (string)        — stable per turn
    * ``ts`` (string, ISO 8601)   — UTC timestamp set at write time
    * ``template_id`` (string | null) — the resolved template, or null
      on agent failure / clarification / not-answerable paths
    * ``usage`` (object | null)   — RunUsage dataclass serialised via
      ``dataclasses.asdict`` (so all sub-keys are integers)
    * ``error`` (string | absent) — present iff the turn errored
    """
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "ModelLogEntry",
        "description": (
            "One line of chat/logs/model/<date>/<session_id>.jsonl. "
            "Emitted by chat_server.pipeline._write_model_log."
        ),
        "type": "object",
        "properties": {
            "turn_id": {
                "type": "string",
                "description": "Per-turn opaque id (secrets.token_urlsafe(8)).",
                "minLength": 1,
            },
            "ts": {
                "type": "string",
                "format": "date-time",
                "description": "ISO-8601 UTC timestamp set at write time.",
            },
            "template_id": {
                "type": ["string", "null"],
                "description": (
                    "Resolved template id (dotted), or null on clarification / "
                    "not-answerable / agent-failure paths."
                ),
            },
            "usage": {
                "type": ["object", "null"],
                "description": (
                    "Pydantic AI RunUsage serialised via dataclasses.asdict. "
                    "All sub-keys are integers."
                ),
                "properties": {
                    "requests": {"type": "integer"},
                    "tool_calls": {"type": "integer"},
                    "input_tokens": {"type": "integer"},
                    "output_tokens": {"type": "integer"},
                    "cache_write_tokens": {"type": "integer"},
                    "cache_read_tokens": {"type": "integer"},
                    "input_audio_tokens": {"type": "integer"},
                    "cache_audio_read_tokens": {"type": "integer"},
                    "output_audio_tokens": {"type": "integer"},
                    "details": {"type": "object"},
                },
                "additionalProperties": True,
            },
            "error": {
                "type": "string",
                "description": "Present iff the turn errored (f'{type}: {exc}').",
            },
        },
        "required": ["turn_id", "ts", "template_id"],
        # ``usage`` is null-able (Pydantic AI may emit 0-token records that
        # serialise cleanly, but null is also allowed). ``error`` is
        # optional and not part of the required set.
        "additionalProperties": False,
    }


def main() -> None:
    """Write both schemas to ``chat_tests/fixtures/schemas/``."""
    out_dir = Path(__file__).resolve().parent.parent / "chat_tests" / "fixtures" / "schemas"
    out_dir.mkdir(parents=True, exist_ok=True)

    targets = {
        "session-message.schema.json": _session_message_schema(),
        "model-log-entry.schema.json": _model_log_entry_schema(),
    }

    for filename, schema in targets.items():
        path = out_dir / filename
        path.write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
