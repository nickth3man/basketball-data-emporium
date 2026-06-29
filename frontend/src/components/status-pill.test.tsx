/**
 * Component tests for `ui/src/components/status-pill.tsx`.
 *
 * The pill reads `useStatus()` directly and chooses between rate-limit,
 * loading, offline, and audit-state presentations. We mock the
 * hook to feed it controlled `{data, error, isLoading}` shapes and
 * assert the rendered DOM for each branch.
 */
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TypedApiError } from "@/lib/api-errors";
import { StatusPill } from "@/components/status-pill";
import type { StatusResponse } from "@/features/player-hub/types";

/** Hoisted shared mock — the factory below re-exports the same ref. */
const { useStatus } = vi.hoisted(() => ({ useStatus: vi.fn() }));

vi.mock("@/lib/use-status", () => ({
  useStatus,
}));

const healthyStatus: StatusResponse = {
  ok: true,
  endpoint_count: 50,
  data_state: "passed",
  data_verified: true,
  data_stale: false,
  latest_pipeline_run_id: "run-1",
  latest_pipeline_stage: "load",
  latest_pipeline_status: "success",
  latest_pipeline_started_at: "2026-06-29T00:00:00Z",
  latest_dq_status: "passed",
};

describe("StatusPill", () => {
  beforeEach(() => {
    useStatus.mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the Verified pill when audit state passed", () => {
    useStatus.mockReturnValue({
      data: healthyStatus,
      error: null,
      isLoading: false,
      isError: false,
      isSuccess: true,
    });

    render(<StatusPill />);

    expect(screen.getByText("Verified")).toBeInTheDocument();
  });

  it("renders failed, stale, and unverified audit states", () => {
    for (const [dataState, label] of [
      ["failed", "DQ failed"],
      ["stale", "Stale"],
      ["unverified", "Unverified"],
    ] as const) {
      useStatus.mockReturnValue({
        data: { ...healthyStatus, data_state: dataState },
        error: null,
        isLoading: false,
        isError: false,
        isSuccess: true,
      });

      const { unmount } = render(<StatusPill />);
      expect(screen.getByText(label)).toBeInTheDocument();
      unmount();
    }
  });

  it("renders the red 'Rate limited' pill when useStatus has a rate_limit_jailed error", () => {
    const error = new TypedApiError({
      code: "rate_limit_jailed",
      status: 429,
      detail: { retry_after: 5 },
      message: "Jailed",
      retryAfter: 5,
    });
    useStatus.mockReturnValue({
      data: undefined,
      error,
      isLoading: false,
      isError: true,
      isSuccess: false,
    });

    render(<StatusPill />);

    // The pill uses the count-down format `Rate limited (Xs)` when
    // `retryAfter` is provided.
    expect(screen.getByText("Rate limited (5s)")).toBeInTheDocument();
  });

  it("renders the red 'Rate limited' pill with no countdown when retryAfter is absent", () => {
    const error = new TypedApiError({
      code: "rate_limit_jailed",
      status: 429,
      detail: {},
      message: "Jailed",
    });
    useStatus.mockReturnValue({
      data: undefined,
      error,
      isLoading: false,
      isError: true,
      isSuccess: false,
    });

    render(<StatusPill />);

    expect(screen.getByText("Rate limited")).toBeInTheDocument();
  });

  it("renders the loading pill while the query is loading", () => {
    useStatus.mockReturnValue({
      data: undefined,
      error: null,
      isLoading: true,
      isError: false,
      isSuccess: false,
    });

    render(<StatusPill />);

    expect(screen.getByText("API")).toBeInTheDocument();
  });

  it("renders the offline pill when the query errored with a non-rate-limit error", () => {
    const error = new Error("service down");
    useStatus.mockReturnValue({
      data: undefined,
      error,
      isLoading: false,
      isError: true,
      isSuccess: false,
    });

    render(<StatusPill />);

    expect(screen.getByText("Offline")).toBeInTheDocument();
  });
});
