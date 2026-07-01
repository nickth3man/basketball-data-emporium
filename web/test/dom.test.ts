import { describe, expect, it } from "vitest";
import { el, formatPct, formatValue } from "../src/dom.ts";

describe("formatValue", () => {
  it("renders an em dash for null, undefined, and empty string", () => {
    expect(formatValue(null)).toBe("—");
    expect(formatValue(undefined)).toBe("—");
    expect(formatValue("")).toBe("—");
  });

  it("renders booleans as Yes/No", () => {
    expect(formatValue(true)).toBe("Yes");
    expect(formatValue(false)).toBe("No");
  });

  it("keeps integers whole and rounds floats to one decimal", () => {
    expect(formatValue(12)).toBe("12");
    expect(formatValue(12.345)).toBe("12.3");
  });
});

describe("formatPct", () => {
  it("renders an em dash for null/undefined/non-numeric input", () => {
    expect(formatPct(null)).toBe("—");
    expect(formatPct(undefined)).toBe("—");
    expect(formatPct("not-a-number")).toBe("—");
  });

  it("formats a fraction as a percentage with one decimal place", () => {
    expect(formatPct(0.4567)).toBe("45.7%");
  });
});

describe("el", () => {
  it("creates an element with text content and a class name", () => {
    const node = el("span", { className: "muted", text: "hello" });
    expect(node.tagName).toBe("SPAN");
    expect(node.className).toBe("muted");
    expect(node.textContent).toBe("hello");
  });

  it("appends children in order", () => {
    const child = el("li", { text: "item" });
    const node = el("ul", {}, [child]);
    expect(node.children).toHaveLength(1);
    expect(node.firstElementChild?.textContent).toBe("item");
  });
});
