import { api, type Row } from "../api.ts";
import {
  announceStatus,
  boxScoreCell,
  el,
  formatValue,
  labeledSelect,
  renderTable,
  teamCell,
} from "../dom.ts";
import { analyticsToolHeader } from "./analytics.ts";

// Vegas vs Reality — moneyline betting explorer. The warehouse's betting
// table only has trustworthy moneylines (its spread/total columns are
// duplicated moneyline values / NULL), and results join against the `game`
// fact table, so coverage runs 2003-04 through the last season `game`
// carries. See getBettingMarketBeaters in web/server/queries.ts.

function record(w: unknown, l: unknown): string {
  return `${formatValue(w)}–${formatValue(l)}`;
}

export function renderBetting(container: HTMLElement): void {
  container.replaceChildren();
  announceStatus("Vegas vs Reality");
  analyticsToolHeader(
    container,
    "Vegas vs Reality",
    "Moneyline odds vs what actually happened: teams that beat the market, the biggest upsets, and how well the bookmakers were calibrated. Regular-season and playoff games from 2003-04 onward.",
  );

  const { wrapper: seasonPicker, select: seasonSelect } = labeledSelect("Season", [
    { value: "", label: "All seasons" },
  ]);
  container.append(el("div", { className: "controls" }, [seasonPicker]));

  const beatersSection = el("section", {}, [
    el("h3", { text: "Market beaters" }),
    el("p", {
      className: "table-note",
      text: "Expected wins = sum of each game's implied win probability (overround removed). Positive “vs market” means the team won more than the odds predicted. Regular season only.",
    }),
    el("p", { className: "muted", text: "Loading…" }),
  ]);
  const upsetsSection = el("section", {}, [
    el("h3", { text: "Biggest upsets" }),
    el("p", {
      className: "table-note",
      text: "Winners ranked by their pre-game moneyline (decimal odds). Implied % is the market's win probability for the eventual winner.",
    }),
    el("p", { className: "muted", text: "Loading…" }),
  ]);
  const calibrationSection = el("section", {}, [
    el("h3", { text: "Was the market right?" }),
    el("p", {
      className: "table-note",
      text: "Season-by-season: how often home teams and favorites actually won vs the market's implied probability. Close columns = well-calibrated bookmakers.",
    }),
    el("p", { className: "muted", text: "Loading…" }),
  ]);
  container.append(beatersSection, upsetsSection, calibrationSection);

  const errorInto = (section: HTMLElement, err: unknown): void => {
    const message = err instanceof Error ? err.message : "Failed to load.";
    section.append(el("p", { className: "muted", text: `Error: ${message}` }));
  };
  const replaceBody = (section: HTMLElement, ...nodes: HTMLElement[]): void => {
    // Keep the heading + note (first two children), replace the rest.
    while (section.childNodes.length > 2) section.lastChild?.remove();
    section.append(...nodes);
  };

  async function loadTables(season: string): Promise<void> {
    const seasonArg = season === "" ? undefined : season;
    const [beaters, upsets] = await Promise.allSettled([
      api.getBettingMarketBeaters(seasonArg),
      api.getBettingUpsets(seasonArg, 25),
    ]);
    if (beaters.status === "fulfilled") {
      replaceBody(
        beatersSection,
        renderTable(
          [
            { key: "team_abbreviation", label: "Team", render: teamCell },
            { key: "gp", label: "G" },
            { key: "wins", label: "W" },
            { key: "expected_wins", label: "Market W" },
            { key: "wins_vs_market", label: "vs Market" },
            {
              key: "fav_wins",
              label: "As favorite",
              render: (_v, row) => record(row.fav_wins, row.fav_losses),
            },
            {
              key: "dog_wins",
              label: "As underdog",
              render: (_v, row) => record(row.dog_wins, row.dog_losses),
            },
          ],
          beaters.value,
        ),
      );
    } else {
      errorInto(beatersSection, beaters.reason);
    }
    if (upsets.status === "fulfilled") {
      replaceBody(
        upsetsSection,
        renderTable(
          [
            { key: "game_id", label: "", headerLabel: "Box score", render: boxScoreCell },
            { key: "game_date", label: "Date" },
            { key: "season_year", label: "Season" },
            { key: "season_type", label: "Type" },
            { key: "winner", label: "Winner" },
            {
              key: "winner_pts",
              label: "Score",
              render: (_v, row) => `${formatValue(row.winner_pts)}–${formatValue(row.loser_pts)}`,
            },
            { key: "loser", label: "Over" },
            { key: "winner_side", label: "Site" },
            {
              key: "winner_odds",
              label: "Odds",
              format: (v) => (v == null ? "—" : Number(v).toFixed(2)),
            },
            {
              key: "implied_win_pct",
              label: "Implied %",
              format: (v) => (v == null ? "—" : `${formatValue(v)}%`),
            },
          ],
          upsets.value,
        ),
      );
    } else {
      errorInto(upsetsSection, upsets.reason);
    }
    announceStatus("Betting tables loaded.");
  }

  async function loadCalibration(): Promise<void> {
    try {
      const rows: Row[] = await api.getBettingCalibration();
      replaceBody(
        calibrationSection,
        renderTable(
          [
            { key: "season_year", label: "Season" },
            { key: "games", label: "Games" },
            { key: "home_win_pct", label: "Home won", format: (v) => `${formatValue(v)}%` },
            {
              key: "implied_home_pct",
              label: "Market said",
              format: (v) => `${formatValue(v)}%`,
            },
            { key: "favorite_win_pct", label: "Favorite won", format: (v) => `${formatValue(v)}%` },
            {
              key: "favorite_implied_pct",
              label: "Market said",
              format: (v) => `${formatValue(v)}%`,
            },
          ],
          rows,
        ),
      );
    } catch (err) {
      errorInto(calibrationSection, err);
    }
  }

  async function init(): Promise<void> {
    try {
      const seasons = await api.listBettingSeasons();
      for (const season of seasons) {
        const option = document.createElement("option");
        option.value = season;
        option.textContent = season;
        seasonSelect.append(option);
      }
    } catch {
      // Season list failing shouldn't block the all-seasons default view.
    }
    seasonSelect.addEventListener("change", () => void loadTables(seasonSelect.value));
    await Promise.all([loadTables(""), loadCalibration()]);
  }

  void init();
}
