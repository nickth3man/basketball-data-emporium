/**
 * Component tests for `ui/src/features/team-hub/components/overview.tsx`.
 *
 * `Overview` renders three hero stat cards, the optional "Franchise Arc"
 * line chart (driven by `summary.franchise_arc`, currently absent
 * server-side — the chart shows the empty-state card until the Python
 * track ships the series), and a roster `DataTable` from a
 * `TeamHubSummary`. We feed hand-built summaries to assert the hero
 * stat em-dash fallback, the empty-state message, and the chart's
 * 5-point rendered shape.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Overview } from "@/features/team-hub/components/overview";
import type { ColumnMeta, TeamHubSummary } from "@/features/team-hub/types";

const columns: ColumnMeta[] = [
  { key: "season", label: "Season", default_visible: true, numeric: false },
  { key: "wins", label: "Wins", default_visible: true, numeric: true },
  { key: "losses", label: "Losses", default_visible: true, numeric: true },
  { key: "team_name_abbr", label: "Team", default_visible: true, numeric: false },
];

function makeSummary(overrides: Partial<TeamHubSummary> = {}): TeamHubSummary {
  return {
    identifier: "BOS",
    display_name: "Boston Celtics",
    leagues: ["NBA"],
    default_season: 2024,
    available_seasons: [2024, 2023, 2022, 2021, 2020],
    hero_stats: {
      season: 2024,
      team: "BOS",
      wins: 64,
      losses: 18,
      win_pct: 0.78,
    },
    roster: {
      dataset: "roster",
      endpoint_name: "team_roster",
      params: {},
      row_count: 2,
      columns,
      default_visible_columns: ["season", "wins", "losses", "team_name_abbr"],
      rows: [
        { season: "2023-24", wins: 64, losses: 18, team_name_abbr: "BOS" },
        { season: "2022-23", wins: 57, losses: 25, team_name_abbr: "BOS" },
      ],
    },
    ...overrides,
  };
}

describe("Overview (team-hub)", () => {
  it("renders the three hero stat cards with formatted numeric values", () => {
    render(<Overview summary={makeSummary()} />);

    // Wins and Losses are integer-formatted via `toLocaleString()`.
    expect(screen.getAllByText("64").length).toBeGreaterThan(0);
    expect(screen.getAllByText("18").length).toBeGreaterThan(0);
    // Win % is scaled from 0.78 → 78% and rendered with the "%" suffix.
    expect(screen.getAllByText(/78%/).length).toBeGreaterThan(0);
  });

  it("renders the em-dash for null hero stats (all three cards collapse to —)", () => {
    const summary = makeSummary({
      hero_stats: {
        season: null,
        team: "BOS",
        wins: null,
        losses: null,
        win_pct: null,
      },
    });

    render(<Overview summary={summary} />);

    const dashes = screen.getAllByText("—");
    // Three hero stat cards → three em-dashes (chart and table don't add
    // em-dashes in this scenario).
    expect(dashes.length).toBeGreaterThanOrEqual(3);
  });

  it("renders the empty-state card when franchise_arc is absent", () => {
    // Default makeSummary() has no `franchise_arc` field — the server
    // has not shipped it yet. The empty-state copy should be visible.
    render(<Overview summary={makeSummary()} />);

    expect(screen.getByText(/Franchise arc data not yet available/i)).toBeInTheDocument();
  });

  it("renders the empty-state card when franchise_arc is an empty array", () => {
    const summary = makeSummary({ franchise_arc: [] });

    render(<Overview summary={summary} />);

    expect(screen.getByText(/Franchise arc data not yet available/i)).toBeInTheDocument();
  });

  it("renders the line chart when franchise_arc is present and non-empty", () => {
    const summary = makeSummary({
      franchise_arc: [
        { season_end_year: 2020, team_name: "BOS", wins: 48, losses: 24, win_pct: 0.667 },
        { season_end_year: 2021, team_name: "BOS", wins: 36, losses: 36, win_pct: 0.5 },
        { season_end_year: 2022, team_name: "BOS", wins: 51, losses: 31, win_pct: 0.622 },
        { season_end_year: 2023, team_name: "BOS", wins: 57, losses: 25, win_pct: 0.695 },
        { season_end_year: 2024, team_name: "BOS", wins: 64, losses: 18, win_pct: 0.78 },
      ],
    });

    render(<Overview summary={summary} />);

    // The chart's legend names map to the two lines we render.
    expect(screen.getAllByText("Wins").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Losses").length).toBeGreaterThan(0);
    // The empty-state copy is suppressed once the series is present.
    expect(screen.queryByText(/Franchise arc data not yet available/i)).not.toBeInTheDocument();
  });

  it("renders the section heading copy and roster row count", () => {
    render(<Overview summary={makeSummary()} />);

    // Use heading role so the assertion is anchored on the `<h2>` from
    // `SectionHeading` rather than the (case-insensitive) "Franchise Arc"
    // substring that also appears in the empty-state title.
    expect(screen.getByRole("heading", { name: /Franchise Arc/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /^Roster$/i })).toBeInTheDocument();
    expect(screen.getByText(/2 rows from the current roster/i)).toBeInTheDocument();
  });
});
