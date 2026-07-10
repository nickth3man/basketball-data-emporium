"""SSE event union for the chat turn pipeline.

This module defines the **canonical 11-event discriminated union** that
streams from ``POST /api/chat/stream`` (Phase 4). The same union is
serialised to JSON Schema by ``export_json_schema()`` and consumed by
the frontend drift guard.

Why a Pydantic discriminated union
----------------------------------
* `Field(discriminator="event")` validates each payload against the
  right concrete model — a typo in `query_ref` on a
  `query_started` event surfaces as a 422 at the SSE boundary, not as
  a runtime crash in the React reducer.
* `model_dump(mode="json")` renders ``datetime`` objects as ISO 8601
  strings, which the SSE wire format requires (browsers won't parse a
  JS `Date` over a text/event-stream frame).
* `TypeAdapter(ChatEvent).json_schema()` is the single source of truth
  for the contract; ``scripts/export_sse_schema.py`` (next phase) and
  the TS handwritten union both stem from this one definition.

Why **not** a TypedDict / dataclass
-----------------------------------
A `BaseModel` union validates round-tripped payloads (essential for the
drift test + JSONL log replay) and integrates with FastAPI's
`response_class=EventSourceResponse` plumbing without bespoke adapters.
The composer's `Citation` dataclass stays a dataclass (it's an
internal-ish carrier); the wire-format ``Citation`` event here is a
Pydantic model with the same field names for ergonomic symmetry.

Add/remove events = also update
-------------------------------
(a) ``scripts/export_sse_schema.py`` (next fixer),
(b) ``frontend/src/generated/sse-events.ts`` (next fixer),
(c) the frontend reducer (Phase 5),
(d) the JSONL log schema (Phase 7).

The CI drift guard catches (a) and (b).
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter


class TurnStarted(BaseModel):
    """The first event on every turn. Carries the ids + timestamp."""

    event: Literal["turn_started"] = "turn_started"
    session_id: str
    turn_id: str
    ts: _dt.datetime


class QueryRef(BaseModel):
    """Structured provenance for a governed query's referenced tables."""

    source: Literal["catalog", "warehouse"]
    tables: list[str]


class IntentClassified(BaseModel):
    """The agent committed to a governed query. ``confidence`` remains 1.0.

    Pydantic AI's structured-output path doesn't surface a probability;
    we emit 1.0 as a stable contract so the frontend reducer can rely on
    the field type. A future enhancement can read the model's tool-call
    ranking to populate this with a real number.
    """

    event: Literal["intent_classified"] = "intent_classified"
    query_ref: QueryRef
    confidence: float


class ClarificationNeeded(BaseModel):
    """The agent cannot act without more input; surface to the user."""

    event: Literal["clarification_needed"] = "clarification_needed"
    question: str
    options: list[str] | None = None


class QueryStarted(BaseModel):
    """A validated SQL query is about to run."""

    event: Literal["query_started"] = "query_started"
    query_id: str
    query_ref: QueryRef
    sql: str


class QueryFinished(BaseModel):
    """The query returned. ``columns`` is the DuckDB column-name list."""

    event: Literal["query_finished"] = "query_finished"
    query_id: str
    duration_ms: float
    row_count: int
    columns: list[str]
    truncated: bool


class ColumnSpec(BaseModel):
    """One column in a `table_ready` preview.

    ``dtype`` is best-effort (None when unknown) so the React table
    builder can fall back to string formatting.
    """

    name: str
    dtype: str | None = None


class TableReady(BaseModel):
    """Result rows ready for the evidence table.

    ``row_count`` is the FULL result row count (so the UI can show
    "showing N of M"); ``rows`` is capped to a preview window (200 by
    default, see ``chat_server.pipeline._TABLE_PREVIEW_ROWS``) and
    ``truncated`` is True iff the preview window is shorter than the
    full result.
    """

    event: Literal["table_ready"] = "table_ready"
    columns: list[ColumnSpec]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool


class Reasoning(BaseModel):
    """Structured reasoning summary for the UI's collapsible panel.

    NEVER includes the model's chain-of-thought — only the pipeline's
    own description of what happened.
    """

    event: Literal["reasoning"] = "reasoning"
    summary: str
    execution_plan: str | None = None


class Citation(BaseModel):
    """One provenance citation attached to a composed answer.

    The composer's internal ``Citation`` dataclass (chat_server.composer)
    has identical field names; the Pydantic form is what crosses the SSE
    boundary so `to_sse_dict` can dump it without manual translation.
    """

    event: Literal["citation"] = "citation"
    table_name: str | None = None
    metric_key: str | None = None
    gap_key: str | None = None


class AnswerDelta(BaseModel):
    """One chunk of the streaming answer.

    Phase 4 splits the composed answer by sentences / fixed windows
    (not token-level — see `chat_server.pipeline._stream_answer_chunks`).
    """

    event: Literal["answer_delta"] = "answer_delta"
    delta: str


class AnswerFinished(BaseModel):
    """The full composed answer, sent once after all deltas.

    The frontend reducer appends deltas to the live bubble and uses
    this event to finalise any UI bookkeeping (cursor, scroll lock).
    """

    event: Literal["answer_finished"] = "answer_finished"
    answer: str


class ChatError(BaseModel):
    """A non-recoverable turn-level error.

    Codes are short, machine-friendly tokens the UI can switch on. The
    ``message`` field is safe to render — any `sk-or-...` / CoT content
    is redacted before it gets here.
    """

    event: Literal["error"] = "error"
    code: str
    message: str


ChatEvent = Annotated[
    TurnStarted
    | IntentClassified
    | ClarificationNeeded
    | QueryStarted
    | QueryFinished
    | TableReady
    | Reasoning
    | Citation
    | AnswerDelta
    | AnswerFinished
    | ChatError,
    Field(discriminator="event"),
]


ChatEventUnion: TypeAdapter = TypeAdapter(ChatEvent)


def export_json_schema() -> dict[str, Any]:
    """Return the JSON Schema for the 11-event discriminated union.

    Used by ``scripts/export_sse_schema.py`` (next fixer) to write the
    committed schema snapshot. Pure read of the `ChatEventUnion`
    `TypeAdapter`; no I/O.
    """
    schema: dict[str, Any] = ChatEventUnion.json_schema()
    return schema


def to_sse_dict(event: ChatEvent) -> dict[str, str]:
    """Serialise a `ChatEvent` into the SSE wire-format triple.

    Returns ``{"event": <name>, "data": <json>}``. The SSE spec also
    defines an optional ``id:`` field for client reconnection; we
    intentionally omit it for Phase 4 (the UI maintains its own
    idempotency layer via the session store).

    The ``default=str`` on ``json.dumps`` is a belt-and-braces fallback:
    ``model_dump(mode="json")`` already coerces ``datetime`` and friends,
    but row dicts may contain values the dump couldn't reach (e.g. a
    pathological Decimal subclass). Logging never crashes on a wire frame.
    """
    payload = event.model_dump(mode="json")
    return {
        "event": event.event,
        "data": json.dumps(payload, ensure_ascii=False, default=str),
    }


def chat_event_from_dict(d: dict[str, Any]) -> ChatEvent:
    """Validate ``d`` against the union and return the typed model.

    Used by the drift test (replay JSONL fixtures) and any future log
    ingestion that wants typed access. Raises ``ValidationError`` when
    ``d`` doesn't match any member.
    """
    return ChatEventUnion.validate_python(d)


__all__ = [
    "TurnStarted",
    "QueryRef",
    "IntentClassified",
    "ClarificationNeeded",
    "QueryStarted",
    "QueryFinished",
    "ColumnSpec",
    "TableReady",
    "Reasoning",
    "Citation",
    "AnswerDelta",
    "AnswerFinished",
    "ChatError",
    "ChatEvent",
    "ChatEventUnion",
    "export_json_schema",
    "to_sse_dict",
    "chat_event_from_dict",
]
