/**
 * Unit tests for the hardened HTTP client in `ui/src/lib/api-client.ts`.
 *
 * We stub `globalThis.fetch` with `vi.fn()` and feed it canned
 * `Response`-shaped objects. No real network is touched. The retry path
 * uses `vi.useFakeTimers()` so the `setTimeout` based backoff is
 * deterministic.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { API_BASE_URL, apiFetch, csvExportUrl, type ApiFetchOptions } from "@/lib/api-client";
import { TypedApiError } from "@/lib/api-errors";

/** Build a `Response`-shaped stub with a JSON body. */
function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : status === 429 ? "Too Many Requests" : "Error",
    headers: new Headers(headers),
    json: async () => body,
  } as unknown as Response;
}

/** vi.fn() cast to the `fetch` signature we care about. */
function mockFetch(): ReturnType<typeof vi.fn> & ((input: RequestInfo | URL, init?: RequestInit) => Promise<Response>) {
  return vi.fn() as unknown as ReturnType<typeof vi.fn> & ((
    input: RequestInfo | URL,
    init?: RequestInit,
  ) => Promise<Response>);
}

describe("apiFetch", () => {
  let fetchSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchSpy = mockFetch();
    globalThis.fetch = fetchSpy as unknown as typeof globalThis.fetch;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns parsed JSON for a 2xx response", async () => {
    fetchSpy.mockResolvedValueOnce(jsonResponse(200, { ok: true, value: 42 }));

    const result = await apiFetch<{ ok: boolean; value: number }>("/api/example");

    expect(result).toEqual({ ok: true, value: 42 });
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy.mock.calls[0]?.[0]).toBe(`${API_BASE_URL}/api/example`);
  });

  it("throws a TypedApiError with the envelope's code and the response status on non-2xx", async () => {
    fetchSpy.mockResolvedValueOnce(
      jsonResponse(404, { detail: { code: "invalid_player", message: "missing", detail: {} } }),
    );

    await expect(apiFetch("/api/players/unknown/summary")).rejects.toMatchObject({
      name: "TypedApiError",
      code: "invalid_player",
      status: 404,
    });
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("retries once on 429 with a Retry-After header and succeeds on the second call", async () => {
    vi.useFakeTimers();
    fetchSpy
      .mockResolvedValueOnce(jsonResponse(429, { detail: { code: "rate_limit_jailed", message: "x", detail: {} } }, { "Retry-After": "1" }))
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }));

    const promise = apiFetch<{ ok: boolean }>("/api/x");
    // Let the queued microtask for the first fetch resolve.
    await vi.advanceTimersByTimeAsync(0);
    // Let the backoff setTimeout fire (1s from Retry-After).
    await vi.advanceTimersByTimeAsync(1_000);

    const result = await promise;

    expect(result).toEqual({ ok: true });
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("throws a rate_limit_jailed TypedApiError when 429 hits twice — no infinite loop", async () => {
    vi.useFakeTimers();
    fetchSpy
      .mockResolvedValueOnce(jsonResponse(429, { detail: { code: "rate_limit_jailed", message: "x", detail: {} } }, { "Retry-After": "1" }))
      .mockResolvedValueOnce(jsonResponse(429, { detail: { code: "rate_limit_jailed", message: "x", detail: {} } }, { "Retry-After": "1" }));

    const promise = apiFetch<unknown>("/api/x");
    // Capture the rejection so the test can assert on it once we have driven the timers.
    const caught = promise.catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(1_000);
    await vi.advanceTimersByTimeAsync(0);

    const error = await caught;

    expect(error).toBeInstanceOf(TypedApiError);
    expect((error as TypedApiError).code).toBe("rate_limit_jailed");
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("wraps a network error (fetch rejection) in a TypedApiError with code 'internal_error' and status 0", async () => {
    fetchSpy.mockRejectedValueOnce(new Error("ECONNREFUSED"));

    await expect(apiFetch("/api/x")).rejects.toMatchObject({
      name: "TypedApiError",
      code: "internal_error",
      status: 0,
    });
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("rejects with a timeout-derived TypedApiError when the fetch never resolves before the deadline", async () => {
    vi.useFakeTimers();
    // A fetch promise that mirrors real `fetch` behavior on abort: it rejects
    // with an `AbortError` when the controller's signal aborts, but otherwise
    // never resolves. The internal `setTimeout(..., 1000)` is replaced by
    // fake timers, so advancing 1s triggers the abort.
    fetchSpy.mockImplementationOnce(
      (_url: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          const signal = init?.signal;
          if (signal) {
            signal.addEventListener(
              "abort",
              () => {
                reject(new DOMException("Aborted", "AbortError"));
              },
              { once: true },
            );
          }
        }),
    );

    const options: ApiFetchOptions = { timeoutMs: 1_000 };
    const promise = apiFetch<unknown>("/api/x", options);
    // Capture the rejection so the assertion can run after we advance time.
    const caught = promise.catch((error: unknown) => error);

    await vi.advanceTimersByTimeAsync(1_000);
    // Let the abort and the resulting microtasks flush.
    await vi.advanceTimersByTimeAsync(0);

    const error = await caught;

    expect(error).toBeInstanceOf(TypedApiError);
    expect((error as TypedApiError).code).toBe("internal_error");
  });
});

describe("csvExportUrl", () => {
  it("builds the basic URL with just identifier + dataset", () => {
    expect(csvExportUrl("jamesle01", "career")).toBe(
      `${API_BASE_URL}/api/players/jamesle01/export?dataset=career`,
    );
  });

  it("appends season_end_year when provided", () => {
    expect(csvExportUrl("jamesle01", "totals", 2024)).toBe(
      `${API_BASE_URL}/api/players/jamesle01/export?dataset=totals&season_end_year=2024`,
    );
  });

  it("appends include_inactive_games when the flag is true (no season)", () => {
    expect(csvExportUrl("jamesle01", "totals", undefined, true)).toBe(
      `${API_BASE_URL}/api/players/jamesle01/export?dataset=totals&include_inactive_games=true`,
    );
  });

  it("appends both season_end_year and include_inactive_games when both are supplied", () => {
    expect(csvExportUrl("jamesle01", "totals", 2024, true)).toBe(
      `${API_BASE_URL}/api/players/jamesle01/export?dataset=totals&season_end_year=2024&include_inactive_games=true`,
    );
  });

  it("omits include_inactive_games when the flag is false (the default)", () => {
    expect(csvExportUrl("jamesle01", "totals", 2024, false)).toBe(
      `${API_BASE_URL}/api/players/jamesle01/export?dataset=totals&season_end_year=2024`,
    );
  });
});
