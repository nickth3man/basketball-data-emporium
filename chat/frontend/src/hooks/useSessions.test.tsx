/**
 * useSessions unit tests — health, list, create, delete.
 *
 * Mocks `@/api/client` so no real HTTP requests are made.  Tests verify
 * that the hook calls the correct API functions and derives the correct
 * `health` and `sessions` state.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useSessions } from "@/hooks/useSessions";
import {
  listSessions,
  getHealth,
  createSession,
  deleteSession,
} from "@/api/client";

vi.mock("@/api/client", () => ({
  listSessions: vi.fn(),
  getHealth: vi.fn(),
  createSession: vi.fn(),
  deleteSession: vi.fn(),
}));

const mockListSessions = vi.mocked(listSessions);
const mockGetHealth = vi.mocked(getHealth);
const mockCreateSession = vi.mocked(createSession);
const mockDeleteSession = vi.mocked(deleteSession);

describe("useSessions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("calls listSessions and getHealth on mount", async () => {
    mockListSessions.mockResolvedValue([]);
    mockGetHealth.mockResolvedValue({ db: "connected", status: "ok" });

    renderHook(() => useSessions());

    await waitFor(() => {
      expect(mockListSessions).toHaveBeenCalledTimes(1);
      expect(mockGetHealth).toHaveBeenCalledTimes(1);
    });
  });

  it("sets health to 'ok' when db is 'connected'", async () => {
    mockListSessions.mockResolvedValue([]);
    mockGetHealth.mockResolvedValue({ db: "connected", status: "ok" });

    const { result } = renderHook(() => useSessions());

    await waitFor(() => {
      expect(result.current.health).toBe("ok");
    });
  });

  it("sets health to 'degraded' when db is not 'connected'", async () => {
    mockListSessions.mockResolvedValue([]);
    mockGetHealth.mockResolvedValue({ db: "disconnected", status: "error" });

    const { result } = renderHook(() => useSessions());

    await waitFor(() => {
      expect(result.current.health).toBe("degraded");
    });
  });

  it("sets health to 'unknown' when getHealth rejects", async () => {
    mockListSessions.mockResolvedValue([]);
    mockGetHealth.mockRejectedValue(new Error("network error"));

    const { result } = renderHook(() => useSessions());

    await waitFor(() => {
      expect(result.current.health).toBe("unknown");
    });
  });

  it("create calls apiCreateSession then refreshes list", async () => {
    const newSession = {
      id: "s1",
      title: "test-title",
      created_at: "2026-01-01T00:00:00Z",
      message_count: 0,
      status: "active",
    };
    mockListSessions.mockResolvedValue([]);
    mockGetHealth.mockResolvedValue({ db: "connected", status: "ok" });
    mockCreateSession.mockResolvedValue(newSession);

    const { result } = renderHook(() => useSessions());

    // Wait for initial list fetch and health check.
    await waitFor(() => {
      expect(mockListSessions).toHaveBeenCalledTimes(1);
    });

    let created: { id: string; title: string; created_at: string } | undefined;
    await act(async () => {
      created = await result.current.create("test-title");
    });

    expect(mockCreateSession).toHaveBeenCalledWith("test-title");
    // refresh() was called after create, incrementing the call count.
    expect(mockListSessions).toHaveBeenCalledTimes(2);
    expect(created).toEqual(newSession);
  });

  it("clearHistory calls apiDeleteSession then refreshes list", async () => {
    mockListSessions.mockResolvedValue([]);
    mockGetHealth.mockResolvedValue({ db: "connected", status: "ok" });
    mockDeleteSession.mockResolvedValue(undefined);

    const { result } = renderHook(() => useSessions());

    await waitFor(() => {
      expect(mockListSessions).toHaveBeenCalledTimes(1);
    });

    await act(async () => {
      await result.current.clearHistory("s1");
    });

    expect(mockDeleteSession).toHaveBeenCalledWith("s1");
    expect(mockListSessions).toHaveBeenCalledTimes(2);
  });
});
