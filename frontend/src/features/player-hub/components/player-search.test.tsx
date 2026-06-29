/**
 * Component tests for `ui/src/features/player-hub/components/player-search.tsx`.
 *
 * The component is a debounced search input wired into react-query +
 * `next/navigation`. We mock every external surface — the search hook,
 * the router, the `useUrlParam` URL bridge — so the rendered output and
 * the navigation/URL side effects are fully under test control.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** Hoisted shared mocks — every `vi.mock` factory points at these refs. */
const { usePlayerSearch, useRouter, useUrlParam } = vi.hoisted(() => ({
  usePlayerSearch: vi.fn(),
  useRouter: vi.fn(),
  useUrlParam: vi.fn(),
}));

vi.mock("@/features/player-hub/api/queries", () => ({
  usePlayerSearch,
}));

vi.mock("next/navigation", () => ({
  useRouter,
}));

vi.mock("@/lib/use-url-param", () => ({
  useUrlParam,
}));

import { PlayerSearch } from "@/features/player-hub/components/player-search";

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

/** A search result that contains one matching player. */
function singleResult() {
  return {
    data: [{ name: "LeBron James", identifier: "jamesle01", leagues: ["NBA"] }],
    isFetching: false,
    isLoading: false,
    isError: false,
    isSuccess: true,
  };
}

describe("PlayerSearch", () => {
  let routerPush: ReturnType<typeof vi.fn>;
  let urlSet: ReturnType<typeof vi.fn>;
  let urlGet: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    usePlayerSearch.mockReset();
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
    usePlayerSearch.mockReturnValue(emptyResult());

    render(<PlayerSearch />, { wrapper: makeWrapper() });

    const input = screen.getByPlaceholderText("Search players");
    fireEvent.change(input, { target: { value: "L" } });

    // The component's `trimmedTerm.length >= 2` gate hides the entire
    // results panel for 1-char terms — so neither the "Searching"
    // affordance nor the "No players found" message should appear,
    // even after the 250ms debounce commits.
    act(() => {
      vi.advanceTimersByTime(500);
    });

    expect(screen.queryByText("Searching")).not.toBeInTheDocument();
    expect(screen.queryByText("No players found for")).not.toBeInTheDocument();
  });

  it("fetches after a 2+ char term once the 250ms debounce elapses", () => {
    vi.useFakeTimers();
    usePlayerSearch.mockReturnValue(emptyResult());

    render(<PlayerSearch />, { wrapper: makeWrapper() });

    const input = screen.getByPlaceholderText("Search players");
    fireEvent.change(input, { target: { value: "Le" } });

    // Before 250ms: debounce still pending.
    act(() => {
      vi.advanceTimersByTime(100);
    });

    // After 250ms: the debounce commits; the input has a 2+ char term, so
    // the search results panel mounts (with the empty-result copy).
    act(() => {
      vi.advanceTimersByTime(250);
    });

    expect(screen.getByText(/No players found for/)).toBeInTheDocument();
  });

  it("shows the 'Searching' indicator while isFetching is true", () => {
    vi.useFakeTimers();
    usePlayerSearch.mockReturnValue({
      data: undefined,
      isFetching: true,
      isLoading: true,
      isError: false,
      isSuccess: false,
    });

    render(<PlayerSearch />, { wrapper: makeWrapper() });

    const input = screen.getByPlaceholderText("Search players");
    fireEvent.change(input, { target: { value: "Le" } });

    // Advance past the 250ms debounce — `isFetching` is true so the
    // "Searching" affordance is shown.
    act(() => {
      vi.advanceTimersByTime(300);
    });

    expect(screen.getByText("Searching")).toBeInTheDocument();
  });

  it("shows the 'No players found' message when the search returns no rows", () => {
    vi.useFakeTimers();
    usePlayerSearch.mockReturnValue(emptyResult());

    render(<PlayerSearch />, { wrapper: makeWrapper() });

    fireEvent.change(screen.getByPlaceholderText("Search players"), { target: { value: "zz" } });

    act(() => {
      vi.advanceTimersByTime(300);
    });

    expect(screen.getByText(/No players found for/)).toBeInTheDocument();
  });

  it("navigates to the first result on submit (Enter)", () => {
    vi.useFakeTimers();
    usePlayerSearch.mockReturnValue(singleResult());

    render(<PlayerSearch />, { wrapper: makeWrapper() });

    fireEvent.change(screen.getByPlaceholderText("Search players"), { target: { value: "Le" } });

    act(() => {
      vi.advanceTimersByTime(300);
    });

    // Submit the form (Enter) — should push to /players/<first.identifier>
    // and write `?term=` to the URL.
    fireEvent.submit(screen.getByRole("button", { name: /open/i }).closest("form")!);

    expect(routerPush).toHaveBeenCalledWith("/players/jamesle01");
    expect(urlSet).toHaveBeenCalledWith("term", "Le");
  });

  it("prepopulates the input from the ?term= URL param on mount", () => {
    usePlayerSearch.mockReturnValue(emptyResult());
    urlGet.mockImplementation((key: string) => (key === "term" ? "james" : null));

    render(<PlayerSearch />, { wrapper: makeWrapper() });

    const input = screen.getByPlaceholderText("Search players") as HTMLInputElement;
    expect(input.value).toBe("james");
  });
});
