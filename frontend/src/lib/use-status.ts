import { useQuery } from "@tanstack/react-query";

import { getStatus } from "@/features/player-hub/api/client";
import { queryKeys } from "@/features/player-hub/api/query-keys";
import type { StatusResponse } from "@/features/player-hub/types";

/**
 * Shared `useStatus` hook — drives the API status pill and any other
 * consumer that needs the `/api/status` payload.
 *
 * The query is feature-agnostic (the status endpoint is the same regardless
 * of which hub renders the pill), so the canonical implementation lives in
 * `@/lib/use-status` and the per-feature `queries.ts` modules re-export it
 * for any caller that has not yet been migrated to the shared path.
 *
 * The query key is `queryKeys.status` from the player-hub module — both
 * features happen to use the same `["status"]` key today, so a re-export
 * from the player-hub keeps the cache shared between the two hubs without
 * changing any react-query semantics. The player-hub's `QueryKeys.status`
 * is the single source of truth until the codegen migration in Track 4
 * hoists the type alongside the rest of the shared API surface.
 */
export function useStatus() {
  return useQuery<StatusResponse>({
    queryKey: queryKeys.status,
    queryFn: getStatus,
  });
}
