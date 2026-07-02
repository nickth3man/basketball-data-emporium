import { api, type Row } from "./api.ts";
import { announceStatus, el, navigateToDetail, playerPhoto } from "./dom.ts";

const INPUT_ID = "header-search-input";
const DROPDOWN_LIMIT = 6;

/** Mounts the persistent, global header search box into `container`. Lives
 *  outside any tab's content (see index.html's #header-search), searching
 *  players and teams together. Typing shows a dropdown of clickable results;
 *  Enter navigates to the full Search Results page via the same nba:navigate
 *  channel navigateToDetail() already uses, so no new event plumbing is
 *  needed in main.ts. */
export function mountHeaderSearch(container: HTMLElement): void {
  const input = el("input", {
    type: "search",
    placeholder: "Search players & teams…",
    className: "search-box",
    id: INPUT_ID,
    "aria-label": "Search players and teams",
    autocomplete: "off",
  }) as HTMLInputElement;

  const dropdown = el("div", { className: "header-search-dropdown", hidden: "" });
  const wrapper = el("div", { className: "header-search" }, [input, dropdown]);
  container.append(wrapper);

  let debounce: number | undefined;
  let controller: AbortController | null = null;

  function closeDropdown(): void {
    dropdown.hidden = true;
    dropdown.replaceChildren();
  }

  function playerRow(p: Row): HTMLElement {
    const sub = [p.position, p.team_abbreviation].filter(Boolean).join(" · ");
    const button = el("button", { type: "button", className: "result-row" }, [
      playerPhoto(p.player_id, "player-photo-thumb", String(p.full_name)),
      el("div", { className: "result-row-text" }, [
        el("span", { text: String(p.full_name) }),
        el("span", { className: "muted", text: sub }),
      ]),
    ]);
    button.addEventListener("click", () => {
      navigateToDetail("players", String(p.player_id));
      closeDropdown();
      input.value = "";
    });
    return button;
  }

  function teamRow(t: Row): HTMLElement {
    const button = el("button", { type: "button", className: "result-row" }, [
      el("span", { text: String(t.team_name) }),
      el("span", { className: "muted", text: String(t.abbreviation) }),
    ]);
    button.addEventListener("click", () => {
      navigateToDetail("teams", String(t.team_id));
      closeDropdown();
      input.value = "";
    });
    return button;
  }

  async function runSearch(query: string): Promise<void> {
    controller?.abort();
    if (query.length === 0) {
      closeDropdown();
      return;
    }
    const abort = new AbortController();
    controller = abort;
    try {
      const [players, teams] = await Promise.all([
        api.searchPlayers(query, abort.signal),
        api.searchTeams(query, abort.signal),
      ]);
      if (abort.signal.aborted) return;
      dropdown.replaceChildren();
      if (players.length === 0 && teams.length === 0) {
        dropdown.append(el("p", { className: "muted", text: "No players or teams found." }));
      } else {
        if (players.length > 0) {
          dropdown.append(
            el("p", { className: "table-note", text: "Players" }),
            ...players.slice(0, DROPDOWN_LIMIT).map(playerRow),
          );
        }
        if (teams.length > 0) {
          dropdown.append(
            el("p", { className: "table-note", text: "Teams" }),
            ...teams.slice(0, DROPDOWN_LIMIT).map(teamRow),
          );
        }
      }
      dropdown.hidden = false;
      announceStatus(`${players.length + teams.length} results for ${query}.`);
    } catch (err) {
      if (abort.signal.aborted) return;
      const message = err instanceof Error ? err.message : "Search failed.";
      dropdown.replaceChildren(el("p", { className: "muted", text: `Error: ${message}` }));
      dropdown.hidden = false;
    }
  }

  input.addEventListener("input", () => {
    window.clearTimeout(debounce);
    debounce = window.setTimeout(() => void runSearch(input.value.trim()), 200);
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const query = input.value.trim();
      if (query.length === 0) return;
      e.preventDefault();
      navigateToDetail("search", query);
      closeDropdown();
    } else if (e.key === "Escape") {
      closeDropdown();
    }
  });

  document.addEventListener("click", (e) => {
    if (!wrapper.contains(e.target as Node)) closeDropdown();
  });

  // Close on any navigation, including the ones this module itself triggers.
  window.addEventListener("nba:navigate", () => closeDropdown());
}

/** Focuses the header search input; used by Home's search CTA tile. */
export function focusHeaderSearch(): void {
  document.getElementById(INPUT_ID)?.focus();
}
