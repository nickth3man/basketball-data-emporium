import type { DatasetCatalogEntry } from "@/features/player-hub/types";

export function datasetLabel(datasets: DatasetCatalogEntry[] | undefined, dataset: string): string {
  return datasets?.find((entry) => entry.id === dataset)?.label ?? dataset.replaceAll("-", " ");
}

export function datasetScope(datasets: DatasetCatalogEntry[] | undefined, dataset: string): "player" | "season" | undefined {
  return datasets?.find((entry) => entry.id === dataset)?.scope;
}
