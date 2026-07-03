import { api, type Row } from "../api.ts";
import {
  announceStatus,
  el,
  formatValue,
  labeledSelect,
  navigateToDetail,
  renderTable,
} from "../dom.ts";
import { analyticsToolHeader } from "./analytics.ts";

// Four Factors dashboard — Dean Oliver's "why teams win" decomposition
// (shooting, turnovers, rebounding, free throws) per team-season with league
// ranks, plus how the league itself has drifted since 2000-01.

function pctCell(value: unknown, rank: unknown): string {
  if (value == null) return "—";
  const pct = (Number(value) * 100).toFixed(1);
  return rank == null ? `${pct}%` : `${pct}% (${formatValue(rank)})`;
}

function teamCell(value: unknown, row: Record<string, unknown>): Node | string {
  const label = formatValue(value);
  const teamId = Number(row.team_id);
  if (!Number.isFinite(teamId) || label === "—") return label;
  const button = el("button", {
    type: "button",
    className: "cell-link",
    text: label,
    "aria-label": `${formatValue(row.team_name)} team profile`,
  });
  button.addEventListener("click", () => navigateToDetail("teams", String(teamId)));
  return button;
}

export function renderFourFactors(container: HTMLElement): void {
  container.replaceChildren();
  announceStatus("Four Factors dashboard");
  analyticsToolHeader(
    container,
    "Four Factors",
    "Dean Oliver's four factors of winning — effective shooting, turnovers, offensive rebounding, and getting to the line — for every team-season since 2000-01, with league ranks on both ends of the floor.",
  );

  const { wrapper: seasonPicker, select: seasonSelect } = labeledSelect("Season", []);
  container.append(el("div", { className: "controls" }, [seasonPicker]));

  const teamsSection = el("section", {}, [
    el("h3", { text: "Team profiles" }),
    el("p", {
      className: "table-note",
      text: "Per-game averages; (n) is the league rank for that factor — offense ranks reward high eFG%/ORB%/FT rate and low TOV%, defense ranks reward the reverse. Wins are shown where the game table has results.",
    }),
    el("p", { className: "muted", text: "Loading…" }),
  ]);
  const leagueSection = el("section", {}, [
    el("h3", { text: "How the league has changed" }),
    el("p", {
      className: "table-note",
      text: "League-wide averages by season — watch eFG% climb and offensive rebounding fade as the three-point era reshapes the game.",
    }),
    el("p", { className: "muted", text: "Loading…" }),
  ]);
  container.append(teamsSection, leagueSection);

  const replaceBody = (section: HTMLElement, node: HTMLElement): void => {
    while (section.childNodes.length > 2) section.lastChild?.remove();
    section.append(node);
  };
  const errorInto = (section: HTMLElement, err: unknown): void => {
    const message = err instanceof Error ? err.message : "Failed to load.";
    replaceBody(section, el("p", { className: "muted", text: `Error: ${message}` }));
  };

  async function loadTeams(season: string): Promise<void> {
    try {
      const rows = await api.getFourFactorsTeams(season);
      replaceBody(
        teamsSection,
        renderTable(
          [
            { key: "team_abbreviation", label: "Team", render: teamCell },
            { key: "gp", label: "G" },
            { key: "wins", label: "W" },
            {
              key: "efg_pct",
              label: "eFG%",
              render: (v, row) => pctCell(v, row.efg_rank),
            },
            { key: "tov_pct", label: "TOV%", render: (v, row) => pctCell(v, row.tov_rank) },
            { key: "oreb_pct", label: "ORB%", render: (v, row) => pctCell(v, row.oreb_rank) },
            { key: "ft_rate", label: "FT rate", render: (v, row) => pctCell(v, row.ft_rate_rank) },
            {
              key: "opp_efg_pct",
              label: "eFG%",
              render: (v, row) => pctCell(v, row.opp_efg_rank),
            },
            {
              key: "opp_tov_pct",
              label: "TOV%",
              render: (v, row) => pctCell(v, row.opp_tov_rank),
            },
            {
              key: "opp_oreb_pct",
              label: "ORB%",
              render: (v, row) => pctCell(v, row.opp_oreb_rank),
            },
            {
              key: "opp_ft_rate",
              label: "FT rate",
              render: (v, row) => pctCell(v, row.opp_ft_rate_rank),
            },
          ],
          rows,
          [
            { label: "", span: 3 },
            { label: "Offense", span: 4 },
            { label: "Defense (opponent)", span: 4 },
          ],
        ),
      );
      announceStatus(`Four factors loaded for ${season}.`);
    } catch (err) {
      errorInto(teamsSection, err);
    }
  }

  async function loadLeague(): Promise<void> {
    try {
      const rows: Row[] = await api.getFourFactorsLeague();
      replaceBody(
        leagueSection,
        renderTable(
          [
            { key: "season_year", label: "Season" },
            { key: "games", label: "Games" },
            { key: "efg_pct", label: "eFG%", render: (v) => pctCell(v, null) },
            { key: "tov_pct", label: "TOV%", render: (v) => pctCell(v, null) },
            { key: "oreb_pct", label: "ORB%", render: (v) => pctCell(v, null) },
            { key: "ft_rate", label: "FT rate", render: (v) => pctCell(v, null) },
          ],
          rows,
        ),
      );
    } catch (err) {
      errorInto(leagueSection, err);
    }
  }

  async function init(): Promise<void> {
    try {
      const seasons = await api.listFourFactorsSeasons();
      for (const season of seasons) {
        const option = document.createElement("option");
        option.value = season;
        option.textContent = season;
        seasonSelect.append(option);
      }
      seasonSelect.addEventListener("change", () => void loadTeams(seasonSelect.value));
      const initial = seasons[0];
      if (initial) await Promise.all([loadTeams(initial), loadLeague()]);
    } catch (err) {
      errorInto(teamsSection, err);
    }
  }

  void init();
}
