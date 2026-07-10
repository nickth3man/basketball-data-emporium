/**
 * ResultTable unit tests — formatting / empty state / accessibility.
 *
 * Renders the component directly with fixture `ChatTurnTable` props and
 * asserts on the DOM output.  No network — TanStack Table and d3-format
 * are pure-computation.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ResultTable } from "@/components/ResultTable";
import type { ChatTurnTable } from "@/hooks/useChatTurn";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const percentageTable: ChatTurnTable = {
  columns: [{ name: "fg_pct" }],
  rows: [{ fg_pct: 0.482 }],
  rowCount: 1,
  truncated: false,
};

const integerTable: ChatTurnTable = {
  columns: [{ name: "career_pts" }],
  rows: [{ career_pts: 38387 }],
  rowCount: 1,
  truncated: false,
};

const salaryTable: ChatTurnTable = {
  columns: [{ name: "salary" }],
  rows: [{ salary: 35000000 }],
  rowCount: 1,
  truncated: false,
};

const floatTable: ChatTurnTable = {
  columns: [{ name: "ppg" }],
  rows: [{ ppg: 30.1 }],
  rowCount: 1,
  truncated: false,
};

const nullValueTable: ChatTurnTable = {
  columns: [{ name: "name" }, { name: "pts" }],
  rows: [{ name: "LeBron", pts: null }, { name: null, pts: 25 }],
  rowCount: 2,
  truncated: false,
};

const emptyTable: ChatTurnTable = {
  columns: [],
  rows: [],
  rowCount: 0,
  truncated: false,
};

const truncationTable: ChatTurnTable = {
  columns: [{ name: "x" }],
  rows: Array.from({ length: 50 }, (_, i) => ({ x: i })),
  rowCount: 100,
  truncated: true,
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ResultTable formatting", () => {
  it("renders percentage (fg_pct) with d3-format .1%", () => {
    render(<ResultTable table={percentageTable} />);
    // d3Format(".1%")(0.482) => "48.2%"
    expect(screen.getByText("48.2%")).toBeInTheDocument();
  });

  it("renders integer (career_pts) with grouped thousands", () => {
    render(<ResultTable table={integerTable} />);
    // d3Format(",.0f")(38387) => "38,387"
    expect(screen.getByText("38,387")).toBeInTheDocument();
  });

  it("renders salary as $currency", () => {
    render(<ResultTable table={salaryTable} />);
    // $ + d3Format(",.0f")(35000000) => "$35,000,000"
    expect(screen.getByText("$35,000,000")).toBeInTheDocument();
  });

  it("renders float (ppg) with toLocaleString trimming zeros", () => {
    render(<ResultTable table={floatTable} />);
    // 30.1.toLocaleString(undefined, {maximumFractionDigits: 3}) => "30.1"
    expect(screen.getByText("30.1")).toBeInTheDocument();
  });

  it("renders empty string for null / undefined cell values", () => {
    render(<ResultTable table={nullValueTable} />);
    // The row with { name: null, pts: 25 } — the name cell should be empty.
    // We assert that "LeBron" exists and that the cell for null name is
    // blank (no "null" text anywhere).
    expect(screen.getByText("LeBron")).toBeInTheDocument();
    expect(screen.queryByText("null")).not.toBeInTheDocument();
  });

  it("renders row count header with N of M rows and truncation label", () => {
    render(<ResultTable table={truncationTable} />);
    // 50 visible of 100 total, backend-truncated.
    expect(screen.getByText(/50 of 100 rows/)).toBeInTheDocument();
    expect(screen.getByText(/backend-truncated/)).toBeInTheDocument();
  });

  it("renders aria-label on section with column count", () => {
    render(<ResultTable table={percentageTable} />);
    // percentageTable has 1 column.
    const section = screen.getByRole("region");
    expect(section).toHaveAttribute("aria-label", "Result table with 1 column");
  });
});

describe("ResultTable empty state", () => {
  it("renders 'No rows.' fallback when columns are empty", () => {
    render(<ResultTable table={emptyTable} />);
    expect(screen.getByText("No rows.")).toBeInTheDocument();
  });
});
