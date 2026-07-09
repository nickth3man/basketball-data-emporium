/**
 * Session-list React hook (typed-wiring proof).
 *
 * Owns the in-memory session list and pings the health endpoint once at
 * mount. Mutations (`create`, `clearHistory`) refresh the list after
 * settling. This hook will stay around as the session-management
 * primitive; `useChatTurn` drives the streaming turn lifecycle.
 */
import { useCallback, useEffect, useState } from "react";

import {
  createSession as apiCreateSession,
  deleteSession as apiDeleteSession,
  getHealth as apiGetHealth,
  listSessions as apiListSessions,
  type SessionMeta,
} from "@/api/client";

/** Coarse health status derived from `GET /api/health`. */
export type HealthStatus = "ok" | "degraded" | "unknown";

export interface UseSessionsResult {
  /** All known sessions (newest list refresh wins). */
  sessions: SessionMeta[];
  /** Whichever refresh is in flight (re-fetch or mutation-triggered). */
  loading: boolean;
  /** Hard-coded health snapshot: ok / degraded / unknown (still loading). */
  health: HealthStatus;
  /** Re-fetch the session list without changing it. */
  refresh: () => Promise<void>;
  /** Create a session server-side and refresh; returns the new meta. */
  create: (title?: string | null) => Promise<SessionMeta>;
  /** Clear the visible history of a session and refresh. */
  clearHistory: (id: string) => Promise<void>;
}

export function useSessions(): UseSessionsResult {
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [health, setHealth] = useState<HealthStatus>("unknown");
  const [loading, setLoading] = useState<boolean>(false);

  const refresh = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const next = await apiListSessions();
      setSessions(next);
    } finally {
      setLoading(false);
    }
  }, []);

  const create = useCallback(
    async (title?: string | null): Promise<SessionMeta> => {
      const session = await apiCreateSession(title ?? null);
      await refresh();
      return session;
    },
    [refresh],
  );

  const clearHistory = useCallback(
    async (id: string): Promise<void> => {
      await apiDeleteSession(id);
      await refresh();
    },
    [refresh],
  );

  useEffect(() => {
    void refresh();
    apiGetHealth()
      .then((res) => setHealth(res.db === "connected" ? "ok" : "degraded"))
      .catch(() => setHealth("unknown"));
  }, [refresh]);

  return { sessions, loading, health, refresh, create, clearHistory };
}
