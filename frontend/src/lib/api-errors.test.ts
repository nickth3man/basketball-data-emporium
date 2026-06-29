/**
 * Unit tests for the typed API error surface in `ui/src/lib/api-errors.ts`.
 *
 * No real `fetch` involved — `Response` is constructed by hand (and cast
 * to the structural shape we need) so we stay on the test-only path.
 */
import { describe, expect, it } from "vitest";

import {
  ApiErrorCode,
  isRateLimited,
  parseApiError,
  TypedApiError,
} from "@/lib/api-errors";

/** Build a `Response`-shaped stub without calling into the network. */
function makeResponse(status: number, headers: Record<string, string> = {}): Response {
  return {
    status,
    statusText: status === 404 ? "Not Found" : status === 429 ? "Too Many Requests" : "Error",
    headers: new Headers(headers),
  } as unknown as Response;
}

describe("parseApiError", () => {
  it("extracts code, message, and detail from a FastAPI envelope body", () => {
    const response = makeResponse(404);
    const body = {
      detail: {
        code: "invalid_player",
        message: "Player not found",
        detail: { identifier: "jamesle01" },
      },
    };

    const err = parseApiError(response, body);

    expect(err).toBeInstanceOf(TypedApiError);
    expect(err.code).toBe<ApiErrorCode>("invalid_player");
    expect(err.status).toBe(404);
    expect(err.message).toBe("Player not found");
    expect(err.detail).toEqual({ identifier: "jamesle01" });
  });

  it("reads retryAfter from detail.retry_after when present", () => {
    const response = makeResponse(429, { "Retry-After": "30" });
    const body = {
      detail: {
        code: "rate_limit_jailed",
        message: "Jailed",
        detail: { retry_after: 5 },
      },
    };

    const err = parseApiError(response, body);

    expect(err.code).toBe<ApiErrorCode>("rate_limit_jailed");
    expect(err.retryAfter).toBe(5);
  });

  it("falls back to the Retry-After header when detail.retry_after is absent", () => {
    const response = makeResponse(429, { "Retry-After": "12" });
    const body = {
      detail: { code: "rate_limit_jailed", message: "Jailed", detail: {} },
    };

    const err = parseApiError(response, body);

    expect(err.retryAfter).toBe(12);
  });

  it("falls back to 'unknown' for malformed body without throwing", () => {
    const response = makeResponse(500);

    const fromNull = parseApiError(response, null);
    expect(fromNull.code).toBe("unknown");
    expect(fromNull.status).toBe(500);
    expect(fromNull.detail).toEqual({});

    const fromMissingDetail = parseApiError(response, { foo: "bar" });
    expect(fromMissingDetail.code).toBe("unknown");
  });
});

describe("isRateLimited", () => {
  it("returns true for a TypedApiError with code 'rate_limit_jailed'", () => {
    const err = new TypedApiError({
      code: "rate_limit_jailed",
      status: 429,
      detail: {},
      message: "Jailed",
      retryAfter: 5,
    });

    expect(isRateLimited(err)).toBe(true);
  });

  it("returns false for a generic Error", () => {
    expect(isRateLimited(new Error("boom"))).toBe(false);
  });

  it("returns false for null and undefined", () => {
    expect(isRateLimited(null)).toBe(false);
    expect(isRateLimited(undefined)).toBe(false);
  });
});

describe("TypedApiError", () => {
  it("constructs with name 'TypedApiError' and all fields populated", () => {
    const err = new TypedApiError({
      code: "invalid_season",
      status: 404,
      detail: { season: 1850 },
      message: "Season out of range",
    });

    expect(err.name).toBe("TypedApiError");
    expect(err.code).toBe<ApiErrorCode>("invalid_season");
    expect(err.status).toBe(404);
    expect(err.detail).toEqual({ season: 1850 });
    expect(err.message).toBe("Season out of range");
    expect(err.retryAfter).toBeUndefined();
    expect(err).toBeInstanceOf(Error);
  });
});
