/**
 * Typed error surface for the Courtside Data HTTP API.
 *
 * Sourced from `courtside_data.server.app._map_exception` — the canonical
 * mapping between domain exceptions and stable error codes. Keep this list
 * in sync if a new code is added server-side.
 */
export type ApiErrorCode =
  | "invalid_search"
  | "bad_request"
  | "invalid_player"
  | "invalid_team"
  | "invalid_season"
  | "rate_limit_jailed"
  | "schema_drift"
  | "internal_error";

const KNOWN_CODES: ReadonlySet<string> = new Set<ApiErrorCode>([
  "invalid_search",
  "bad_request",
  "invalid_player",
  "invalid_team",
  "invalid_season",
  "rate_limit_jailed",
  "schema_drift",
  "internal_error",
]);

export interface TypedApiErrorInit {
  code: ApiErrorCode | "unknown";
  status: number;
  detail: Record<string, unknown>;
  message: string;
  retryAfter?: number;
}

/**
 * Error raised when an API call fails. Carries a stable `code` that maps
 * 1:1 to a server-side exception category, plus the HTTP status and the
 * FastAPI `detail` payload (extracted defensively).
 */
export class TypedApiError extends Error {
  readonly code: ApiErrorCode | "unknown";
  readonly status: number;
  readonly detail: Record<string, unknown>;
  readonly retryAfter?: number;

  constructor(init: TypedApiErrorInit) {
    super(init.message);
    this.name = "TypedApiError";
    this.code = init.code;
    this.status = init.status;
    this.detail = init.detail;
    this.retryAfter = init.retryAfter;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function asFiniteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

/**
 * Build a `TypedApiError` from a `fetch` `Response` and its (already
 * parsed, possibly `null`) JSON body. The body is expected to follow the
 * FastAPI envelope shape `{ detail: { code, message, detail } }`; anything
 * missing or malformed falls back to a best-effort value rather than
 * throwing during error construction.
 */
export function parseApiError(response: Response, body: unknown): TypedApiError {
  let code: ApiErrorCode | "unknown" = "unknown";
  let message = `${response.status} ${response.statusText}`;
  let detail: Record<string, unknown> = {};

  if (isRecord(body) && isRecord(body.detail)) {
    const inner = body.detail;
    const rawCode = asString(inner.code);
    if (rawCode && KNOWN_CODES.has(rawCode)) {
      code = rawCode as ApiErrorCode;
    }
    const rawMessage = asString(inner.message);
    if (rawMessage) {
      message = rawMessage;
    }
    if (isRecord(inner.detail)) {
      detail = inner.detail;
    }
  }

  // Prefer the structured `retry_after` field; fall back to the header.
  const detailRetry = asFiniteNumber(detail.retry_after);
  let retryAfter: number | undefined;
  if (detailRetry !== undefined) {
    retryAfter = detailRetry;
  } else {
    const headerValue = response.headers.get("Retry-After");
    if (headerValue) {
      const parsed = Number.parseInt(headerValue, 10);
      if (Number.isFinite(parsed)) {
        retryAfter = parsed;
      }
    }
  }

  return new TypedApiError({ code, message, status: response.status, detail, retryAfter });
}

/** Narrow an unknown thrown value to a rate-limit `TypedApiError`. */
export function isRateLimited(err: unknown): err is TypedApiError {
  return err instanceof TypedApiError && err.code === "rate_limit_jailed";
}
