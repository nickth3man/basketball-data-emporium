import { api, type Row } from "../api.ts";
import { announceStatus, el, navigateToDetail, playerPhoto } from "../dom.ts";

function playerRow(p: Row): HTMLElement {
  const sub = [p.position, p.team_abbreviation].filter(Boolean).join(" · ");
  const button = el("button", { type: "button", className: "result-row" }, [
    playerPhoto(p.player_id, "player-photo-thumb", String(p.full_name)),
    el("div", { className: "result-row-text" }, [
      el("span", { text: String(p.full_name) }),
      el("span", { className: "muted", text: sub }),
    ]),
  ]);
  button.addEventListener("click", () => navigateToDetail("players", String(p.player_id)));
  return el("li", {}, [button]);
}

function teamRow(t: Row): HTMLElement {
  const button = el("button", { type: "button", className: "result-row" }, [
    el("span", { text: String(t.team_name) }),
    el("span", { className: "muted", text: String(t.abbreviation) }),
  ]);
  button.addEventListener("click", () => navigateToDetail("teams", String(t.team_id)));
  return el("li", {}, [button]);
}

export async function renderSearchResults(container: HTMLElement, query?: string): Promise<void> {
  const trimmed = (query ?? "").trim();
  container.replaceChildren();
  if (trimmed.length === 0) {
    container.append(el("p", { className: "muted", text: "Type a search above to find players or teams." }));
    return;
  }
  container.append(el("p", { className: "muted", text: "Loading…" }));
  announceStatus(`Searching for ${trimmed}…`);
  try {
    const [players, teams] = await Promise.all([api.searchPlayers(trimmed), api.searchTeams(trimmed)]);
    container.replaceChildren(el("h2", { text: `Search results for "${trimmed}"` }));
    if (players.length === 0 && teams.length === 0) {
      container.append(el("p", { className: "muted", text: `No players or teams found for "${trimmed}".` }));
      announceStatus(`No results for ${trimmed}.`);
      return;
    }
    if (players.length > 0) {
      container.append(
        el("section", {}, [
          el("h3", { text: `Players (${players.length})` }),
          el("ul", { className: "result-list" }, players.map(playerRow)),
        ]),
      );
    }
    if (teams.length > 0) {
      container.append(
        el("section", {}, [
          el("h3", { text: `Teams (${teams.length})` }),
          el("ul", { className: "result-list" }, teams.map(teamRow)),
        ]),
      );
    }
    announceStatus(`${players.length + teams.length} results for ${trimmed}.`);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Search failed.";
    container.replaceChildren(el("p", { className: "muted", text: `Error: ${message}` }));
    announceStatus(`Search failed: ${message}`);
  }
}
