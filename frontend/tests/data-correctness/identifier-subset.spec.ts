/**
 * MVCS Test 4 — Identifier subset.
 *
 * Asserts every hardcoded BBR slug in the frontend's sample data
 * resolves through the live sidecar to a real player / team record
 * in `unified_star.dim_player` / `unified_star.dim_team`.
 *
 * Implementation: this spec uses the `@playwright/test` `request`
 * fixture (no browser) to hit the sidecar at
 * `http://127.0.0.1:8765` directly. The sidecar must be running
 * for this spec to pass; the test fails fast with a clear
 * connection-refused error otherwise.
 *
 * Slug sources (the spec says "read the slugs from sample-*.ts"):
 *   - frontend/src/lib/sample-athletes.ts → SAMPLE_ATHLETES[*].identifier
 *   - frontend/src/lib/sample-teams.ts    → SAMPLE_TEAMS[*].identifier
 *
 * Phase 2 implication: if any slug in the hand-curated fallback
 * list does not exist in the DB, the corresponding sidebar
 * renders an empty card. This test makes that a CI failure.
 */

import { test, expect, type APIResponse } from "@playwright/test";

import { SAMPLE_ATHLETES } from "../../src/lib/sample-athletes";
import { SAMPLE_TEAMS } from "../../src/lib/sample-teams";

const API_BASE = process.env.BASKETBALL_DATA_API_URL ?? "http://127.0.0.1:8765";

interface SearchResult {
  name?: string;
  identifier: string;
  leagues?: string[];
}

function playerIdentifiers(): string[] {
  return SAMPLE_ATHLETES.map((a) => a.identifier);
}

function teamIdentifiers(): string[] {
  return SAMPLE_TEAMS.map((t) => t.identifier);
}

async function searchByIdentifier(
  request: import("@playwright/test").APIRequestContext,
  identifier: string,
  owner: "players" | "teams" = "players",
): Promise<{ status: number; results: SearchResult[] }> {
  const response: APIResponse = await request.get(
    `${API_BASE}/api/${owner}/search?term=${encodeURIComponent(identifier)}`,
  );
  const status = response.status();
  const body = (await response.json().catch(() => [])) as unknown;
  const results: SearchResult[] = Array.isArray(body)
    ? (body as SearchResult[])
    : [];
  return { status, results };
}

test.beforeAll(async ({ request }) => {
  // Fail fast with a clear message if the sidecar is not reachable.
  // This is a correctness gate, not a robustness test — the sidecar
  // is a hard dependency.
  let reachable = false;
  try {
    const r = await request.get(`${API_BASE}/api/status`, { timeout: 5_000 });
    reachable = r.ok();
  } catch {
    reachable = false;
  }
  if (!reachable) {
    throw new Error(
      `Sidecar is not reachable at ${API_BASE}. ` +
        `Start it with: cd backend && uv run basketball-data-emporium serve. ` +
        `This MVCS test is a correctness gate and requires the live sidecar.`,
    );
  }
});

test.describe("sample-athletes slugs", () => {
  for (const identifier of playerIdentifiers()) {
    test(`player slug ${identifier} resolves via /api/players/search`, async ({
      request,
    }) => {
      const { status, results } = await searchByIdentifier(request, identifier);
      expect(status, `GET /api/players/search?term=${identifier}`).toBe(200);
      const match = results.find((r) => r.identifier === identifier);
      expect(
        match,
        `slug '${identifier}' from sample-athletes.ts was not found in ` +
          `/api/players/search results (got ${results.length} result(s))`,
      ).toBeDefined();
    });
  }
});

test.describe("sample-teams slugs", () => {
  for (const identifier of teamIdentifiers()) {
    test(`team slug ${identifier} resolves via /api/teams/search`, async ({
      request,
    }) => {
      const { status, results } = await searchByIdentifier(request, identifier, "teams");
      expect(
        status,
        `GET /api/teams/search?term=${identifier}`,
      ).toBe(200);

      const match = results.find((r) => r.identifier === identifier);
      expect(
        match,
        `slug '${identifier}' from sample-teams.ts was not found in ` +
          `/api/teams/search results (got ${results.length} result(s))`,
      ).toBeDefined();
    });
  }
});

test("sample-athletes match /api/players/featured", async ({ request }) => {
  const response = await request.get(`${API_BASE}/api/players/featured`);
  expect(response.status()).toBe(200);
  const body = (await response.json()) as { athletes: SearchResult[] };
  expect(body.athletes.map((entry) => entry.identifier)).toEqual(playerIdentifiers());
});

test("sample-teams match /api/teams/featured", async ({ request }) => {
  const response = await request.get(`${API_BASE}/api/teams/featured`);
  expect(response.status()).toBe(200);
  const body = (await response.json()) as { teams: SearchResult[] };
  expect(body.teams.map((entry) => entry.identifier)).toEqual(teamIdentifiers());
});
