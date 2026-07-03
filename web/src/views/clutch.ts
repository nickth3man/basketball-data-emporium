import { api } from "../api.ts";
import { announceStatus, el, labeledSelect, playerCell, renderTable } from "../dom.ts";
import { analyticsToolHeader } from "./analytics.ts";

// Clutch Performers — computed live from 18.7M play-by-play events because
// the warehouse's prebuilt clutch tables are empty. Definition matches
// NBA.com: last 5 minutes of the 4th quarter or overtime with the score
// within 5 points before the scoring event.

export function renderClutch(container: HTMLElement): void {
  container.replaceChildren();
  announceStatus("Clutch Performers");
  analyticsToolHeader(
    container,
    "Clutch Performers",
    "Who scores when it matters: points in the last five minutes of the fourth quarter or overtime with the score within five, derived play-by-play since 1996-97. Regular season only.",
  );

  const { wrapper: seasonPicker, select: seasonSelect } = labeledSelect("Season", []);
  container.append(el("div", { className: "controls" }, [seasonPicker]));

  const section = el("section", {}, [
    el("h3", { text: "Clutch scoring leaders" }),
    el("p", {
      className: "table-note",
      text: "Points are credited from the score change on each play-by-play scoring event, so free throws, and-ones, and garbage-time-free crunch minutes all count exactly once. G = games with at least one clutch point.",
    }),
    el("p", { className: "muted", text: "Loading…" }),
  ]);
  container.append(section);

  const replaceBody = (node: HTMLElement): void => {
    while (section.childNodes.length > 2) section.lastChild?.remove();
    section.append(node);
  };

  async function loadSeason(season: string): Promise<void> {
    replaceBody(el("p", { className: "muted", text: "Crunching crunch time…" }));
    try {
      const rows = await api.getClutchLeaders(season, 30);
      replaceBody(
        renderTable(
          [
            { key: "full_name", label: "Player", render: playerCell },
            { key: "clutch_pts", label: "Clutch PTS" },
            { key: "games", label: "G" },
            { key: "pts_per_game", label: "Per game" },
          ],
          rows,
        ),
      );
      announceStatus(`Clutch leaders loaded for ${season}.`);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load.";
      replaceBody(el("p", { className: "muted", text: `Error: ${message}` }));
    }
  }

  async function init(): Promise<void> {
    try {
      const seasons = await api.listClutchSeasons();
      for (const season of seasons) {
        const option = document.createElement("option");
        option.value = season;
        option.textContent = season;
        seasonSelect.append(option);
      }
      seasonSelect.addEventListener("change", () => void loadSeason(seasonSelect.value));
      const initial = seasons[0];
      if (initial) await loadSeason(initial);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load seasons.";
      replaceBody(el("p", { className: "muted", text: `Error: ${message}` }));
    }
  }

  void init();
}
