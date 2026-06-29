/**
 * Component tests for `ui/src/features/team-hub/components/team-hub.tsx`.
 *
 * Pins the team-hub landing subtitle contract: when the summary's
 * ``leagues`` array is empty, the rendered subtitle must NOT contain
 * a dangling ``·`` separator. The previous render expression
 * (``{summary.identifier} · {summary.leagues.join("/")} · …``)
 * produced a literal ``BOS ·  · —`` for any team, regardless of
 * whether leagues or hero-stats data were populated.
 *
 * The fix in the component uses
 * ``[summary.identifier, summary.leagues.join("/") || null]
 *   .filter(Boolean).join(" · ")``
 * so empty slots are dropped before the join. This test asserts the
 * user-visible result for the worst case (``leagues=[]`` and a
 * ``TeamHeroStats`` with no extras).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** Hoisted shared mocks — every `vi.mock` factory points at these refs. */
const { useTeamSummary, useTeamSearch, useCatalog, useUrlParam, useRouter } = vi.hoisted(() => ({
  useTeamSummary: vi.fn(),
  useTeamSearch: vi.fn(),
  useCatalog: vi.fn(),
  useUrlParam: vi.fn(),
  useRouter: vi.fn(),
}));

vi.mock("@/features/team-hub/api/queries", () => ({
  useTeamSummary,
  useTeamSearch,
  useCatalog,
}));

vi.mock("next/navigation", () => ({
  useRouter,
}));

vi.mock("@/lib/use-url-param", () => ({
  useUrlParam,
}));

import { TeamHub } from "@/features/team-hub/components/team-hub";
import type { TeamHubSummary } from "@/features/team-hub/types";

/** Build a fresh `QueryClient` plus a React wrapper for the component. */
function makeWrapper(): ({ children }: { children: ReactNode }) => ReactNode {
  const client = new QueryClient({
    defaultOptions: { queries: { staleTime: 0, retry: false, gcTime: 0 } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

/** Minimal empty-roster envelope — keeps the rendered DOM small. */
function emptyRoster() {
  return {
    dataset: "roster",
    endpoint_name: "team_roster",
    params: {},
    row_count: 0,
    columns: [],
    default_visible_columns: [],
    rows: [],
  };
}

/** Build a `TeamHubSummary` with overridable fields. */
function makeSummary(overrides: Partial<TeamHubSummary> = {}): TeamHubSummary {
  return {
    identifier: "BOS",
    display_name: "Boston Celtics",
    leagues: ["NBA"],
    default_season: 2024,
    available_seasons: [2024],
    hero_stats: {
      season: 2024,
      team: "BOS",
      wins: 64,
      losses: 18,
      win_pct: 0.78,
    },
    roster: emptyRoster(),
    ...overrides,
  };
}

describe("TeamHub", () => {
  let urlGet: ReturnType<typeof vi.fn>;
  let urlSet: ReturnType<typeof vi.fn>;
  let routerPush: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    useTeamSummary.mockReset();
    useTeamSearch.mockReset();
    useCatalog.mockReset();
    useUrlParam.mockReset();
    useRouter.mockReset();

    urlGet = vi.fn(() => null);
    urlSet = vi.fn();
    routerPush = vi.fn();

    useRouter.mockReturnValue({ push: routerPush });
    useUrlParam.mockReturnValue({ get: urlGet, set: urlSet });
    // `<TeamSearch compact />` (rendered inside `<TeamHub />`) calls
    // `useTeamSearch` on every render; return the no-results shape so
    // the search input renders without crashing the parent component.
    useTeamSearch.mockReturnValue({
      data: [],
      isFetching: false,
      isLoading: false,
      isError: false,
      isSuccess: true,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not render a dangling · separator in the subtitle when leagues is empty", () => {
    // The regression scenario: every team previously rendered
    // `BOS ·  · —` because the subtitle unconditionally joined three
    // slots with `· ` regardless of whether any slot was populated.
    useTeamSummary.mockReturnValue({
      data: makeSummary({ leagues: [] }),
      isFetching: false,
      isLoading: false,
      isError: false,
      isSuccess: true,
    });
    // The catalog state is incidental to this subtitle assertion. With
    // no catalog data, the component renders its explicit unavailable
    // state after the header instead of relying on hard-coded tabs.
    useCatalog.mockReturnValue({
      data: undefined,
      isFetching: false,
      isLoading: false,
      isError: false,
      isSuccess: false,
    });

    render(<TeamHub identifier="BOS" />, { wrapper: makeWrapper() });

    // The subtitle is the `<p>` directly under the `<h1>` in the
    // header — anchor on the heading to find the right paragraph
    // (other components on the page also use `text-court-muted`).
    const heading = screen.getByRole("heading", { name: /Boston Celtics/i });
    const subtitle = heading.parentElement?.querySelector("p");
    expect(subtitle).not.toBeNull();
    const subtitleText = subtitle?.textContent ?? "";
    // Defensive contract: no `·` may appear (the subtitle has only the
    // identifier; no second slot, no separator).
    expect(subtitleText).not.toContain("·");
    // The identifier should still be visible.
    expect(subtitleText).toContain("BOS");
  });

  it("renders the identifier and league in the subtitle when leagues is non-empty", () => {
    // The non-regression contract: when leagues is non-empty, both
    // the identifier and the league value are present, joined by
    // exactly one `·` separator.
    useTeamSummary.mockReturnValue({
      data: makeSummary({ leagues: ["NBA"] }),
      isFetching: false,
      isLoading: false,
      isError: false,
      isSuccess: true,
    });
    useCatalog.mockReturnValue({
      data: undefined,
      isFetching: false,
      isLoading: false,
      isError: false,
      isSuccess: false,
    });

    render(<TeamHub identifier="BOS" />, { wrapper: makeWrapper() });

    const heading = screen.getByRole("heading", { name: /Boston Celtics/i });
    const subtitle = within(heading.parentElement as HTMLElement).getByText(/BOS.*NBA/);
    // The subtitle contains exactly one `·` (between BOS and NBA) —
    // and nothing else (no third slot, no em-dash).
    expect(subtitle.textContent).toBe("BOS · NBA");
  });
});
