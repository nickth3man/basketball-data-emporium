import { describe, expect, it } from "vitest";

import { asNumber, formatValue } from "@/features/player-hub/utils/format";

describe("format helpers", () => {
  it("formats empty, numeric, boolean, and array values", () => {
    expect(formatValue(null)).toBe("—");
    expect(formatValue(27.12345)).toBe("27.123");
    expect(formatValue(true)).toBe("Yes");
    expect(formatValue(["PG", "SG"])).toBe("PG, SG");
  });

  it("normalizes numeric strings", () => {
    expect(asNumber("12.5")).toBe(12.5);
    expect(asNumber("not a number")).toBeNull();
  });
});
