/**
 * Typed REST client.
 *
 * Wraps `openapi-fetch` against the `paths` interface generated from
 * `frontend/openapi.json` (which mirrors the FastAPI app's `openapi()`).
 *
 * Regenerating types end-to-end (drift-guarded in CI):
 *
 *   1. From `chat/` (backend): `uv run python scripts/export_openapi.py`
 *      → writes `chat/frontend/openapi.json` from the live FastAPI app.
 *      CI runs this then `git diff --exit-code frontend/openapi.json`
 *      (drift guard).
 *   2. From `chat/frontend/` (this dir): `npm run gen:types`
 *      → writes `src/generated/api.d.ts` from the committed
 *      `openapi.json`.
 *   3. `tsc --noEmit`, `vite build`, `eslint .` (Phase 2 exit gates).
 *
 * URL convention:
 *   - Generated `paths` keys include the `/api` prefix (because that's
 *     how the FastAPI routers are mounted: `app.include_router(router,
 *     prefix="/api")`).
 *   - `baseUrl` is left as the empty string so `baseUrl + path` resolves
 *     to a single, predictable URL (`/api/sessions`) that the Vite dev
 *     server's `/api -> http://localhost:8787` proxy can forward.
 *   - For a deployed build, the same `/api/...` path is served by the
 *     backend on the same origin, so no `baseUrl` change is needed.
 *
 * The wrapped helpers (`createSession`, `listSessions`, …) narrow the
 * typed responses to one call-site and discard the success/error union
 * by throwing on `error`. Components are also re-exported so callers
 * (e.g. `useSessions`) can refer to schema types without reaching into
 * `@/generated/api` directly.
 */
import createClient from "openapi-fetch";

import type { components, paths } from "@/generated/api";

// --- Typed session/health schema re-exports -----------------------------
// App code should import these from `@/api/client`, not from the raw
// `@/generated/api`, so the generated module's surface stays private.

export type SessionMeta = components["schemas"]["SessionMeta"];
export type SessionMessage = components["schemas"]["SessionMessage"];
export type HistoryPage = components["schemas"]["HistoryPage"];
export type HealthResponse = components["schemas"]["HealthResponse"];

// --- Client singleton ---------------------------------------------------

export const apiClient = createClient<paths>({ baseUrl: "" });

// --- Typed helper wrappers ----------------------------------------------
// Each helper throws on a transport error (non-2xx, no body) and returns
// the parsed response. The plan calls for a "create + read history with
// full types" wiring proof; the helpers below are the load-bearing ones.

type CreateSessionBody = components["schemas"]["CreateSessionRequest"];

/** `POST /api/sessions` → 201 `SessionMeta`. */
export async function createSession(title?: string | null): Promise<SessionMeta> {
  const body: CreateSessionBody = { title: title ?? null };
  const { data, error } = await apiClient.POST("/api/sessions", { body });
  if (error || !data) throw new Error(`failed to create session: ${error ?? "no data"}`);
  return data as SessionMeta;
}

/** `GET /api/sessions` → 200 `SessionMeta[]`. */
export async function listSessions(): Promise<SessionMeta[]> {
  const { data, error } = await apiClient.GET("/api/sessions", {});
  if (error || !data) throw new Error(`failed to list sessions: ${error ?? "no data"}`);
  return data as SessionMeta[];
}

/** `GET /api/sessions/{id}/history?limit&offset` → 200 `HistoryPage`. */
export async function getSessionHistory(
  sessionId: string,
  limit = 50,
  offset = 0,
): Promise<HistoryPage> {
  const { data, error } = await apiClient.GET("/api/sessions/{session_id}/history", {
    params: {
      path: { session_id: sessionId },
      query: { limit, offset },
    },
  });
  if (error || !data) {
    throw new Error(`failed to load history: ${error ?? "no data"}`);
  }
  return data as HistoryPage;
}

/** `DELETE /api/sessions/{id}` → 204 (no content). */
export async function deleteSession(sessionId: string): Promise<void> {
  const { error } = await apiClient.DELETE("/api/sessions/{session_id}", {
    params: { path: { session_id: sessionId } },
  });
  if (error) throw new Error(`failed to clear history: ${error}`);
}

/** `GET /api/health` → 200 `HealthResponse`. */
export async function getHealth(): Promise<HealthResponse> {
  const { data, error } = await apiClient.GET("/api/health", {});
  if (error || !data) throw new Error(`health check failed: ${error ?? "no data"}`);
  return data as HealthResponse;
}
