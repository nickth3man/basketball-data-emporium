/**
 * Promoted, hardened HTTP client for the Basketball Data Emporium API.
 *
 * Replaces the original 86-line `apiFetch` in
 * `ui/src/features/player-hub/api/client.ts`. The player-hub module is now
 * a thin compatibility shim that re-exports from here.
 */
import { parseApiError, TypedApiError } from "@/lib/api-errors";
import type { paths } from "@/lib/openapi-types";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_BASKETBALL_DATA_API_URL ?? "http://127.0.0.1:8765";

type OpenApiPath = keyof paths & string;
type ApiPath = OpenApiPath | (string & {});

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
  path: ApiPath,
  opts: ApiFetchOptions = {},
): Promise<T> {
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
  const params = new URLSearchParams({ dataset });
  const overlay = buildDatasetParams({ seasonEndYear, includeInactiveGames });
  for (const [key, value] of overlay) {
    params.set(key, value);
  }
  return `${API_BASE_URL}/api/teams/${identifier}/export?${params.toString()}`;
}

export async function downloadCsv(url: string, filename: string): Promise<void> {
  let response: Response;
  try {
    response = await fetch(url, {
      headers: { Accept: "text/csv, application/json" },
    });
  } catch (error) {
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

  if (!response.ok) {
    throw parseApiError(response, await safeJson(response));
  }

  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().includes("text/csv")) {
    throw new TypedApiError({
      code: "internal_error",
      status: response.status,
      detail: { content_type: contentType },
      message: "CSV export returned an unexpected content type",
    });
  }

  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);
}
