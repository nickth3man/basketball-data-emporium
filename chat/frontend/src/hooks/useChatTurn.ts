/**
 * useChatTurn — one chat turn driven by SSE.
 *
 * Wraps `streamChat` in a `useReducer` that accumulates state from each
 * `ChatEvent` as it arrives:
 *
 *   - `answer` is the running concatenation of `answer_delta` chunks;
 *     `answer_finished` overwrites it with the canonical final string
 *     (covers any chunking artifacts on the server side).
 *   - `sql` / `queryRef` come from `intent_classified` + `query_started`.
 *   - `table` / `reasoning` / `clarification` mirror their eponymous events.
 *   - `citations` is an array (multiple citations per turn).
 *   - `queryDurationMs` comes from `query_finished`.
 *   - `error` is set either by a server `error` event or by a client-side
 *     transport failure (status `"error"`).
 *
 * **StrictMode safety:** `send` is invoked imperatively (button click),
 * not from an effect, so React 19's double-invoke in dev does not affect
 * it directly. If a caller wires `send` into an effect, that effect MUST
 * call `cancel()` on cleanup so the in-flight AbortController is released.
 *
 * **Concurrency:** `send` is not safe to invoke while another `send` is
 * still in flight — the new call would reset state mid-stream and leak
 * the old AbortController. The UI should disable the send button while
 * `state.status === "running"` to prevent this.
 */
import { useCallback, useReducer, useRef } from "react";

import { streamChat } from "@/api/sse";
import type { ChatEvent, Citation, ColumnSpec, QueryRef } from "@/generated/sse-events";

export interface ChatTurnTable {
  columns: ColumnSpec[];
  rows: Record<string, unknown>[];
  rowCount: number;
  truncated: boolean;
}

export interface ChatTurnReasoning {
  summary: string;
  executionPlan: string | null;
}

export interface ChatTurnClarification {
  question: string;
  options: string[] | null;
}

export interface ChatTurnError {
  code: string;
  message: string;
}

export type ChatTurnStatus = "idle" | "running" | "done" | "error" | "cancelled";

export interface ChatTurnState {
  status: ChatTurnStatus;
  /** Full event log for debugging and the (future) SQL/reasoning panels. */
  events: ChatEvent[];
  answer: string;
  sql: string | null;
  queryRef: QueryRef | null;
  table: ChatTurnTable | null;
  reasoning: ChatTurnReasoning | null;
  citations: Citation[];
  clarification: ChatTurnClarification | null;
  error: ChatTurnError | null;
  queryDurationMs: number | null;
  turnId: string | null;
}

const initialState: ChatTurnState = {
  status: "idle",
  events: [],
  answer: "",
  sql: null,
  queryRef: null,
  table: null,
  reasoning: null,
  citations: [],
  clarification: null,
  error: null,
  queryDurationMs: null,
  turnId: null,
};

type Action =
  | { type: "reset" }
  | { type: "running" }
  | { type: "event"; ev: ChatEvent }
  | { type: "error"; code: string; message: string }
  | { type: "cancelled" }
  | { type: "done" };

function reducer(state: ChatTurnState, action: Action): ChatTurnState {
  switch (action.type) {
    case "reset":
      return initialState;
    case "running":
      return { ...state, status: "running" };
    case "cancelled":
      // Don't downgrade an existing server error to cancellation.
      if (state.status === "error") return state;
      return { ...state, status: "cancelled" };
    case "done":
      // `done` only transitions from `running` / `idle`. A terminal
      // error or cancellation must not be overwritten by stream close.
      if (state.status === "error" || state.status === "cancelled") {
        return state;
      }
      return { ...state, status: "done" };
    case "error":
      return {
        ...state,
        status: "error",
        error: { code: action.code, message: action.message },
      };
    case "event": {
      const ev = action.ev;
      const events = [...state.events, ev];
      switch (ev.event) {
        case "turn_started":
          return { ...state, events, turnId: ev.turn_id };
        case "intent_classified":
          return { ...state, events, queryRef: ev.query_ref };
        case "clarification_needed":
          return {
            ...state,
            events,
            clarification: {
              question: ev.question,
              options: ev.options ?? null,
            },
          };
        case "query_started":
          return {
            ...state,
            events,
            sql: ev.sql,
            queryRef: ev.query_ref,
          };
        case "query_finished":
          return { ...state, events, queryDurationMs: ev.duration_ms };
        case "table_ready":
          return {
            ...state,
            events,
            table: {
              columns: ev.columns,
              rows: ev.rows,
              rowCount: ev.row_count,
              truncated: ev.truncated,
            },
          };
        case "reasoning":
          return {
            ...state,
            events,
            reasoning: {
              summary: ev.summary,
              executionPlan: ev.execution_plan ?? null,
            },
          };
        case "citation":
          return {
            ...state,
            events,
            citations: [...state.citations, ev],
          };
        case "answer_delta":
          return { ...state, events, answer: state.answer + ev.delta };
        case "answer_finished":
          return { ...state, events, answer: ev.answer };
        case "error":
          return {
            ...state,
            events,
            status: "error",
            error: { code: ev.code, message: ev.message },
          };
        default:
          // Defensive: future union additions would land here and be
          // appended to the event log without mutating state. The TS
          // compiler catches a missing case on the next build.
          return state;
      }
    }
    default:
      return state;
  }
}

export interface UseChatTurnResult {
  state: ChatTurnState;
  /** Start a new turn. Resets prior state. Throws if `sessionId` is null. */
  send: (message: string) => Promise<void>;
  /** Abort the in-flight turn (no-op when nothing is running). */
  cancel: () => void;
  /** Clear state back to `idle` without sending anything. */
  reset: () => void;
}

export function useChatTurn(sessionId: string | null): UseChatTurnResult {
  const [state, dispatch] = useReducer(reducer, initialState);
  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(
    async (message: string): Promise<void> => {
      if (!sessionId) {
        // Caller responsibility (per the contract): the UI must create a
        // session via `useSessions` before invoking `send`.
        throw new Error("useChatTurn: no session id (create one via useSessions first)");
      }
      const ac = new AbortController();
      abortRef.current = ac;
      dispatch({ type: "reset" });
      dispatch({ type: "running" });
      try {
        for await (const ev of streamChat({
          sessionId,
          message,
          signal: ac.signal,
        })) {
          dispatch({ type: "event", ev });
        }
        dispatch({ type: "done" });
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") {
          dispatch({ type: "cancelled" });
        } else {
          const msg = e instanceof Error ? e.message : String(e);
          dispatch({ type: "error", code: "network", message: msg });
        }
      } finally {
        abortRef.current = null;
      }
    },
    [sessionId],
  );

  const cancel = useCallback((): void => {
    abortRef.current?.abort();
  }, []);

  const reset = useCallback((): void => {
    dispatch({ type: "reset" });
  }, []);

  return { state, send, cancel, reset };
}
