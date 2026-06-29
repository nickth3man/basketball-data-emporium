import type { TeamDatasetCatalogEntry } from "@/features/team-hub/types";

export function teamDatasetLabel(id: string): string {
  return id.replaceAll("-", " ");
}

export function lookupTeamDataset(
  datasets: TeamDatasetCatalogEntry[] | undefined,
  id: string,
): TeamDatasetCatalogEntry | undefined {
  return datasets?.find((entry) => entry.id === id);
}
