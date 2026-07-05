"""Unit tests for the SSE event union (`chat_server.events`, PLAN §7.8, §9.2).

Pure-Python tests — **no DB connection required**. Covers:

* Round-trip serialization for every one of the canonical 11 events
  (`TurnStarted`, `IntentClassified`, `ClarificationNeeded`, `QueryStarted`,
  `QueryFinished`, `TableReady`, `Reasoning`, `Citation`, `AnswerDelta`,
  `AnswerFinished`, `ChatError`).
* `to_sse_dict` wire-format triple (``{"event": ..., "data": ...}``).
* Discriminator rejection of unknown `event` literals.
* **Snapshot drift guard:** `export_json_schema()` output equals the
  committed `frontend/src/generated/sse-events.schema.json`. The committed
  file is the canonical source of truth for the frontend TS union —
  divergence here is the same error class as "backend drifted, frontend
  didn't catch it".
* `jsonschema.validate` round-trip: the exported Pydantic schema is
  draft-07-compatible and successfully validates a fixture set of
  fully-built event payloads.
"""

from __future__ import annotations

import datetime as _dt
import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from jsonschema import Draft7Validator
from pydantic import ValidationError

if TYPE_CHECKING:
    # `jsonschema` ships no `.pyi` stub, so ty treats `Draft7Validator`
    # as `type[@Todo]` and rejects its use in annotations. Type it as
    # `Any` at type-check time (the runtime import above is unchanged).
    _Draft7Validator: Any = Draft7Validator
else:  # pragma: no cover - type-checker only
    _Draft7Validator = Draft7Validator

from chat_server.events import (
    AnswerDelta,
    AnswerFinished,
    ChatError,
    Citation,
    ClarificationNeeded,
    ColumnSpec,
    IntentClassified,
    QueryFinished,
    QueryStarted,
    Reasoning,
    TableReady,
    TurnStarted,
    chat_event_from_dict,
    export_json_schema,
    to_sse_dict,
)

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

# Committed frontend snapshot. Resolved from this file so the test works
# regardless of `cwd`.
_COMMITTED_SCHEMA = (
    Path(__file__).resolve().parent.parent
    / "frontend"
    / "src"
    / "generated"
    / "sse-events.schema.json"
)


# ---------------------------------------------------------------------------
# Fixtures: one realistic payload per event type
# ---------------------------------------------------------------------------


def _sample_turn_started() -> TurnStarted:
    return TurnStarted(
        session_id="sess-42",
        turn_id="turn-7",
        ts=_dt.datetime(2026, 7, 5, 12, 0, 0, tzinfo=_dt.UTC),
    )


def _sample_intent_classified() -> IntentClassified:
    return IntentClassified(template_id="season_thresholds.fifty_forty_ninety", confidence=1.0)


def _sample_clarification_needed() -> ClarificationNeeded:
    return ClarificationNeeded(
        question="Which season do you mean?",
        options=["2022-23", "2023-24", "career"],
    )


def _sample_query_started() -> QueryStarted:
    return QueryStarted(
        query_id="q1",
        template_id="season_thresholds.fifty_forty_ninety",
        sql="SELECT * FROM mart_player_season LIMIT 50",
    )


def _sample_query_finished() -> QueryFinished:
    return QueryFinished(
        query_id="q1",
        duration_ms=123.4,
        row_count=42,
        columns=["player_id", "season_year", "fg_pct"],
        truncated=False,
    )


def _sample_table_ready() -> TableReady:
    return TableReady(
        columns=[
            ColumnSpec(name="player_id", dtype="BIGINT"),
            ColumnSpec(name="season_year", dtype="VARCHAR"),
            ColumnSpec(name="fg_pct", dtype="DOUBLE"),
        ],
        rows=[
            {"player_id": 1, "season_year": "2022-23", "fg_pct": 0.5},
            {"player_id": 2, "season_year": "2023-24", "fg_pct": 0.49},
        ],
        row_count=2,
        truncated=False,
    )


def _sample_reasoning() -> Reasoning:
    return Reasoning(summary="Ran the trivial season_threshold template.", execution_plan=None)


def _sample_citation() -> Citation:
    return Citation(table_name="mart_player_season", metric_key=None, gap_key=None)


def _sample_answer_delta() -> AnswerDelta:
    return AnswerDelta(delta="Stephen Curry ")


def _sample_answer_finished() -> AnswerFinished:
    return AnswerFinished(answer="Stephen Curry shot 50/40/90 in 2015-16.")


def _sample_chat_error() -> ChatError:
    return ChatError(code="timeout", message="DuckDB query exceeded 30s.")


ALL_EVENTS = [
    pytest.param(_sample_turn_started, id="TurnStarted"),
    pytest.param(_sample_intent_classified, id="IntentClassified"),
    pytest.param(_sample_clarification_needed, id="ClarificationNeeded"),
    pytest.param(_sample_query_started, id="QueryStarted"),
    pytest.param(_sample_query_finished, id="QueryFinished"),
    pytest.param(_sample_table_ready, id="TableReady"),
    pytest.param(_sample_reasoning, id="Reasoning"),
    pytest.param(_sample_citation, id="Citation"),
    pytest.param(_sample_answer_delta, id="AnswerDelta"),
    pytest.param(_sample_answer_finished, id="AnswerFinished"),
    pytest.param(_sample_chat_error, id="ChatError"),
]


def _dump_json(model: Any) -> dict[str, Any]:
    """Dump a Pydantic model with `mode='json'` (the wire form)."""
    return model.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Round-trip + wire format
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("factory", ALL_EVENTS)
def test_each_event_serializes_and_round_trips(factory) -> None:
    """Dump each event as JSON, validate it back into the union, assert type matches."""
    original = factory()
    payload = _dump_json(original)
    # The payload is a plain dict — same shape the SSE client receives.
    rebuilt = chat_event_from_dict(payload)
    assert type(rebuilt) is type(original)
    # `model_dump(mode="json")` round-trip equality.
    assert _dump_json(rebuilt) == payload


@pytest.mark.parametrize("factory", ALL_EVENTS)
def test_each_event_round_trips_with_default_event_field(factory) -> None:
    """The `event` discriminator literal is a Pydantic default; including it
    explicitly in the payload must still validate."""
    original = factory()
    payload = _dump_json(original)
    # Inject the event key by hand (mimicking a caller that copies the SSE
    # `event:` line into the payload and forgets to strip it).
    payload_with_event = {"event": original.event, **payload}
    rebuilt = chat_event_from_dict(payload_with_event)
    assert rebuilt.event == original.event


def test_to_sse_dict_format() -> None:
    """`to_sse_dict` yields `{event, data}` with the right names per model."""
    for param in ALL_EVENTS:
        # `param.values[0]` is the `_sample_*` factory; pytest types it as
        # `object` (the parametrize generic isn't preserved). The runtime
        # type is a no-arg callable returning a `BaseModel`.
        factory = cast("Callable[[], Any]", param.values[0])
        original = factory()
        wire = to_sse_dict(original)
        assert set(wire.keys()) == {"event", "data"}
        assert wire["event"] == original.event
        # `data` is a JSON string the browser will `JSON.parse`.
        decoded = json.loads(wire["data"])
        assert decoded["event"] == original.event
        # The wire triple must be JSON-safe (no datetime leakage etc.).
        json.dumps(wire)  # would raise on non-serialisable


# ---------------------------------------------------------------------------
# Discriminator enforcement
# ---------------------------------------------------------------------------


def test_discriminator_rejects_unknown_event() -> None:
    """`{"event": "totally_made_up", ...}` raises `ValidationError`."""
    with pytest.raises(ValidationError):
        chat_event_from_dict(
            {
                "event": "totally_made_up",  # type: ignore[typeddict-item]
                "query_id": "q1",
                "template_id": "t",
                "sql": "SELECT 1",
            },
        )


def test_discriminator_rejects_unknown_event_for_non_query_started() -> None:
    """A bogus event on a payload shaped like `AnswerDelta` is rejected."""
    with pytest.raises(ValidationError):
        chat_event_from_dict({"event": "nope", "delta": "hi"})


def test_extra_fields_silently_dropped_by_default() -> None:
    """Document the current policy: Pydantic v2 default is `extra='ignore'`.

    The per-event models don't set `model_config['extra'] = 'forbid'`, so
    unexpected keys are silently dropped. This is intentional — the SSE
    pipeline may add diagnostic fields in the future without breaking
    older clients — but worth a regression test so any future switch to
    `forbid` (stricter) is a deliberate, code-reviewed change.
    """
    rebuilt = chat_event_from_dict(
        {
            "event": "query_started",
            "query_id": "q1",
            "template_id": "t",
            "sql": "SELECT 1",
            "rogue_field": "boom",
        },
    )
    # The valid fields parse through; the rogue field is dropped (Pydantic
    # default behaviour).
    assert rebuilt.event == "query_started"
    assert rebuilt.query_id == "q1"  # type: ignore[union-attr]  # narrowed by event discriminator
    assert "rogue_field" not in rebuilt.model_dump()


# ---------------------------------------------------------------------------
# Snapshot drift guard
# ---------------------------------------------------------------------------


def test_exported_schema_is_stable_and_committed(tmp_path: Path) -> None:
    """`export_json_schema()` output must equal the committed snapshot.

    This is the **drift guard**: if `chat_server.events` changes but
    `scripts/export_sse_schema.py` isn't re-run, this fails and points the
    dev at the script. The CLI command for the fix is::

        cd chat && uv run python scripts/export_sse_schema.py
    """
    fresh = json.dumps(export_json_schema(), indent=2, sort_keys=True) + "\n"
    on_disk_path = _COMMITTED_SCHEMA
    assert on_disk_path.exists(), (
        f"Committed SSE schema missing at {on_disk_path}. Run "
        "`cd chat && uv run python scripts/export_sse_schema.py` to (re)generate it."
    )
    committed = on_disk_path.read_text(encoding="utf-8")
    # Compare as text — eliminates any chance of JSON-encoder round-trip
    # nitpicks (`-0` vs `0`, key-ordering, etc.) silently masking a drift.
    assert committed == fresh


# ---------------------------------------------------------------------------
# jsonschema validation
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _validator() -> Any:
    """Build a draft-07 validator from `export_json_schema()`.

    Pydantic v2 emits JSON Schema draft-07-compatible output (`type`,
    `properties`, `required`, `$ref`/`$defs`, `oneOf`, `discriminator`).
    The `Draft7Validator` is what `check-jsonschema` wraps under the hood
    for the JSONL log fixture corpus (PLAN §14.1). Annotated as `Any`
    because `jsonschema` ships no `.pyi` (see import-block note).
    """
    return _Draft7Validator(export_json_schema())


@pytest.mark.parametrize("factory", ALL_EVENTS)
def test_sample_payloads_validate_against_schema(factory, _validator: Any) -> None:
    """Every sample payload passes `Draft7Validator` against the exported schema."""
    payload = _dump_json(factory())
    errors = sorted(_validator.iter_errors(payload), key=lambda e: list(e.path))
    assert not errors, f"validation failed: {[e.message for e in errors]}"


def test_validator_rejects_an_unknown_event() -> None:
    """The exported schema itself must also reject an unknown event literal."""
    schema = export_json_schema()
    validator = Draft7Validator(schema)
    bogus = {
        "event": "ghost_event",
        # Pick a few plausible fields so the only failure is the discriminator.
        "query_id": "q1",
        "template_id": "t",
        "sql": "SELECT 1",
    }
    errors = list(validator.iter_errors(bogus))
    assert errors, "validator accepted an event literal that does not exist in the schema"
