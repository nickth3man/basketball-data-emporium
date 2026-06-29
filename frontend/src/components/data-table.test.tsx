import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DataTable } from "@/components/data-table";
import type { ColumnMeta } from "@/features/player-hub/types";

const columns: ColumnMeta[] = [
  { key: "season", label: "Season", default_visible: true, numeric: false },
  { key: "points_per_game", label: "Points", default_visible: true, numeric: true },
  { key: "team_name_abbr", label: "Team", default_visible: true, numeric: false },
];

const rows = [
  { season: "2023-24", points_per_game: 25.7, team_name_abbr: "LAL" },
  { season: "2022-23", points_per_game: 28.9, team_name_abbr: "LAL" },
];

describe("DataTable", () => {
  it("renders rows and filters them", () => {
    render(<DataTable rows={rows} columns={columns} defaultVisibleColumns={["season", "points_per_game", "team_name_abbr"]} />);

    expect(screen.getByText("2023-24")).toBeInTheDocument();
    expect(screen.getByText("2022-23")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("Filter rows"), { target: { value: "2022" } });

    expect(screen.queryByText("2023-24")).not.toBeInTheDocument();
    expect(screen.getByText("2022-23")).toBeInTheDocument();
  });

  it("allows column visibility changes", () => {
    render(<DataTable rows={rows} columns={columns} defaultVisibleColumns={["season", "points_per_game", "team_name_abbr"]} />);

    fireEvent.click(screen.getByRole("button", { name: /columns/i }));
    fireEvent.click(screen.getByLabelText("Team"));

    expect(screen.queryByText("LAL")).not.toBeInTheDocument();
  });
});
