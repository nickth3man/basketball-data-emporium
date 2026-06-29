/**
 * Component tests for `ui/src/features/team-hub/components/team-search.tsx`.
 *
 * Mirrors the structure of `player-search.test.tsx`. The team-search input
 * is debounced (250ms) and persists the term to `?term=` in the URL — both
 * behaviors are tested below by mocking the same external surfaces the
 * player-search test mocks (`useTeamSearch`, `next/navigation`,
 * `useUrlParam`).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** Hoisted shared mocks — every `vi.mock` factory points at these refs. */
const { useTeamSearch, useRouter, useUrlParam } = vi.hoisted(() => ({
  useTeamSearch: vi.fn(),
  useRouter: vi.fn(),
  useUrlParam: vi.fn(),
}));

vi.mock("@/features/team-hub/api/queries", () => ({
  useTeamSearch,
}));

vi.mock("next/navigation", () => ({
  useRouter,
}));

vi.mock("@/lib/use-url-param", () => ({
  useUrlParam,
}));

import { TeamSearch } from "@/features/team-hub/components/team-search";

/** Build a fresh `QueryClient` plus a React wrapper for the component. */
function makeWrapper(): ({ children }: { children: ReactNode }) => ReactNode {
  const client = new QueryClient({
    defaultOptions: { queries: { staleTime: 0, retry: false, gcTime: 0 } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

/** A successful but empty search result (no matches). */
function emptyResult() {
  return {
    data: [],
    isFetching: false,
    isLoading: false,
    isError: false,
    isSuccess: true,
  };
}

/** A search result that contains one matching team. */
function singleResult() {
  return {
    data: [{ name: "Los Angeles Lakers", identifier: "LAL", leagues: ["NBA"] }],
    isFetching: false,
    isLoading: false,
    isError: false,
    isSuccess: true,
  };
}

describe("TeamSearch", () => {
  let routerPush: ReturnType<typeof vi.fn>;
  let urlSet: ReturnType<typeof vi.fn>;
  let urlGet: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    useTeamSearch.mockReset();
    useUrlParam.mockReset();
    useRouter.mockReset();

    routerPush = vi.fn();
    urlSet = vi.fn();
    urlGet = vi.fn(() => null);

    useRouter.mockReturnValue({ push: routerPush });
    useUrlParam.mockReturnValue({ get: urlGet, set: urlSet });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not render a results panel when the typed term is shorter than 2 chars", () => {
    vi.useFakeTimers();
    useTeamSearch.mockReturnValue(emptyResult());

    render(<TeamSearch />, { wrapper: makeWrapper() });

    const input = screen.getByPlaceholderText("Search teams");
    fireEvent.change(input, { target: { value: "L" } });

    // The component's `trimmedTerm.length >= 2` gate hides the entire
    // results panel for 1-char terms — so neither the "Searching"
    // affordance nor the "No teams found" message should appear,
    // even after the 250ms debounce commits.
    act(() => {
      vi.advanceTimersByTime(500);
    });

    expect(screen.queryByText("Searching")).not.toBeInTheDocument();
    expect(screen.queryByText("No teams found for")).not.toBeInTheDocument();
  });

  it("fetches after a 2+ char term once the 250ms debounce elapses", () => {
    vi.useFakeTimers();
    useTeamSearch.mockReturnValue(emptyResult());

    render(<TeamSearch />, { wrapper: makeWrapper() });

    const input = screen.getByPlaceholderText("Search teams");
    fireEvent.change(input, { target: { value: "LA" } });

    // Before 250ms: debounce still pending.
    act(() => {
      vi.advanceTimersByTime(100);
    });

    // After 250ms: the debounce commits; the input has a 2+ char term, so
    // the search results panel mounts (with the empty-result copy).
    act(() => {
      vi.advanceTimersByTime(250);
    });

    expect(screen.getByText(/No teams found for/)).toBeInTheDocument();
  });

  it("shows the 'Searching' indicator while isFetching is true", () => {
    vi.useFakeTimers();
    useTeamSearch.mockReturnValue({
      data: undefined,
      isFetching: true,
      isLoading: true,
      isError: false,
      isSuccess: false,
    });

    render(<TeamSearch />, { wrapper: makeWrapper() });

    const input = screen.getByPlaceholderText("Search teams");
    fireEvent.change(input, { target: { value: "LA" } });

    // Advance past the 250ms debounce — `isFetching` is true so the
    // "Searching" affordance is shown.
    act(() => {
      vi.advanceTimersByTime(300);
    });

    expect(screen.getByText("Searching")).toBeInTheDocument();
  });

  it("shows the 'No teams found' message when the search returns no rows", () => {
    vi.useFakeTimers();
    useTeamSearch.mockReturnValue(emptyResult());

    render(<TeamSearch />, { wrapper: makeWrapper() });

    fireEvent.change(screen.getByPlaceholderText("Search teams"), { target: { value: "zz" } });

    act(() => {
      vi.advanceTimersByTime(300);
    });

    expect(screen.getByText(/No teams found for/)).toBeInTheDocument();
  });

  it("navigates to the first result on submit (Enter)", () => {
    vi.useFakeTimers();
    useTeamSearch.mockReturnValue(singleResult());

    render(<TeamSearch />, { wrapper: makeWrapper() });

    fireEvent.change(screen.getByPlaceholderText("Search teams"), { target: { value: "LA" } });

    act(() => {
      vi.advanceTimersByTime(300);
    });

    // Submit the form (Enter) — should push to /teams/<first.identifier>
    // and write `?term=` to the URL.
    fireEvent.submit(screen.getByRole("button", { name: /open/i }).closest("form")!);

    expect(routerPush).toHaveBeenCalledWith("/teams/LAL");
    expect(urlSet).toHaveBeenCalledWith("term", "LA");
  });

  it("prepopulates the input from the ?term= URL param on mount", () => {
    useTeamSearch.mockReturnValue(emptyResult());
    urlGet.mockImplementation((key: string) => (key === "term" ? "LAL" : null));

    render(<TeamSearch />, { wrapper: makeWrapper() });

    const input = screen.getByPlaceholderText("Search teams") as HTMLInputElement;
    expect(input.value).toBe("LAL");
  });
});
