import { announceStatus, el, navigateToDetail } from "../dom.ts";

// Hub page for the analysis tools. Each tool lives in its own hidden tab
// (same pattern as the game box-score tab) so the visible tab bar stays
// small; this page is the discoverable entry point.

interface AnalyticsTool {
  id: string;
  label: string;
  description: string;
}

export const ANALYTICS_TOOLS: AnalyticsTool[] = [
  {
    id: "betting",
    label: "Vegas vs Reality",
    description:
      "Which teams beat the betting market? Moneyline upsets, market-beaters, and bookmaker calibration since 2003.",
  },
  {
    id: "matchups",
    label: "Matchup Explorer",
    description:
      "Who guarded whom this season: tracking-based matchup minutes, points allowed per 100 possessions, and defender leaderboards.",
  },
  {
    id: "four-factors",
    label: "Four Factors",
    description:
      "Dean Oliver's why-teams-win decomposition: shooting, turnovers, rebounding, and free throws for every team-season since 2000, plus how the league itself has drifted.",
  },
];

export function renderAnalytics(container: HTMLElement): void {
  container.replaceChildren();
  announceStatus("Analytics tools");
  container.append(
    el("h2", { text: "Analytics" }),
    el("p", {
      className: "muted",
      text: "Interactive analysis tools built on the deeper corners of the warehouse.",
    }),
    el(
      "div",
      { className: "home-tiles" },
      ANALYTICS_TOOLS.map((tool) => {
        const tile = el("button", { type: "button", className: "home-tile home-tile-nav" }, [
          el("h3", { text: tool.label }),
          el("p", { className: "muted", text: tool.description }),
        ]);
        tile.addEventListener("click", () => navigateToDetail(tool.id));
        return tile;
      }),
    ),
  );
}

/** Standard header for a tool page: back-link to the hub plus title/blurb. */
export function analyticsToolHeader(
  container: HTMLElement,
  title: string,
  blurb: string,
): HTMLElement {
  const back = el("button", {
    type: "button",
    className: "cell-link",
    text: "← All analytics tools",
  });
  back.addEventListener("click", () => navigateToDetail("analytics"));
  const header = el("div", {}, [
    back,
    el("h2", { text: title }),
    el("p", { className: "muted", text: blurb }),
  ]);
  container.append(header);
  return header;
}
