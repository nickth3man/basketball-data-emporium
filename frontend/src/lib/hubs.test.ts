/**
 * Tests for `ui/src/lib/hubs.ts`.
 *
 * Pins down the canonical list of hub navigation entries. A regression
 * that drops a hub (e.g. `Teams` removal) trips a test rather than
 * silently shrinking the cross-nav.
 */
import { describe, expect, it } from "vitest";

import { HUBS, type HubLink } from "@/lib/hubs";

describe("HUBS", () => {
  it("has exactly the two v1 hubs: Players and Teams", () => {
    expect(HUBS).toHaveLength(2);
    expect(HUBS.map((entry) => entry.label)).toEqual(["Players", "Teams"]);
  });

  it("maps each hub to an absolute path under the app root", () => {
    for (const hub of HUBS) {
      expect(hub.href.startsWith("/")).toBe(true);
      // No trailing slash — `next/link` and the URL bar render the same path.
      expect(hub.href.endsWith("/")).toBe(false);
    }
  });

  it("has unique hrefs (no two hubs claim the same route)", () => {
    const seen = new Set<string>();
    for (const hub of HUBS) {
      expect(seen.has(hub.href)).toBe(false);
      seen.add(hub.href);
    }
  });

  it("conforms to the HubLink interface shape", () => {
    for (const hub of HUBS) {
      const shape: HubLink = hub;
      expect(typeof shape.label).toBe("string");
      expect(typeof shape.href).toBe("string");
      expect(shape.label.length).toBeGreaterThan(0);
    }
  });
});
