/**
 * Component tests for `ui/src/features/player-hub/components/overview.tsx`.
 *
 * `Overview` renders three hero stat cards, a career-arc line chart, and
 * a career `DataTable` from a `PlayerHubSummary`. We feed a hand-built
 * summary (with a mix of numeric, string, and null stat values) and
 * assert that the hero stat cards surface the expected `formatStat`
 * output — including the `—` fallback for null values.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Overview } from "@/features/player-hub/components/overview";
import type { ColumnMeta, PlayerHubSummary } from "@/features/player-hub/types";

const columns: ColumnMeta[] = [
  { key: "season", label: "Season", default_visible: true, numeric: false },
  { key: "points_per_game", label: "Points", default_visible: true, numeric: true },
  { key: "team_name_abbr", label: "Team", default_visible: true, numeric: false },
];

function makeSummary(overrides: Partial<PlayerHubSummary> = {}): PlayerHubSummary {
  return {
    identifier: "jamesle01",
    display_name: "LeBron James",
    leagues: ["NBA"],
    default_season: 2024,
    available_seasons: [2024, 2023],
    hero_stats: {
      points_per_game: 25.7,
      total_rebounds_per_game: 7.3,
      assists_per_game: 10.9,
    },
    career: {
      dataset: "career",
      endpoint_name: "career",
      params: {},
      row_count: 2,
      columns,
      default_visible_columns: ["season", "points_per_game", "team_name_abbr"],
      rows: [
        { season: "2023-24", points_per_game: 25.7, team_name_abbr: "LAL" },
        { season: "2022-23", points_per_game: 28.9, team_name_abbr: "LAL" },
      ],
    },
    ...overrides,
  };
}

describe("Overview", () => {
  it("renders the three hero stat cards with the expected formatted values", () => {
    render(<Overview summary={makeSummary()} />);

    // The career table also renders the same numeric values, so the hero
    // stat cards and the table cells both contain the strings. We assert
    // they appear at least once rather than exactly once — the contract
    // under test is "the hero card formatted the stat", not uniqueness.
    // formatStat formats numbers with up-to-3 fraction digits; 25.7 → "25.7"
    expect(screen.getAllByText("25.7").length).toBeGreaterThan(0);
    expect(screen.getAllByText("7.3").length).toBeGreaterThan(0);
    expect(screen.getAllByText("10.9").length).toBeGreaterThan(0);
  });

  it("renders the career table rows", () => {
    render(<Overview summary={makeSummary()} />);

    expect(screen.getByText("2023-24")).toBeInTheDocument();
    expect(screen.getByText("2022-23")).toBeInTheDocument();
  });

  it("renders the em-dash for null hero stats", () => {
    const summary = makeSummary({
      hero_stats: {
        points_per_game: null,
        total_rebounds_per_game: null,
        assists_per_game: null,
      },
    });

    render(<Overview summary={summary} />);

    // All three stat values collapse to "—" — three instances, one per card.
    const dashes = screen.getAllByText("—");
    expect(dashes.length).toBe(3);
  });

  it("renders the section heading copy and row count", () => {
    render(<Overview summary={makeSummary()} />);

    expect(screen.getByText(/Career Arc/i)).toBeInTheDocument();
    expect(screen.getByText(/Career Table/i)).toBeInTheDocument();
    expect(screen.getByText(/2 rows from player career stats/i)).toBeInTheDocument();
  });
});
