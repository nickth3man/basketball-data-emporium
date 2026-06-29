/**
 * Tests for `ui/src/lib/sample-teams.ts`.
 *
 * Cheap lint-as-test: the constants below pin down the shape of the
 * hand-curated "Featured franchises" sidebar list. A regression that
 * changes a `SAMPLE_TEAMS` entry (e.g. a typo on the identifier, an
 * empty list) trips a test rather than a silent 404 on `/teams/...`.
 */
import { describe, expect, it } from "vitest";

import { SAMPLE_TEAMS, type SampleTeam } from "@/lib/sample-teams";

describe("SAMPLE_TEAMS", () => {
  it("is a non-empty array", () => {
    expect(SAMPLE_TEAMS.length).toBeGreaterThan(0);
  });

  it("uses 3-character uppercase Basketball Reference identifiers", () => {
    for (const team of SAMPLE_TEAMS) {
      expect(team.identifier).toMatch(/^[A-Z]{3}$/);
    }
  });

  it("has a non-empty display name for every entry", () => {
    for (const team of SAMPLE_TEAMS) {
      expect(team.name.length).toBeGreaterThan(0);
    }
  });

  it("has unique identifiers (no duplicate deep-link targets)", () => {
    const seen = new Set<string>();
    for (const team of SAMPLE_TEAMS) {
      expect(seen.has(team.identifier)).toBe(false);
      seen.add(team.identifier);
    }
  });

  it("conforms to the SampleTeam interface shape", () => {
    // Lightweight structural check: every entry has the required fields,
    // and the optional `blurb` (if present) is a non-empty string.
    for (const team of SAMPLE_TEAMS) {
      const shape: SampleTeam = team;
      expect(typeof shape.identifier).toBe("string");
      expect(typeof shape.name).toBe("string");
      if (shape.blurb !== undefined) {
        expect(typeof shape.blurb).toBe("string");
        expect(shape.blurb.length).toBeGreaterThan(0);
      }
    }
  });
});
