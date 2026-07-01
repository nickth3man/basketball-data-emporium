import { api, type Row } from "../api.ts";
import {
  announceStatus,
  el,
  formatPct,
  formatValue,
  labeledSearch,
  renderDefList,
  renderTable,
} from "../dom.ts";

export function renderTeams(container: HTMLElement): void {
  const { wrapper: searchWrapper, input: searchBox } = labeledSearch(
    "Filter teams",
    "Filter teams…",
    "search-box",
    "teams-search",
  );
  const resultsList = el("ul", { className: "result-list" });
  const detail = el("div", { className: "detail" });

  container.append(el("div", { className: "search-panel" }, [searchWrapper, resultsList]), detail);

  let debounce: number | undefined;
  searchBox.addEventListener("input", () => {
    window.clearTimeout(debounce);
    debounce = window.setTimeout(() => void runSearch(searchBox.value.trim()), 200);
  });

  async function runSearch(query: string): Promise<void> {
    resultsList.replaceChildren();
    announceStatus("Loading teams…");
    try {
      const teams = await api.searchTeams(query);
      if (teams.length === 0) {
        resultsList.append(el("li", { className: "muted", text: "No teams found." }));
        announceStatus("No teams found.");
        return;
      }
      announceStatus(`${teams.length} team${teams.length === 1 ? "" : "s"} found.`);
      for (const t of teams) {
        const button = el("button", { type: "button", className: "result-row" }, [
          el("span", { text: String(t.team_name) }),
          el("span", { className: "muted", text: String(t.abbreviation) }),
        ]);
        button.addEventListener("click", () => void showTeam(String(t.team_id)));
        resultsList.append(el("li", {}, [button]));
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Search failed.";
      resultsList.append(el("li", { className: "muted", text: `Error: ${message}` }));
      announceStatus(`Team search failed: ${message}`);
    }
  }

  async function showTeam(id: string): Promise<void> {
    detail.replaceChildren(el("p", { className: "muted", text: "Loading…" }));
    announceStatus("Loading team profile…");
    try {
      const profile = await api.getTeam(id);
      detail.replaceChildren();
      if (!profile.bio) {
        detail.append(el("p", { text: "Team not found." }));
        announceStatus("Team not found.");
        return;
      }
      renderBio(detail, profile.bio, profile.currentStanding);
      if (profile.franchiseHistory.length > 1)
        renderFranchiseHistory(detail, profile.franchiseHistory);
      if (profile.seasons.length > 0) renderSeasons(detail, profile.seasons);
      if (profile.recentGames.length > 0) renderRecentGames(detail, profile.recentGames);
      announceStatus(`Loaded profile for ${String(profile.bio.nickname)}.`);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load team.";
      detail.replaceChildren(el("p", { className: "muted", text: `Error: ${message}` }));
      announceStatus(`Failed to load team: ${message}`);
    }
  }

  void runSearch("");
}

function renderBio(container: HTMLElement, bio: Row, standing: Row | null): void {
  container.append(
    el("h2", { text: String(bio.nickname) }),
    renderDefList([
      ["Abbreviation", bio.abbreviation],
      ["Conference", standing?.conference],
      ["Division", standing?.division],
      ["Arena", bio.arena],
      ["Founded", bio.year_founded],
      [
        "Latest record",
        standing
          ? `${formatValue(standing.wins)}-${formatValue(standing.losses)} (${formatValue(standing.season_year)} ${formatValue(standing.season_type)})`
          : "—",
      ],
    ]),
  );
}

function renderFranchiseHistory(container: HTMLElement, history: Row[]): void {
  container.append(
    el("h3", { text: "Franchise history" }),
    renderTable(
      [
        { key: "nickname", label: "Name" },
        { key: "city", label: "City" },
        { key: "abbreviation", label: "Abbr." },
        { key: "valid_from", label: "From" },
        { key: "valid_to", label: "To" },
      ],
      history,
    ),
  );
}

function renderSeasons(container: HTMLElement, seasons: Row[]): void {
  container.append(
    el("h3", { text: "Season by season" }),
    renderTable(
      [
        { key: "season_year", label: "Season" },
        { key: "season_type", label: "Type" },
        { key: "gp", label: "GP" },
        { key: "avg_pts", label: "PPG" },
        { key: "avg_reb", label: "RPG" },
        { key: "avg_ast", label: "APG" },
        { key: "fg_pct", label: "FG%", format: formatPct },
      ],
      seasons,
    ),
  );
}

function renderRecentGames(container: HTMLElement, games: Row[]): void {
  container.append(
    el("h3", { text: "Recent games" }),
    renderTable(
      [
        { key: "game_date", label: "Date", format: (v) => String(v).slice(0, 10) },
        { key: "location", label: "", headerLabel: "Home or away" },
        { key: "opponent", label: "Opponent" },
        { key: "team_pts", label: "PTS" },
        { key: "opp_pts", label: "Opp PTS" },
        { key: "result", label: "Result" },
      ],
      games,
    ),
  );
}
