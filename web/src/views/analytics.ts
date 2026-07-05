import { announceStatus, el, navigateToDetail, pageHeader } from "../dom.ts";

// Hub page for the analysis tools. Each tool lives in its own hidden tab
// (same pattern as the game box-score tab) so the visible tab bar stays
// small; this page is the discoverable entry point.

interface AnalyticsTool {
  id: string;
  label: string;
  kicker: string;
  description: string;
}

export const ANALYTICS_TOOLS: AnalyticsTool[] = [
  {
    id: "betting",
    label: "Vegas vs Reality",
    kicker: "Market",
    description:
      "Which teams beat the betting market? Moneyline upsets, market-beaters, and bookmaker calibration since 2003.",
  },
  {
    id: "matchups",
    label: "Matchup Explorer",
    kicker: "Tracking",
    description:
      "Who guarded whom this season: tracking-based matchup minutes, points allowed per 100 possessions, and defender leaderboards.",
  },
  {
    id: "clutch",
    label: "Clutch Performers",
    kicker: "Play-by-play",
    description:
      "Scoring in the last five minutes with the game within five points, computed straight from 18.7M play-by-play events back to 1996-97. Game pages also gain a score-margin Game Flow chart.",
  },
  {
    id: "four-factors",
    label: "Four Factors",
    kicker: "Team seasons",
    description:
      "Dean Oliver's why-teams-win decomposition: shooting, turnovers, rebounding, and free throws for every team-season since 1996-97, plus how the league itself has drifted.",
  },
  {
    id: "officials",
    label: "Officials",
    kicker: "Referees",
    description: "Which officials work the most games. Coverage is the 2025-26 season only.",
  },
  {
    id: "coaching",
    label: "Coaching Leaderboard",
    kicker: "Coaches",
    description: "Career regular-season wins across every team a coach has led.",
  },
  {
    id: "franchise-leaders",
    label: "Franchise Leaders",
    kicker: "Teams",
    description:
      "Career points, assists, rebounds, blocks, and steals leaders for any franchise, plus a sortable all-time roster board.",
  },
];

export function renderAnalytics(container: HTMLElement): void {
  container.replaceChildren();
  announceStatus("Analytics tools");
  container.append(
    pageHeader(
      "Analytics",
      "Open focused tools for betting markets, player matchups, clutch scoring, and four factors.",
    ),
    el(
      "div",
      { className: "home-tiles" },
      ANALYTICS_TOOLS.map((tool) => {
        const tile = el(
          "button",
          { type: "button", className: "home-tile home-tile-nav analytics-card" },
          [
            el("span", { className: "tile-kicker", text: tool.kicker }),
            el("h3", { text: tool.label }),
            el("p", { className: "muted", text: tool.description }),
          ],
        );
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
    className: "cell-link page-back",
    text: "← All analytics tools",
  });
  back.addEventListener("click", () => navigateToDetail("analytics"));
  const header = el("div", { className: "page-header analytics-tool-header" }, [
    back,
    el("h2", { className: "page-title", text: title }),
    el("p", { className: "page-description", text: blurb }),
  ]);
  container.append(header);
  return header;
}
