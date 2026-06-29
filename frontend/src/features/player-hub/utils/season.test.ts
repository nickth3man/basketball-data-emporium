import { describe, expect, it } from "vitest";

import { seasonEndYearFromLabel, seasonLabel } from "@/features/player-hub/utils/season";

describe("season helpers", () => {
  it("converts season labels to end years and back", () => {
    expect(seasonLabel(2024)).toBe("2023-24");
    expect(seasonEndYearFromLabel("1999-00")).toBe(2000);
    expect(seasonEndYearFromLabel("2023-24")).toBe(2024);
  });
});
