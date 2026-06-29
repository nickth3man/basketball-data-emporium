/**
 * Component tests for `ui/src/components/query-boundary.tsx`.
 *
 * `QueryBoundary` collapses a `UseQueryResult<T>` into a four-state
 * ladder (loading → error → empty → success). We pass a fake
 * `UseQueryResult` (only the shape we read) to drive each branch
 * without touching the network or react-query internals.
 */
import { render, screen } from "@testing-library/react";
import type { UseQueryResult } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { QueryBoundary } from "@/components/query-boundary";

interface FakeData {
  rows: string[];
}

/** Build a `UseQueryResult`-shaped object that satisfies the boundary. */
function fakeQuery(overrides: Partial<UseQueryResult<FakeData>>): UseQueryResult<FakeData> {
  return {
    data: undefined,
    error: null,
    isLoading: false,
    isError: false,
    isSuccess: false,
    isFetching: false,
    refetch: () => Promise.resolve(),
    status: "pending",
    fetchStatus: "idle",
    dataUpdatedAt: 0,
    errorUpdatedAt: 0,
    failureCount: 0,
    failureReason: null,
    errorUpdateCount: 0,
    isLoadingError: false,
    isPaused: false,
    isPending: true,
    isRefetchError: false,
    isRefetching: false,
    isStale: true,
    promise: Promise.resolve(undefined as unknown as FakeData),
    ...overrides,
  } as unknown as UseQueryResult<FakeData>;
}

describe("QueryBoundary", () => {
  it("renders the loading block while the query is loading", () => {
    render(
      <QueryBoundary query={fakeQuery({ isLoading: true, isPending: true })}>{(data) => <span>{data.rows.join(",")}</span>}</QueryBoundary>,
    );

    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("renders the error empty-state with the query's error message", () => {
    const error = new Error("boom");
    render(
      <QueryBoundary<FakeData> query={fakeQuery({ isError: true, error, status: "error" })}>
        {(data) => <span>{data.rows.join(",")}</span>}
      </QueryBoundary>,
    );

    expect(screen.getByText("Something went wrong")).toBeInTheDocument();
    expect(screen.getByText("boom")).toBeInTheDocument();
  });

  it("renders the empty-state when isEmpty returns true", () => {
    render(
      <QueryBoundary<FakeData>
        query={fakeQuery({ data: { rows: [] }, isSuccess: true, status: "success" })}
        emptyTitle="No rows returned"
        isEmpty={(data) => data.rows.length === 0}
      >
        {(data) => <span>{data.rows.join(",")}</span>}
      </QueryBoundary>,
    );

    expect(screen.getByText("No rows returned")).toBeInTheDocument();
  });

  it("renders the children with the data on success", () => {
    render(
      <QueryBoundary<FakeData>
        query={fakeQuery({ data: { rows: ["a", "b", "c"] }, isSuccess: true, status: "success" })}
      >
        {(data) => <span>rows:{data.rows.join(",")}</span>}
      </QueryBoundary>,
    );

    expect(screen.getByText("rows:a,b,c")).toBeInTheDocument();
  });
});
