/**
 * Component tests for `ui/src/features/player-hub/components/dataset-panel.tsx`.
 *
 * The panel wires two react-query hooks (`usePlayerDataset` for
 * `scope: "player"` datasets and `useSeasonDataset` for
 * `scope: "season"` datasets) through `QueryBoundary` into a
 * `DataTable`. We mock the hooks directly so the rendered output is
 * deterministic and we can verify the CSV export URL the panel hands
 * to the data table.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DatasetPanel } from "@/features/player-hub/components/dataset-panel";
import type { ColumnMeta, DatasetCatalogEntry, EndpointRowsResponse } from "@/features/player-hub/types";

/** Hoisted shared mock for the query hooks — the factory below re-uses these. */
const { usePlayerDataset, useSeasonDataset, csvExportUrl, downloadCsv } = vi.hoisted(() => ({
  usePlayerDataset: vi.fn(),
  useSeasonDataset: vi.fn(),
  csvExportUrl: vi.fn(() => "http://test/api/players/jamesle01/export?dataset=career"),
  downloadCsv: vi.fn(),
}));

vi.mock("@/features/player-hub/api/queries", () => ({
  usePlayerDataset,
  useSeasonDataset,
}));

vi.mock("@/features/player-hub/api/client", () => ({
  csvExportUrl,
}));

vi.mock("@/lib/api-client", () => ({
  downloadCsv,
}));

const columns: ColumnMeta[] = [
  { key: "season", label: "Season", default_visible: true, numeric: false },
  { key: "points_per_game", label: "Points", default_visible: true, numeric: true },
];

const successResult: EndpointRowsResponse = {
  dataset: "career",
  endpoint_name: "career",
  params: {},
  row_count: 2,
  columns,
  default_visible_columns: ["season", "points_per_game"],
  rows: [
    { season: "2023-24", points_per_game: 25.7 },
    { season: "2022-23", points_per_game: 28.9 },
  ],
};

const playerScopeDataset: DatasetCatalogEntry = {
  id: "career",
  label: "Career Totals",
  endpoint_name: "career",
  scope: "player",
  description: "Career per-game stats",
  columns,
  default_visible_columns: ["season", "points_per_game"],
  supports_export: true,
  supports_include_inactive_games: false,
};

const seasonScopeDataset: DatasetCatalogEntry = {
  id: "adjusted-shooting",
  label: "Adjusted Shooting",
  endpoint_name: "adjusted-shooting",
  scope: "season",
  description: "Adjusted shooting splits by season",
  columns,
  default_visible_columns: ["season", "points_per_game"],
  supports_export: true,
  supports_include_inactive_games: false,
};

function makeWrapper(): ({ children }: { children: ReactNode }) => ReactNode {
  const client = new QueryClient({
    defaultOptions: { queries: { staleTime: 0, retry: false, gcTime: 0 } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe("DatasetPanel", () => {
  beforeEach(() => {
    usePlayerDataset.mockReset();
    useSeasonDataset.mockReset();
    downloadCsv.mockReset();
    csvExportUrl.mockClear();
    csvExportUrl.mockReturnValue("http://test/api/players/jamesle01/export?dataset=career");
  });

  it("renders the table when the player-scope query succeeds", () => {
    usePlayerDataset.mockReturnValue({
      data: successResult,
      isLoading: false,
      isError: false,
      isSuccess: true,
    } as unknown as ReturnType<typeof usePlayerDataset>);

    render(
      <DatasetPanel
        identifier="jamesle01"
        dataset={playerScopeDataset}
        seasonEndYear={null}
      />,
      { wrapper: makeWrapper() },
    );

    expect(screen.getByText("2023-24")).toBeInTheDocument();
    expect(screen.getByText("2022-23")).toBeInTheDocument();
    // The trailing row count chip is rendered. The same number also
    // appears in the data table's footer, so we use getAllByText to
    // assert "at least one" rather than "exactly one".
    expect(screen.getAllByText(/2 rows/).length).toBeGreaterThan(0);
  });

  it("downloads CSV with the right URL for player-scope", () => {
    usePlayerDataset.mockReturnValue({
      data: successResult,
      isLoading: false,
      isError: false,
      isSuccess: true,
    } as unknown as ReturnType<typeof usePlayerDataset>);

    render(
      <DatasetPanel
        identifier="jamesle01"
        dataset={playerScopeDataset}
        seasonEndYear={null}
      />,
      { wrapper: makeWrapper() },
    );

    fireEvent.click(screen.getByRole("button", { name: /csv/i }));
    expect(csvExportUrl).toHaveBeenCalledWith("jamesle01", "career", undefined, false);
    expect(downloadCsv).toHaveBeenCalledWith(
      "http://test/api/players/jamesle01/export?dataset=career",
      "jamesle01-career.csv",
    );
  });

  it("uses the season-scope query when the dataset scope is 'season'", () => {
    useSeasonDataset.mockReturnValue({
      data: successResult,
      isLoading: false,
      isError: false,
      isSuccess: true,
    } as unknown as ReturnType<typeof useSeasonDataset>);

    render(
      <DatasetPanel
        identifier="jamesle01"
        dataset={seasonScopeDataset}
        seasonEndYear={2024}
      />,
      { wrapper: makeWrapper() },
    );

    expect(screen.getByText("2023-24")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /csv/i }));
    expect(csvExportUrl).toHaveBeenCalledWith("jamesle01", "adjusted-shooting", 2024, false);
  });
});
