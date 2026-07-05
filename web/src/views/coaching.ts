import { api } from "../api.ts";
import { announceStatus, el, errorEl, loadingEl, renderTable } from "../dom.ts";
import { analyticsToolHeader } from "./analytics.ts";

// Coaching Leaderboard: career wins/win-pct across every team-season a coach
// has worked, from fact_coach_season. This is additive to (not a replacement
// for) the per-team coach-history section on a Team profile page.

export function renderCoaching(container: HTMLElement): void {
  container.replaceChildren();
  announceStatus("Coaching Leaderboard");
  analyticsToolHeader(
    container,
    "Coaching Leaderboard",
    "Career regular-season wins across every team a coach has led, aggregated from team-by-season coaching records.",
  );

  const section = el("section", {}, [el("h3", { text: "Most career wins" }), loadingEl()]);
  container.append(section);

  const replaceBody = (node: HTMLElement): void => {
    while (section.childNodes.length > 1) section.lastChild?.remove();
    section.append(node);
  };

  async function init(): Promise<void> {
    try {
      const rows = await api.getCoachingLeaders(50);
      replaceBody(
        renderTable(
          [
            { key: "coach_name", label: "Coach" },
            { key: "teams", label: "Teams" },
            { key: "seasons", label: "Seasons" },
            { key: "wins", label: "W" },
            { key: "losses", label: "L" },
            { key: "win_pct", label: "Win %" },
          ],
          rows,
        ),
      );
      announceStatus("Coaching leaderboard loaded.");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load.";
      replaceBody(errorEl(message));
    }
  }

  void init();
}
