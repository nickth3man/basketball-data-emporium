"use client";

import { Database, LoaderCircle, WifiOff } from "lucide-react";

import { useStatus } from "@/lib/use-status";
import { isRateLimited } from "@/lib/api-errors";

/**
 * API status pill — surfaces the live-connection health of the API.
 *
 * The project serves only live Basketball Reference data (there is no
 * offline/fixture data mode), so the steady state is a simple "Live"
 * indicator. The two signals that need user-visible attention are:
 *
 * 1. **Rate-limit jail** — the `useStatus` query is in an error state
 *    with `code === "rate_limit_jailed"`. We read the error from the
 *    query result (not from a prop) so the pill can also surface the
 *    server-supplied `retryAfter` countdown. Using the shared hook
 *    here keeps the pill a thin visual component and lets it be
 *    rendered by any feature without per-feature plumbing.
 *
 * 2. **Offline** — the status query failed or reported not-ok, i.e. the
 *    API server is unreachable.
 *
 * TODO P1-FE-01: once `/api/status` includes audit/DQ fields, expand this
 * state machine beyond Live/Offline. Required states are: verified data,
 * failed latest ETL, stale DQ result, data present but unverified, offline,
 * and rate-limited. Keep the visual language compact because this pill appears
 * in every hub header.
 */
export function StatusPill() {
  const status = useStatus();

  // 1. Rate-limit — highest priority. Overrides every other state so a
  //    user who is currently throttled always sees the actionable pill.
  if (isRateLimited(status.error)) {
    const retryAfter = status.error.retryAfter;
    const label = retryAfter !== undefined ? `Rate limited (${retryAfter}s)` : "Rate limited";
    return (
      <span className="inline-flex h-8 items-center gap-2 rounded-md border border-court-danger-line bg-court-danger-soft px-3 text-xs font-medium text-court-danger">
        <WifiOff className="size-3.5" aria-hidden="true" />
        {label}
      </span>
    );
  }

  if (status.isLoading) {
    return (
      <span className="inline-flex h-8 items-center gap-2 rounded-md border border-court-line bg-white px-3 text-xs text-court-muted">
        <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />
        API
      </span>
    );
  }

  if (status.isError || !status.data?.ok) {
    return (
      <span className="inline-flex h-8 items-center gap-2 rounded-md border border-court-danger-line bg-court-danger-soft px-3 text-xs font-medium text-court-danger">
        <WifiOff className="size-3.5" aria-hidden="true" />
        Offline
      </span>
    );
  }

  return (
    <span className="inline-flex h-8 items-center gap-2 rounded-md border border-court-accent-line bg-court-accent-soft px-3 text-xs font-medium text-court-accent-strong">
      <Database className="size-3.5" aria-hidden="true" />
      Live
    </span>
  );
}
