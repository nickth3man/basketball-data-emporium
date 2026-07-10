import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { streamChat } from "@/api/sse";
import { useChatTurn } from "@/hooks/useChatTurn";

vi.mock("@/api/sse", () => ({ streamChat: vi.fn() }));

const mockedStreamChat = vi.mocked(streamChat);

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

describe("useChatTurn clarification", () => {
  it("persists clarification after stream completion and completes a follow-up", async () => {
    mockedStreamChat.mockReturnValueOnce(clarificationStream()).mockReturnValueOnce(answerStream());
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
