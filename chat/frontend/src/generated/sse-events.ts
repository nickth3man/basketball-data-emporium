/**
 * Hand-maintained SSE event union for `POST /api/chat/stream`.
 *
 * Mirrors the 11-event Pydantic discriminated union in `chat_server/events.py`.
 * **Drift-guarded** (in CI):
 *   1. `scripts/export_sse_schema.py` → writes
 *      `frontend/src/generated/sse-events.schema.json` from the live Pydantic
 *      `ChatEvent` `TypeAdapter`.
 *   2. `git diff --exit-code frontend/src/generated/sse-events.schema.json`
 *      in `.github/workflows/chat.yml` → drift guard #1.
 *   3. `frontend/src/__tests__/sse-events.drift.test.ts` → asserts every event
 *      name in the TS union matches the schema's discriminator mapping
 *      (drift guard #2) — if a backend event is added or removed, the TS union
 *      and this file go out of sync and the test fails.
 *
 * When you change `chat_server/events.py`:
 *   • Update the interfaces below to mirror the new field shapes / event names.
 *   • Re-run `uv run python scripts/export_sse_schema.py` from `chat/` so the
 *     committed schema snapshot reflects the new union.
 *   • Both drift guards will flag any divergence.
 */

/** One column in a `table_ready` preview (`ColumnSpec` in `events.py`). */
export interface ColumnSpec {
  name: string;
  /** Best-effort DuckDB dtype; `null` / omitted when unknown. */
  dtype?: string | null;
}

/** `turn_started` — first event of every turn; carries the ids + timestamp. */
export interface TurnStarted {
  event: "turn_started";
  session_id: string;
  turn_id: string;
  /** ISO-8601 string; `datetime` is dumped as such by Pydantic `mode="json"`. */
  ts: string;
}

/** `intent_classified` — agent committed to a template. */
export interface IntentClassified {
  event: "intent_classified";
  template_id: string;
  /** 1.0 in Phase 4 (no model probability exposed); see `events.py` note. */
  confidence: number;
}

/** `clarification_needed` — the agent cannot act without more input. */
export interface ClarificationNeeded {
  event: "clarification_needed";
  question: string;
  /** Optional list of suggested answers (null if absent). */
  options?: string[] | null;
}

/** `query_started` — a validated SQL query is about to run. */
export interface QueryStarted {
  event: "query_started";
  query_id: string;
  template_id: string;
      /** The rendered SQL (already validated). */
  sql: string;
}

/** `query_finished` — the query returned. */
export interface QueryFinished {
  event: "query_finished";
  query_id: string;
  /** Float milliseconds (matches `events.py: QueryFinished.duration_ms: float`). */
  duration_ms: number;
  row_count: number;
  /** DuckDB column-name list (strings only; `table_ready` carries the typed specs). */
  columns: string[];
  truncated: boolean;
}

/** `table_ready` — result rows for the evidence table. */
export interface TableReady {
  event: "table_ready";
  columns: ColumnSpec[];
  /** Each row is an object keyed by column name with JSON-safe values. */
  rows: Record<string, unknown>[];
  /** Full result row count (UI shows "N of M"); preview lives in `rows`. */
  row_count: number;
  truncated: boolean;
}

/** `reasoning` — structured (non-CoT) reasoning for the collapsible panel. */
export interface Reasoning {
  event: "reasoning";
  summary: string;
  /** Optional execution plan describing the pipeline steps taken. */
  execution_plan?: string | null;
}

/** `citation` — one provenance citation attached to a composed answer. */
export interface Citation {
  event: "citation";
  table_name?: string | null;
  metric_key?: string | null;
  gap_key?: string | null;
}

/** `answer_delta` — one chunk of the streaming answer. */
export interface AnswerDelta {
  event: "answer_delta";
  delta: string;
}

/** `answer_finished` — the full composed answer, sent once after all deltas. */
export interface AnswerFinished {
  event: "answer_finished";
  answer: string;
}

/** `error` — a non-recoverable turn-level error. */
export interface ChatError {
  event: "error";
  code: string;
  message: string;
}

/** The discriminated union consumed by the SSE client (`@/api/sse.ts`, Phase 5). */
export type ChatEvent =
  | TurnStarted
  | IntentClassified
  | ClarificationNeeded
  | QueryStarted
  | QueryFinished
  | TableReady
  | Reasoning
  | Citation
  | AnswerDelta
  | AnswerFinished
  | ChatError;

/**
 * The 11 event names — kept in declaration order to mirror `events.py`.
 * The drift test compares this list (as a `Set<string>`) against the keys
 * of the schema's `discriminator.mapping`. If Pydantic ever changes a
 * `Literal[...]` default or the union membership, the schemas and this
 * list diverge and the test fails.
 */
export const CHAT_EVENT_TYPES = [
  "turn_started",
  "intent_classified",
  "clarification_needed",
  "query_started",
  "query_finished",
  "table_ready",
  "reasoning",
  "citation",
  "answer_delta",
  "answer_finished",
  "error",
] as const;

/** The union of the event-name literals — handy for type guards. */
export type ChatEventType = (typeof CHAT_EVENT_TYPES)[number];

/**
 * Parse one SSE frame's `{event, data}` into a typed `ChatEvent`.
 *
 * @param event  Value of the SSE `event:` line (the discriminator literal).
 * @param data   Value of the SSE `data:` line (already JSON-decoded by the caller is NOT required — this fn does the `JSON.parse`).
 * @returns     A discriminated-union member. Narrow at the call site via the `event` field.
 *
 * Throws on malformed JSON. The discriminator invariant (`event` field in the
 * payload MUST equal the SSE `event:` line value) is the caller's
 * responsibility — the Pydantic backend already enforces it on the wire, so
 * the data-payload's `event` field always matches. We still override with the
 * SSE-frame `event` value as belt-and-braces (e.g. for testing).
 */
export function parseChatEvent(event: string, data: string): ChatEvent {
  const payload = JSON.parse(data) as Record<string, unknown>;
  // Spread the payload first, then override with the SSE wire-frame `event`
  // value so the wire name is authoritative — the Pydantic backend also
  // guarantees equality, but a wrong `event` key in the payload (e.g. a
  // mis-forwarded internal log line) won't sneak through.
  return { ...payload, event } as unknown as ChatEvent;
}
