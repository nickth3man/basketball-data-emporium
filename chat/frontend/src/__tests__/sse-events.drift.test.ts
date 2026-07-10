/**
 * SSE event drift guard.
 *
 * Why this test exists
 * --------------------
 * The ChatEvent Pydantic discriminated union (`chat_server/events.py`) is
 * the canonical contract for the SSE wire format. Its JSON Schema is
 * committed at `../generated/sse-events.schema.json` and is itself
 * drift-guarded by CI (guard #1: regenerate + `git diff --exit-code`).
 * This test is the third line of defence: it reads the **committed**
 * schema and asserts that the hand-maintained TS union in
 * `../generated/sse-events.ts` covers exactly the same event names.
 *
 * If a fixer's PR adds or removes an event in `events.py` without
 * updating `sse-events.ts`, this test fails before CI even runs.
 */

/* Imports follow `consistent-type-imports`: `type ChatEvent` is used as a
   type-only, while `CHAT_EVENT_TYPES` and `parseChatEvent` are values.
   The JSON import below intentionally omits the `type` keyword so the
   schema object keeps its discriminated-union shape (a typed `Record`,
   not `unknown`). */

import { describe, it, expect } from "vitest";

import { CHAT_EVENT_TYPES, type ChatEvent, parseChatEvent } from "../generated/sse-events";

// The JSON is committed next to the TS union (drift-guarded separately).
// `resolveJsonModule` is on (see `tsconfig.app.json`).
import sseSchema from "../generated/sse-events.schema.json";

// ---------------------------------------------------------------------------
// Schema event-name extraction.
//
// Pydantic v2 emits a discriminated union as a `oneOf` of `$ref`s with a
// top-level `discriminator` block whose `mapping` is `{ <eventName>:
// "#/$defs/<ClassName>" }`. That mapping's keys are the **authoritative**
// list of event literals — every member of the union participates, and the
// keys match the Literal[...] defaults on each model's `event` field.
//
// We deliberately do NOT derive event names from `$defs` keys: `ColumnSpec`
// is also a `$def` but is never a top-level union member. Likewise, scanning
// every `const` value in every `event` property would re-introduce the
// redundancy the discriminator gives us for free.
// ---------------------------------------------------------------------------

interface PydanticDiscriminatedSchema {
  oneOf?: Array<{ $ref: string }>;
  discriminator?: {
    propertyName: string;
    mapping?: Record<string, string>;
  };
}

function extractSchemaEventNames(schema: unknown): string[] {
  // Narrow defensively — if the schema ever loses its discriminator we want
  // a clear failure ("map") rather than a silent zero-event match.
  const s = schema as PydanticDiscriminatedSchema;
  if (!s.discriminator || !s.discriminator.mapping) {
    throw new Error(
      "sse-events.schema.json is missing `discriminator.mapping`; " +
        "the Pydantic export may have lost its `Field(discriminator=...)`. " +
        "Re-check chat_server.events.ChatEvent.",
    );
  }
  if (s.discriminator.propertyName !== "event") {
    throw new Error(
      `schema discriminator property is "${s.discriminator.propertyName}", ` +
        'expected "event". Drift the TS union deliberately after the Pydantic ' +
        "side is repaired.",
    );
  }
  return Object.keys(s.discriminator.mapping).sort();
}

// ---------------------------------------------------------------------------

describe("SSE event drift guard", () => {
  const schemaEventNames = extractSchemaEventNames(sseSchema);

  it("the committed schema advertises exactly 11 events (sanity)", () => {
    // Belt-and-braces: if a future Pydantic release ever flattens the
    // union or duplicates keys, this catches it before the equality
    // check below becomes ambiguous.
    expect(schemaEventNames).toHaveLength(11);
  });

  it("TS union covers every event name in the Pydantic schema", () => {
    expect(new Set(schemaEventNames)).toEqual(new Set(CHAT_EVENT_TYPES));
  });

  it("every TS event name has a matching schema entry (no orphans)", () => {
    // Reverse direction — catches the case where someone adds a new TS
    // union member but forgets to regenerate the schema (which would
    // silently ship a TS-only event into the wire contract).
    const schemaSet = new Set(schemaEventNames);
    for (const name of CHAT_EVENT_TYPES) {
      expect(schemaSet.has(name)).toBe(true);
    }
  });

  it("TS and schema names have matching sorted sets (catch impl-detail drift)", () => {
    // Sanity check that the two lists are alphabetically consistent —
    // Pydantic emits the discriminator mapping in alphabetical order, so
    // if our hand-maintained TS list drifts away (renamed event, duplicate
    // entry, accidental sort), the sorted-equality fails *and* names the
    // offender, rather than the set-equality tests giving a noisy diff.
    expect([...CHAT_EVENT_TYPES].sort()).toEqual(schemaEventNames);
  });
});

describe("parseChatEvent", () => {
  it("parses a turn_started frame into a TurnStarted member", () => {
    const e = parseChatEvent(
      "turn_started",
      JSON.stringify({
        session_id: "sess-1",
        turn_id: "turn-1",
        ts: "2026-07-05T12:00:00Z",
      }),
    ) as Extract<ChatEvent, { event: "turn_started" }>;
    expect(e.event).toBe("turn_started");
    expect(e.session_id).toBe("sess-1");
    expect(e.turn_id).toBe("turn-1");
    expect(e.ts).toBe("2026-07-05T12:00:00Z");
  });

  it("parses an answer_delta frame into an AnswerDelta member", () => {
    const e = parseChatEvent("answer_delta", JSON.stringify({ delta: "Hello, world" })) as Extract<
      ChatEvent,
      { event: "answer_delta" }
    >;
    expect(e.event).toBe("answer_delta");
    expect(e.delta).toBe("Hello, world");
  });

  it("SSE frame `event` value wins over the payload's `event` field", () => {
    // Belt-and-braces: the wire-frame `event:` line is authoritative
    // even if the payload's `event` key disagrees.
    const e = parseChatEvent(
      "query_started",
      JSON.stringify({
        event: "anything_else",
        query_id: "q1",
        query_ref: { source: "catalog", tables: ["mart_player_season"] },
        sql: "SELECT 1",
      }),
    ) as Extract<ChatEvent, { event: "query_started" }>;
    expect(e.event).toBe("query_started");
    expect(e.query_id).toBe("q1");
    expect(e.sql).toBe("SELECT 1");
  });

  it("throws on malformed JSON", () => {
    expect(() => parseChatEvent("turn_started", "not json {")).toThrow();
  });
});
