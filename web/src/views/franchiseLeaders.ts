import { api, type FranchiseLeaderRow } from "../api.ts";
import {
  announceStatus,
  el,
  errorEl,
  formatValue,
  labeledSearch,
  loadingEl,
  renderDefList,
  renderTable,
} from "../dom.ts";
import { analyticsToolHeader } from "./analytics.ts";

// Franchise Leaders — per-team career leaderboards (mart_franchise_leaders
// for the fixed pts/ast/reb/blk/stl leaders, plus a sortable top-players
// board backed by src_fact_franchise_players). Search a team to load both.

const TOP_PLAYER_COLUMNS: Parameters<typeof renderTable>[0] = [
  { key: "full_name", label: "Player" },
  { key: "gp", label: "GP" },
  { key: "pts", label: "PTS" },
  { key: "ast", label: "AST" },
  { key: "reb", label: "REB" },
  { key: "stl", label: "STL" },
  { key: "blk", label: "BLK" },
];

function leaderRow(row: FranchiseLeaderRow, stat: "pts" | "ast" | "reb" | "blk" | "stl"): string {
  const name = row[`${stat}_leader_name`];
  const value = row[stat];
  if (name == null || value == null) return "—";
  return `${formatValue(name)} (${formatValue(value)})`;
}

export function renderFranchiseLeaders(container: HTMLElement, detailId?: string): void {
  container.replaceChildren();
  announceStatus("Franchise Leaders");
  analyticsToolHeader(
    container,
    "Franchise Leaders",
    "Career leaders in points, assists, rebounds, blocks, and steals for a franchise, plus a sortable all-time roster board.",
  );

  const { wrapper: searchWrapper, input: searchInput } = labeledSearch(
    "Find a team",
    "Search a team (e.g. Lakers)…",
  );
  const resultsList = el("ul", { className: "result-list" });
  const teamSection = el("div", {});
  container.append(el("div", { className: "controls" }, [searchWrapper]), resultsList, teamSection);

  let searchTimer: ReturnType<typeof setTimeout> | undefined;
  let searchAbort: AbortController | undefined;
  searchInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    const query = searchInput.value.trim();
    if (query.length < 2) {
      resultsList.replaceChildren();
      return;
    }
    searchTimer = setTimeout(() => void runSearch(query), 200);
  });

  async function runSearch(query: string): Promise<void> {
    searchAbort?.abort();
    searchAbort = new AbortController();
    try {
      const teams = await api.searchTeams(query, searchAbort.signal);
      resultsList.replaceChildren(
        ...teams.slice(0, 8).map((t) => {
          const li = el("li", {});
          const button = el("button", {
            type: "button",
            className: "cell-link",
            text: `${formatValue(t.city)} ${formatValue(t.team_name)}`,
          });
          button.addEventListener("click", () => {
            resultsList.replaceChildren();
            searchInput.value = `${formatValue(t.city)} ${formatValue(t.team_name)}`;
            void showTeam(
              formatValue(t.team_id),
              `${formatValue(t.city)} ${formatValue(t.team_name)}`,
            );
          });
          li.append(button);
          return li;
        }),
      );
    } catch {
      // Aborted or failed search; leave the list as-is.
    }
  }

  async function showTeam(teamId: string, teamName: string): Promise<void> {
    teamSection.replaceChildren(loadingEl(`Loading ${teamName} leaders…`));
    const [leaders, topPlayers] = await Promise.allSettled([
      api.getFranchiseLeaders(teamId),
      api.getFranchiseTopPlayers(teamId, "gp", 25),
    ]);
    teamSection.replaceChildren();

    const leaderSection = el("section", {}, [el("h3", { text: `${teamName} career leaders` })]);
    if (leaders.status === "fulfilled" && leaders.value) {
      const l = leaders.value;
      leaderSection.append(
        renderDefList([
          ["Points", leaderRow(l, "pts")],
          ["Assists", leaderRow(l, "ast")],
          ["Rebounds", leaderRow(l, "reb")],
          ["Blocks", leaderRow(l, "blk")],
          ["Steals", leaderRow(l, "stl")],
        ]),
      );
    } else {
      leaderSection.append(
        el("p", { className: "empty-state", text: "No franchise leaders on file." }),
      );
    }

    const topSection = el("section", {}, [el("h3", { text: "All-time roster (by games played)" })]);
    if (topPlayers.status === "fulfilled") {
      topSection.append(
        renderTable(TOP_PLAYER_COLUMNS, topPlayers.value as unknown as Record<string, unknown>[]),
      );
    } else {
      topSection.append(errorEl("Failed to load roster."));
    }

    teamSection.append(leaderSection, topSection);
    announceStatus(`Franchise leaders loaded for ${teamName}.`);
  }

  if (detailId) void showTeam(detailId, "this team");
}
