/**
 * Unit tests for the player-hub tab/dataset catalog helpers in
 * `ui/src/features/player-hub/utils/catalog.ts`.
 */
import { describe, expect, it } from "vitest";

import type { DatasetCatalogEntry } from "@/features/player-hub/types";
import { datasetLabel, datasetScope } from "@/features/player-hub/utils/catalog";

/** Build a minimal but complete `DatasetCatalogEntry` for test fixtures. */
function makeEntry(id: string, label: string, scope: "player" | "season" = "player"): DatasetCatalogEntry {
  return {
    id,
    label,
    endpoint_name: id,
    scope,
    description: label,
    columns: [],
    default_visible_columns: [],
    supports_export: true,
    supports_include_inactive_games: false,
  };
}

describe("datasetLabel", () => {
  it("returns the catalog label when the dataset id is known", () => {
    const datasets = [makeEntry("career", "Career Totals"), makeEntry("splits", "Season Splits")];
    expect(datasetLabel(datasets, "career")).toBe("Career Totals");
    expect(datasetLabel(datasets, "splits")).toBe("Season Splits");
  });

  it("returns a fallback humanized id when the dataset id is unknown", () => {
    const datasets = [makeEntry("career", "Career Totals")];
    expect(datasetLabel(datasets, "adjusted-shooting")).toBe("adjusted shooting");
  });

  it("returns a fallback humanized id when the catalog is undefined", () => {
    expect(datasetLabel(undefined, "playoff-series")).toBe("playoff series");
  });
});

describe("datasetScope", () => {
  it("returns the catalog scope literal when the dataset id is known", () => {
    const datasets = [makeEntry("career", "Career", "player"), makeEntry("splits", "Splits", "season")];
    expect(datasetScope(datasets, "career")).toBe("player");
    expect(datasetScope(datasets, "splits")).toBe("season");
  });

  it("returns undefined when the dataset id is not in the catalog", () => {
    const datasets = [makeEntry("career", "Career", "player")];
    expect(datasetScope(datasets, "missing")).toBeUndefined();
  });

  it("returns undefined when the catalog is undefined", () => {
    expect(datasetScope(undefined, "anything")).toBeUndefined();
  });
});
