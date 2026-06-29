/**
 * Promoted, hardened HTTP client for the Courtside Data API.
 *
 * Replaces the original 86-line `apiFetch` in
 * `ui/src/features/player-hub/api/client.ts`. The player-hub module is now
 * a thin compatibility shim that re-exports from here.
 */
import { parseApiError, TypedApiError } from "@/lib/api-errors";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_COURTSIDE_API_URL ?? "http://127.0.0.1:8765";

// TODO P0-BE-01: once backend CORS is configured, add a browser-level E2E test
// that calls this URL from the Next origin and proves the response is not
// blocked by the browser. Unit tests and server-side tests cannot catch CORS.

export interface ApiFetchOptions {
  /** Total request timeout in milliseconds. Defaults to 10s. */
  timeoutMs?: number;
  /** Caller-supplied abort signal; aborts propagate to the in-flight request. */
  signal?: AbortSignal;
}

interface InternalOptions {
  timeoutMs: number;
  signal: AbortSignal | undefined;
}

function normalizeOptions(opts: ApiFetchOptions): InternalOptions {
  return {
    timeoutMs: opts.timeoutMs ?? 10_000,
    signal: opts.signal,
  };
}

/**
 * Manually link a caller's `AbortSignal` with the timeout controller. We
 * avoid `AbortSignal.any([...])` for explicitness — every abort path is
 * visible in one place and works on older runtimes.
 */
function buildLinkedController(opts: InternalOptions): {
  controller: AbortController;
  clear: () => void;
} {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), opts.timeoutMs);

  let externalListener: (() => void) | undefined;
  if (opts.signal) {
    if (opts.signal.aborted) {
      controller.abort();
    } else {
      externalListener = () => controller.abort();
      opts.signal.addEventListener("abort", externalListener, { once: true });
    }
  }

  return {
    controller,
    clear: () => {
      clearTimeout(timer);
      if (externalListener && opts.signal) {
        opts.signal.removeEventListener("abort", externalListener);
      }
    },
  };
}

/** Reject after `ms` unless the caller's signal aborts first. */
function waitWithSignal(
  ms: number,
  signal: AbortSignal | undefined,
): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal?.reason ?? new DOMException("Aborted", "AbortError"));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

async function safeJson(response: Response): Promise<unknown> {
  try {
    return (await response.json()) as unknown;
  } catch {
    return null;
  }
}

export async function apiFetch<T>(
  path: string,
  opts: ApiFetchOptions = {},
): Promise<T> {
  // TODO P2-FE-01: replace hand-written string paths with generated
  // operation-aware helpers. `openapi-types.ts` now has every path/operation,
  // but this client still accepts arbitrary strings and trusts callers to match
  // parameters and response types manually.
  const normalized = normalizeOptions(opts);
  let retried = false;

  for (;;) {
    const { controller, clear } = buildLinkedController(normalized);
    let response: Response;
    try {
      response = await fetch(`${API_BASE_URL}${path}`, {
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
    } catch (error) {
      clear();
      throw new TypedApiError({
        code: "internal_error",
        status: 0,
        detail: {
          error_type: "network",
          reason: error instanceof Error ? error.message : String(error),
        },
        message: error instanceof Error ? error.message : "Network error",
      });
    }
    clear();

    if (response.ok) {
      return (await response.json()) as T;
    }

    const body = await safeJson(response);
    const typed = parseApiError(response, body);

    if (response.status === 429 && !retried) {
      if (typed.retryAfter !== undefined && Number.isFinite(typed.retryAfter)) {
        retried = true;
        await waitWithSignal(typed.retryAfter * 1000, normalized.signal);
        continue;
      }
    }

    throw typed;
  }
}

export function buildDatasetParams(opts: {
  seasonEndYear?: number;
  includeInactiveGames?: boolean;
}): URLSearchParams {
  const params = new URLSearchParams();
  if (opts.seasonEndYear !== undefined) {
    params.set("season_end_year", String(opts.seasonEndYear));
  }
  if (opts.includeInactiveGames) {
    params.set("include_inactive_games", "true");
  }
  return params;
}

/**
 * Build a fully-qualified URL to the player CSV export endpoint. Mirrors
 * the original `csvExportUrl` from the player-hub client; moved here so
 * every URL the UI hits is constructed in one place.
 */
export function csvExportUrl(
  identifier: string,
  dataset: string,
  seasonEndYear?: number,
  includeInactiveGames = false,
): string {
  // TODO P1-FE-03: anchor-based CSV downloads bypass `apiFetch`, so typed API
  // errors render as raw browser behavior. Replace this URL-only helper with a
  // download action that fetches the CSV, checks `Content-Type`, handles
  // `ApiError` envelopes, and then creates a blob URL for successful exports.
  const params = new URLSearchParams({ dataset });
  const overlay = buildDatasetParams({ seasonEndYear, includeInactiveGames });
  for (const [key, value] of overlay) {
    params.set(key, value);
  }
  return `${API_BASE_URL}/api/players/${identifier}/export?${params.toString()}`;
}

/** Build a fully-qualified URL to the team CSV export endpoint. */
export function teamCsvExportUrl(
  identifier: string,
  dataset: string,
  seasonEndYear?: number,
  includeInactiveGames = false,
): string {
  // TODO P1-FE-03: keep player/team export behavior aligned when moving from
  // anchor URLs to an in-app download handler.
  const params = new URLSearchParams({ dataset });
  const overlay = buildDatasetParams({ seasonEndYear, includeInactiveGames });
  for (const [key, value] of overlay) {
    params.set(key, value);
  }
  return `${API_BASE_URL}/api/teams/${identifier}/export?${params.toString()}`;
}
