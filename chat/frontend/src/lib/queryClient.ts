/**
 * Singleton TanStack QueryClient.
 *
 * Defaults (oracle-reconciled, Phase 0):
 *   - `staleTime: 30_000` — chat data is conversational; 30s keeps the
 *     timeline snappy without thrashing the backend on each keystroke
 *     or focus change. Per-query overrides are expected in Phase 1
 *     (e.g. session list can be much longer-lived).
 *   - `refetchOnWindowFocus: false` — the chat is a single-user, single-
 *     tab workflow; a focus-triggered refetch produces distracting UI
 *     flicker and double-submits on textbox refocus. Opt in explicitly
 *     on queries that need freshness.
 *   - `retry: 1` — single retry covers transient network blips. Idempotent
 *     reads are safe to retry; non-idempotent mutations (Phase 1) will
 *     override per-call via `retry: 0` so we never silently re-fire a
 *     POST that may have already reached the backend.
 *
 * No defaults are set for `cacheTime` / `gcTime` here — the TanStack
 * defaults (5 min stale, 30 min inactive) are fine for the chat shape.
 */
import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});
