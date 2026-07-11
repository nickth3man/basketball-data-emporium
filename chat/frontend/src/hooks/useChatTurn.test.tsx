import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { streamChat } from "@/api/sse";
import {
  useChatTurn,
  chatTurnReducer,
  chatTurnInitialState,
  type ChatTurnState,
} from "@/hooks/useChatTurn";

vi.mock("@/api/sse", () => ({ streamChat: vi.fn() }));

const mockedStreamChat = vi.mocked(streamChat);

/** Yield a single event then complete. */
async function* singleEvent<T>(ev: T): AsyncGenerator<T> {
  yield ev;
}

/** Yield multiple events in sequence then complete. */
async function* multiEvent<T>(...evs: T[]): AsyncGenerator<T> {
  for (const ev of evs) yield ev;
}

async function* clarificationStream() {
  yield {
    event: "clarification_needed" as const,
    question: "Which season?",
    options: ["2024-25"],
  };
}

async function* answerStream() {
  yield { event: "answer_finished" as const, answer: "The answer." };
}

// ---------------------------------------------------------------------------
// Reducer direct tests (for invariants unreachable via the public API)
// ---------------------------------------------------------------------------
describe("reducer (direct)", () => {
  describe("terminal state guards", () => {
    it("done after error stays error", () => {
      const state: ChatTurnState = { ...chatTurnInitialState, status: "error" };
      const next = chatTurnReducer(state, { type: "done" });
      expect(next.status).toBe("error");
    });

    it("done after cancelled stays cancelled", () => {
      const state: ChatTurnState = { ...chatTurnInitialState, status: "cancelled" };
      const next = chatTurnReducer(state, { type: "done" });
      expect(next.status).toBe("cancelled");
    });

    it("done after awaiting_clarification stays awaiting_clarification", () => {
      const state: ChatTurnState = {
        ...chatTurnInitialState,
        status: "awaiting_clarification",
      };
      const next = chatTurnReducer(state, { type: "done" });
      expect(next.status).toBe("awaiting_clarification");
    });

    it("cancelled after error stays error", () => {
      const state: ChatTurnState = { ...chatTurnInitialState, status: "error" };
      const next = chatTurnReducer(state, { type: "cancelled" });
      expect(next.status).toBe("error");
    });
  });

  describe("reset action", () => {
    it("returns initial state from any status", () => {
      const dirty: ChatTurnState = {
        ...chatTurnInitialState,
        status: "done",
        answer: "some answer",
        sql: "SELECT 1",
        citations: [{ event: "citation", table_name: "t" }],
      };
      const next = chatTurnReducer(dirty, { type: "reset" });
      expect(next).toEqual(chatTurnInitialState);
    });
  });
});

// ---------------------------------------------------------------------------
// Hook integration tests
// ---------------------------------------------------------------------------
describe("useChatTurn", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("event reducer", () => {
    it("turn_started sets turnId", async () => {
      mockedStreamChat.mockReturnValueOnce(
        singleEvent({
          event: "turn_started",
          session_id: "s1",
          turn_id: "t1",
          ts: "2026-01-01T00:00:00Z",
        }),
      );
      const { result } = renderHook(() => useChatTurn("session-1"));
      await act(async () => {
        await result.current.send("hello");
      });
      await waitFor(() => expect(result.current.state.status).toBe("done"));
      expect(result.current.state.turnId).toBe("t1");
    });

    it("intent_classified sets queryRef", async () => {
      mockedStreamChat.mockReturnValueOnce(
        singleEvent({
          event: "intent_classified",
          query_ref: { source: "catalog", tables: ["dim_player"] },
          confidence: 1.0,
        }),
      );
      const { result } = renderHook(() => useChatTurn("session-1"));
      await act(async () => {
        await result.current.send("hello");
      });
      await waitFor(() => expect(result.current.state.status).toBe("done"));
      expect(result.current.state.queryRef).toEqual({
        source: "catalog",
        tables: ["dim_player"],
      });
    });

    it("query_started sets sql and queryRef", async () => {
      mockedStreamChat.mockReturnValueOnce(
        singleEvent({
          event: "query_started",
          query_id: "q1",
          query_ref: { source: "catalog", tables: ["fact_player_season"] },
          sql: "SELECT * FROM fact_player_season",
        }),
      );
      const { result } = renderHook(() => useChatTurn("session-1"));
      await act(async () => {
        await result.current.send("hello");
      });
      await waitFor(() => expect(result.current.state.status).toBe("done"));
      expect(result.current.state.sql).toBe("SELECT * FROM fact_player_season");
      expect(result.current.state.queryRef).toEqual({
        source: "catalog",
        tables: ["fact_player_season"],
      });
    });

    it("query_finished sets queryDurationMs", async () => {
      mockedStreamChat.mockReturnValueOnce(
        singleEvent({
          event: "query_finished",
          query_id: "q1",
          duration_ms: 1234,
          row_count: 100,
          columns: ["player"],
          truncated: false,
        }),
      );
      const { result } = renderHook(() => useChatTurn("session-1"));
      await act(async () => {
        await result.current.send("hello");
      });
      await waitFor(() => expect(result.current.state.status).toBe("done"));
      expect(result.current.state.queryDurationMs).toBe(1234);
    });

    it("table_ready sets table with columns rows rowCount truncated", async () => {
      mockedStreamChat.mockReturnValueOnce(
        singleEvent({
          event: "table_ready",
          columns: [{ name: "player" }, { name: "pts" }],
          rows: [{ player: "LeBron", pts: 30 }],
          row_count: 1,
          truncated: false,
        }),
      );
      const { result } = renderHook(() => useChatTurn("session-1"));
      await act(async () => {
        await result.current.send("hello");
      });
      await waitFor(() => expect(result.current.state.status).toBe("done"));
      expect(result.current.state.table).toEqual({
        columns: [{ name: "player" }, { name: "pts" }],
        rows: [{ player: "LeBron", pts: 30 }],
        rowCount: 1,
        truncated: false,
      });
    });

    it("reasoning sets summary and executionPlan", async () => {
      mockedStreamChat.mockReturnValueOnce(
        singleEvent({
          event: "reasoning",
          summary: "Found the best player",
          execution_plan: "1. Query dim_player\n2. Filter by season",
        }),
      );
      const { result } = renderHook(() => useChatTurn("session-1"));
      await act(async () => {
        await result.current.send("hello");
      });
      await waitFor(() => expect(result.current.state.status).toBe("done"));
      expect(result.current.state.reasoning).toEqual({
        summary: "Found the best player",
        executionPlan: "1. Query dim_player\n2. Filter by season",
      });
    });

    it("citation appends to citations array (two in sequence)", async () => {
      mockedStreamChat.mockReturnValueOnce(
        multiEvent(
          { event: "citation" as const, table_name: "dim_player" },
          { event: "citation" as const, metric_key: "pts" },
        ),
      );
      const { result } = renderHook(() => useChatTurn("session-1"));
      await act(async () => {
        await result.current.send("hello");
      });
      await waitFor(() => expect(result.current.state.status).toBe("done"));
      expect(result.current.state.citations).toHaveLength(2);
      expect(result.current.state.citations[0]).toEqual({
        event: "citation",
        table_name: "dim_player",
      });
      expect(result.current.state.citations[1]).toEqual({
        event: "citation",
        metric_key: "pts",
      });
    });

    it("answer_delta concatenates to answer (two deltas)", async () => {
      mockedStreamChat.mockReturnValueOnce(
        multiEvent(
          { event: "answer_delta" as const, delta: "Hello " },
          { event: "answer_delta" as const, delta: "World" },
        ),
      );
      const { result } = renderHook(() => useChatTurn("session-1"));
      await act(async () => {
        await result.current.send("hello");
      });
      await waitFor(() => expect(result.current.state.status).toBe("done"));
      expect(result.current.state.answer).toBe("Hello World");
    });

    it("answer_finished overwrites answer (not concatenated)", async () => {
      mockedStreamChat.mockReturnValueOnce(
        multiEvent(
          { event: "answer_delta" as const, delta: "Partial " },
          { event: "answer_finished" as const, answer: "Canonical answer" },
        ),
      );
      const { result } = renderHook(() => useChatTurn("session-1"));
      await act(async () => {
        await result.current.send("hello");
      });
      await waitFor(() => expect(result.current.state.status).toBe("done"));
      expect(result.current.state.answer).toBe("Canonical answer");
    });

    it("error event sets status to error with code and message", async () => {
      mockedStreamChat.mockReturnValueOnce(
        singleEvent({
          event: "error",
          code: "query_timeout",
          message: "Query took too long",
        }),
      );
      const { result } = renderHook(() => useChatTurn("session-1"));
      await act(async () => {
        await result.current.send("hello");
      });
      await waitFor(() => expect(result.current.state.status).toBe("error"));
      expect(result.current.state.error).toEqual({
        code: "query_timeout",
        message: "Query took too long",
      });
    });

    it("clarification_needed sets status to awaiting_clarification", async () => {
      mockedStreamChat.mockReturnValueOnce(clarificationStream());
      const { result } = renderHook(() => useChatTurn("session-1"));
      await act(async () => {
        await result.current.send("initial question");
      });
      expect(result.current.state.status).toBe("awaiting_clarification");
      expect(result.current.state.clarification).toEqual({
        question: "Which season?",
        options: ["2024-25"],
      });
    });
  });

  describe("error / cancellation", () => {
    it("network error sets status to error with code network", async () => {
      mockedStreamChat.mockRejectedValueOnce(new Error("network failure"));
      const { result } = renderHook(() => useChatTurn("session-1"));
      await act(async () => {
        await result.current.send("hello");
      });
      await waitFor(() => {
        expect(result.current.state.status).toBe("error");
        expect(result.current.state.error?.code).toBe("network");
      });
    });

    it("AbortError sets status to cancelled", async () => {
      // Return an async generator that throws — mockRejectedValueOnce
      // would not produce a proper async iterable for the for-await loop.
      mockedStreamChat.mockReturnValueOnce(
        // eslint-disable-next-line require-yield
        (async function* () {
          throw new DOMException("The operation was aborted", "AbortError");
        })(),
      );
      const { result } = renderHook(() => useChatTurn("session-1"));
      await act(async () => {
        await result.current.send("hello");
      });
      expect(result.current.state.status).toBe("cancelled");
    });

    it("cancel while running dispatches cancelled", async () => {
      // Mock implementation that hangs until the signal is aborted:
      // this mirrors the real streamChat which aborts when the
      // AbortController fires.
      mockedStreamChat.mockImplementation(
        // eslint-disable-next-line require-yield
        async function* (opts: { sessionId: string; message: string; signal?: AbortSignal }) {
          await new Promise<void>((_, reject) => {
            if (opts.signal?.aborted) {
              reject(new DOMException("The operation was aborted", "AbortError"));
              return;
            }
            opts.signal?.addEventListener(
              "abort",
              () => {
                reject(new DOMException("The operation was aborted", "AbortError"));
              },
              { once: true },
            );
          });
        },
      );

      const { result } = renderHook(() => useChatTurn("session-1"));

      // Start send (hangs on the first await).
      act(() => {
        result.current.send("hello");
      });

      // Wait for running state.
      await waitFor(() => expect(result.current.state.status).toBe("running"));

      // Cancel triggers abort on the signal → AbortError → cancelled.
      act(() => {
        result.current.cancel();
      });

      await waitFor(() => expect(result.current.state.status).toBe("cancelled"));
    });
  });

  describe("session ID override", () => {
    it("uses override session ID when provided", async () => {
      mockedStreamChat.mockReturnValueOnce(answerStream());
      const { result } = renderHook(() => useChatTurn(null)); // hook has null
      await act(async () => {
        await result.current.send("hello", "override-session");
      });
      await waitFor(() => expect(result.current.state.status).toBe("done"));
      // Verify the mock was called with the override session ID, not null.
      expect(mockedStreamChat).toHaveBeenCalledWith(
        expect.objectContaining({ sessionId: "override-session" }),
      );
    });

    it("throws when sessionId is null and no override", async () => {
      const { result } = renderHook(() => useChatTurn(null));
      // send rejects synchronously before calling streamChat.
      await expect(result.current.send("hello")).rejects.toThrow("no session id");
      expect(mockedStreamChat).not.toHaveBeenCalled();
    });
  });

  // Keep existing clarification round-trip test
  describe("clarification", () => {
    it("persists clarification after stream completion and completes a follow-up", async () => {
      mockedStreamChat
        .mockReturnValueOnce(clarificationStream())
        .mockReturnValueOnce(answerStream());
      const { result } = renderHook(() => useChatTurn("session-1"));

      await act(async () => {
        await result.current.send("initial question");
      });

      expect(result.current.state.status).toBe("awaiting_clarification");
      expect(result.current.state.clarification).toEqual({
        question: "Which season?",
        options: ["2024-25"],
      });

      await act(async () => {
        await result.current.send("2024-25");
      });

      await waitFor(() => expect(result.current.state.status).toBe("done"));
      expect(result.current.state.answer).toBe("The answer.");
      expect(result.current.state.clarification).toBeNull();
    });
  });
});
