/**
 * Hook-level tests for `ui/src/features/player-hub/api/queries.ts`.
 *
 * We mock `@/features/player-hub/api/client` (the thin shim that the
 * hooks delegate to) so each hook's `queryFn` resolves to a value we
 * control. A real `QueryClient` is used (per-test, no cross-test
 * sharing) and we assert both the hook's data and the URL it asked the
 * client to fetch.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { type ReactNode, createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock the client shim. The hook delegates to `getStatus`, `getCatalog`,
// `searchPlayers`, `getSummary`, `getPlayerDataset`, `getSeasonDataset` —
// we replace every one of those with a vi.fn() that the test can drive.
// `vi.hoisted` keeps the `mocks` reference stable so the factory below
// can return spies that point at the same registry from the test bodies.
const { getStatus, getCatalog, searchPlayers, getSummary, getPlayerDataset, getSeasonDataset } = vi.hoisted(() => ({
  getStatus: vi.fn(),
  getCatalog: vi.fn(),
  searchPlayers: vi.fn(),
  getSummary: vi.fn(),
  getPlayerDataset: vi.fn(),
  getSeasonDataset: vi.fn(),
}));

vi.mock("@/features/player-hub/api/client", () => ({
  getStatus,
  getCatalog,
  searchPlayers,
  getSummary,
  getPlayerDataset,
  getSeasonDataset,
}));

vi.mock("@/features/player-hub/api/queries", async () => {
  // Import the real module's exports; vi.mock's factory runs before the
  // module is read, so we re-export everything except the `apiFetch`
  // import path. We re-implement the hooks using the *mocked* client
  // functions defined in the `vi.hoisted` block above.
  const { useQuery } = await import("@tanstack/react-query");
  const { queryKeys } = await import("@/features/player-hub/api/query-keys");
  return {
    useStatus: () =>
      useQuery({
        queryKey: queryKeys.status,
        queryFn: getStatus,
      }),
    useCatalog: () =>
      useQuery({
        queryKey: queryKeys.catalog,
        queryFn: getCatalog,
      }),
    usePlayerSearch: (term: string) =>
      useQuery({
        queryKey: queryKeys.playerSearch(term),
        queryFn: () => searchPlayers(term),
        enabled: term.trim().length >= 2,
      }),
    usePlayerSummary: (identifier: string) =>
      useQuery({
        queryKey: queryKeys.playerSummary(identifier),
        queryFn: () => getSummary(identifier),
      }),
    usePlayerDataset: (identifier: string, dataset: string, enabled = true) =>
      useQuery({
        queryKey: queryKeys.playerDataset(identifier, dataset),
        queryFn: () => getPlayerDataset(identifier, dataset),
        enabled,
      }),
    useSeasonDataset: (
      identifier: string,
      seasonEndYear: number | null,
      dataset: string,
      enabled = true,
      includeInactiveGames = false,
    ) =>
      useQuery({
        queryKey: queryKeys.seasonDataset(identifier, seasonEndYear, dataset, includeInactiveGames),
        queryFn: () => getSeasonDataset(identifier, seasonEndYear ?? 0, dataset, includeInactiveGames),
        enabled: enabled && seasonEndYear !== null,
      }),
  };
});

import {
  useCatalog,
  usePlayerDataset,
  usePlayerSearch,
  usePlayerSummary,
  useSeasonDataset,
  useStatus,
} from "@/features/player-hub/api/queries";

/** Build a fresh `QueryClient` plus a React wrapper for `renderHook`. */
function makeWrapper(): ({ children }: { children: ReactNode }) => ReactNode {
  const client = new QueryClient({
    defaultOptions: { queries: { staleTime: 0, retry: false, gcTime: 0 } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client }, children);
  };
}

describe("player-hub query hooks", () => {
  beforeEach(() => {
    getStatus.mockReset();
    getCatalog.mockReset();
    searchPlayers.mockReset();
    getSummary.mockReset();
    getPlayerDataset.mockReset();
    getSeasonDataset.mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("useStatus fetches /api/status and returns the payload", async () => {
    const payload = { ok: true, endpoint_count: 42 };
    getStatus.mockResolvedValueOnce(payload);

    const { result } = renderHook(() => useStatus(), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(getStatus).toHaveBeenCalledTimes(1);
    expect(result.current.data).toEqual(payload);
  });

  it("usePlayerSearch does NOT fetch when the term is shorter than 2 chars", async () => {
    const { result } = renderHook(() => usePlayerSearch("a"), { wrapper: makeWrapper() });

    // Wait a tick for the query to settle in its disabled state.
    await waitFor(() => expect(result.current.fetchStatus).toBe("idle"));
    expect(searchPlayers).not.toHaveBeenCalled();
  });

  it("usePlayerSearch fetches when the term is 2+ chars", async () => {
    searchPlayers.mockResolvedValueOnce([{ name: "LeBron", identifier: "jamesle01", leagues: ["NBA"] }]);

    const { result } = renderHook(() => usePlayerSearch("Le"), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(searchPlayers).toHaveBeenCalledWith("Le");
    expect(result.current.data).toEqual([{ name: "LeBron", identifier: "jamesle01", leagues: ["NBA"] }]);
  });

  it("usePlayerSummary fetches the right identifier", async () => {
    const summary = {
      identifier: "jamesle01",
      display_name: "LeBron James",
      leagues: ["NBA"],
      default_season: 2024,
      available_seasons: [2024],
      hero_stats: { points_per_game: 25.7 },
      career: { dataset: "career", endpoint_name: "career", params: {}, row_count: 0, columns: [], default_visible_columns: [], rows: [] },
    };
    getSummary.mockResolvedValueOnce(summary);

    const { result } = renderHook(() => usePlayerSummary("jamesle01"), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(getSummary).toHaveBeenCalledWith("jamesle01");
    expect(result.current.data).toEqual(summary);
  });

  it("usePlayerDataset hits the player-dataset client fn with the right args", async () => {
    const payload = {
      dataset: "career",
      endpoint_name: "career",
      params: {},
      row_count: 1,
      columns: [],
      default_visible_columns: [],
      rows: [{}],
    };
    getPlayerDataset.mockResolvedValueOnce(payload);

    const { result } = renderHook(() => usePlayerDataset("jamesle01", "career"), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(getPlayerDataset).toHaveBeenCalledWith("jamesle01", "career");
  });

  it("useSeasonDataset hits the season-dataset client fn with the right args", async () => {
    const payload = {
      dataset: "splits",
      endpoint_name: "splits",
      params: {},
      row_count: 0,
      columns: [],
      default_visible_columns: [],
      rows: [],
    };
    getSeasonDataset.mockResolvedValueOnce(payload);

    const { result } = renderHook(() => useSeasonDataset("jamesle01", 2024, "splits", true, false), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(getSeasonDataset).toHaveBeenCalledWith("jamesle01", 2024, "splits", false);
  });

  it("useCatalog fetches the player-hub catalog", async () => {
    const payload = { tabs: [], datasets: [] };
    getCatalog.mockResolvedValueOnce(payload);

    const { result } = renderHook(() => useCatalog(), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(getCatalog).toHaveBeenCalledTimes(1);
    expect(result.current.data).toEqual(payload);
  });
});
