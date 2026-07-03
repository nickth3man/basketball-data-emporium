import { api } from "../api.ts";
import {
  announceStatus,
  el,
  errorEl,
  formatPct,
  labeledSelect,
  loadingEl,
  pageHeader,
  renderTable,
} from "../dom.ts";

const STANDINGS_COLUMNS = [
  { key: "conf_rank", label: "#" },
  { key: "team_name", label: "Team" },
  { key: "wins", label: "W" },
  { key: "losses", label: "L" },
  { key: "win_pct", label: "PCT", format: formatPct },
  { key: "games_back", label: "GB" },
  { key: "home_record", label: "Home" },
  { key: "road_record", label: "Road" },
];

export async function renderStandings(container: HTMLElement): Promise<void> {
  const header = () =>
    pageHeader(
      "Standings",
      "Compare conference records, winning percentages, and home-road splits by season.",
    );

  container.append(header(), loadingEl());
  announceStatus("Loading standings…");
  try {
    const seasons = await api.standingsSeasons();
    container.replaceChildren(header());

    const { wrapper: seasonWrapper, select: seasonSelect } = labeledSelect(
      "Season",
      seasons.map((s) => ({ value: s, label: s })),
      "standings-season",
    );
    const { wrapper: typeWrapper, select: typeSelect } = labeledSelect(
      "Season type",
      [
        { value: "Regular", label: "Regular Season" },
        { value: "Playoffs", label: "Playoffs" },
      ],
      "standings-type",
    );
    const resultDiv = el("div", { className: "standings-result" });

    container.append(el("div", { className: "controls" }, [seasonWrapper, typeWrapper]), resultDiv);

    async function load(): Promise<void> {
      resultDiv.replaceChildren(loadingEl());
      announceStatus("Loading standings…");
      try {
        const rows = await api.standings(seasonSelect.value, typeSelect.value);
        resultDiv.replaceChildren();
        if (rows.length === 0) {
          resultDiv.append(
            el("p", { className: "empty-state", text: "No standings for this season." }),
          );
          announceStatus("No standings for this season.");
          return;
        }
        const east = rows.filter((r) => r.conference === "East");
        const west = rows.filter((r) => r.conference === "West");
        resultDiv.append(
          el("h3", { text: "Eastern Conference" }),
          renderTable(STANDINGS_COLUMNS, east),
          el("h3", { text: "Western Conference" }),
          renderTable(STANDINGS_COLUMNS, west),
        );
        announceStatus(`Loaded ${seasonSelect.value} ${typeSelect.value.toLowerCase()} standings.`);
      } catch (err) {
        const message = err instanceof Error ? err.message : "Failed to load standings.";
        resultDiv.replaceChildren(errorEl(message));
        announceStatus(`Failed to load standings: ${message}`);
      }
    }

    seasonSelect.addEventListener("change", () => void load());
    typeSelect.addEventListener("change", () => void load());
    await load();
  } catch (err) {
    const message = err instanceof Error ? err.message : "Failed to load seasons.";
    container.replaceChildren(header(), errorEl(message));
    announceStatus(`Failed to load standings seasons: ${message}`);
  }
}
