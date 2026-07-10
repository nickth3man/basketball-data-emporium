/**
 * ChatView integration test — fresh-session flow.
 *
 * Mocks:
 *   - `@/api/sse` (streamChat) — no real network.
 *   - `@/api/client` (getSessionHistory, deleteSession) — no real API calls.
 *   - `@/hooks/useSessions` — controlled session lifecycle.
 *
 * Tests that the first message in a new session creates a session and
 * sends the message successfully (Bug 1 regression guard).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { streamChat } from "@/api/sse";
import { useSessions } from "@/hooks/useSessions";
import { ChatView } from "@/views/ChatView";

// ChatTimeline uses Element.scrollIntoView which jsdom does not implement.
Element.prototype.scrollIntoView = vi.fn();

// --- Mocks -----------------------------------------------------------------

vi.mock("@/api/sse", () => ({ streamChat: vi.fn() }));

// Provide every export that ChatView (or its dependencies) imports at
// runtime.  The mock values are only used when a function is actually
// called — types are compile-time only.
vi.mock("@/api/client", () => ({
  getSessionHistory: vi.fn().mockRejectedValue(new Error("not used in this test")),
  deleteSession: vi.fn().mockResolvedValue(undefined),
  createSession: vi.fn(),
  getHealth: vi.fn(),
  listSessions: vi.fn(),
}));

const mockCreate = vi.fn();
vi.mock("@/hooks/useSessions", () => ({
  useSessions: vi.fn(),
}));

// --- Helpers ----------------------------------------------------------------

async function* answerStream() {
  yield { event: "answer_finished" as const, answer: "Test answer." };
}

// --- Tests ------------------------------------------------------------------

describe("ChatView fresh session", () => {
  beforeEach(() => {
    vi.clearAllMocks();

    // Default mock for useSessions: empty session list, a create factory.
    vi.mocked(useSessions).mockReturnValue({
      sessions: [],
      create: mockCreate,
      health: "unknown" as const,
      loading: false,
      refresh: vi.fn(),
      clearHistory: vi.fn(),
    });

    mockCreate.mockImplementation(async (title?: string | null) => ({
      id: "test-session-id",
      title: title ?? "New chat",
      created_at: "2026-01-01T00:00:00Z",
      message_count: 0,
      status: "active",
    }));
  });

  it("creates a session on first message and sends with session ID override", async () => {
    const user = userEvent.setup();
    vi.mocked(streamChat).mockReturnValue(answerStream());

    render(<ChatView />);

    // Type a question.
    const textarea = screen.getByRole("textbox", { name: "Message" });
    await user.type(textarea, "Who is the GOAT?");

    // Submit.
    const sendButton = screen.getByRole("button", { name: "Send message" });
    await user.click(sendButton);

    // Session was created.
    await waitFor(() => {
      expect(mockCreate).toHaveBeenCalledWith(null);
    });

    // The user message appears in the timeline.
    await waitFor(() => {
      expect(screen.getByText("Who is the GOAT?")).toBeInTheDocument();
    });

    // The assistant answer appears.
    await waitFor(() => {
      expect(screen.getByText("Test answer.")).toBeInTheDocument();
    });

    // The session ID from create was passed as override to streamChat.
    expect(streamChat).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: "test-session-id" }),
    );
  });

  it("does not error when sending with an existing session ID", async () => {
    const user = userEvent.setup();
    vi.mocked(streamChat).mockReturnValue(answerStream());

    // Pre-populate a session.
    vi.mocked(useSessions).mockReturnValue({
      sessions: [
        {
          id: "existing-session",
          title: "Existing",
          created_at: "2026-01-01T00:00:00Z",
          message_count: 0,
          status: "active",
        },
      ],
      create: mockCreate,
      health: "unknown" as const,
      loading: false,
      refresh: vi.fn(),
      clearHistory: vi.fn(),
    });

    render(<ChatView />);

    const textarea = screen.getByRole("textbox", { name: "Message" });
    await user.type(textarea, "Best scorer?");
    const sendButton = screen.getByRole("button", { name: "Send message" });
    await user.click(sendButton);

    // create should NOT be called — session already exists.
    expect(mockCreate).not.toHaveBeenCalled();

    // Answer arrives.
    await waitFor(() => {
      expect(screen.getByText("Test answer.")).toBeInTheDocument();
    });
  });
});
