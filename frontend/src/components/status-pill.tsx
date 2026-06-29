"use client";

import { AlertTriangle, CheckCircle2, Clock3, Database, LoaderCircle, WifiOff } from "lucide-react";

import { useStatus } from "@/lib/use-status";
import { isRateLimited } from "@/lib/api-errors";

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

  const state = status.data.data_state;
  if (state === "passed") {
    return (
      <span className="inline-flex h-8 items-center gap-2 rounded-md border border-court-accent-line bg-court-accent-soft px-3 text-xs font-medium text-court-accent-strong">
        <CheckCircle2 className="size-3.5" aria-hidden="true" />
        Verified
      </span>
    );
  }
  if (state === "failed") {
    return (
      <span className="inline-flex h-8 items-center gap-2 rounded-md border border-court-danger-line bg-court-danger-soft px-3 text-xs font-medium text-court-danger">
        <AlertTriangle className="size-3.5" aria-hidden="true" />
        DQ failed
      </span>
    );
  }
  if (state === "stale") {
    return (
      <span className="inline-flex h-8 items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 text-xs font-medium text-amber-800">
        <Clock3 className="size-3.5" aria-hidden="true" />
        Stale
      </span>
    );
  }
  return (
    <span className="inline-flex h-8 items-center gap-2 rounded-md border border-court-line bg-white px-3 text-xs font-medium text-court-muted">
      <Database className="size-3.5" aria-hidden="true" />
      Unverified
    </span>
  );
}
