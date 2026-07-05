import { api } from "../api.ts";
import { announceStatus, el, errorEl, loadingEl, renderTable } from "../dom.ts";
import { analyticsToolHeader } from "./analytics.ts";

// Officials (referee leaderboard). fact_official_assignment only covers the
// 2025-26 season (a live/current-season feed) — a real, permanent coverage
// limit, so this is a flat leaderboard rather than a season picker.

export function renderOfficials(container: HTMLElement): void {
  container.replaceChildren();
  announceStatus("Officials");
  analyticsToolHeader(
    container,
    "Officials",
    "Games officiated this season, ranked by workload. Coverage is 2025-26 only — the warehouse's official-assignment data doesn't yet extend to prior seasons.",
  );

  const section = el("section", {}, [
    el("h3", { text: "Most games officiated (2025-26)" }),
    loadingEl(),
  ]);
  container.append(section);

  const replaceBody = (node: HTMLElement): void => {
    while (section.childNodes.length > 1) section.lastChild?.remove();
    section.append(node);
  };

  async function init(): Promise<void> {
    try {
      const rows = await api.getOfficialsLeaders(50);
      replaceBody(
        renderTable(
          [
            { key: "full_name", label: "Official" },
            { key: "games", label: "Games" },
          ],
          rows,
        ),
      );
      announceStatus("Officials leaderboard loaded.");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load.";
      replaceBody(errorEl(message));
    }
  }

  void init();
}
