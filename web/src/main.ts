import { announceStatus, el } from "./dom.ts";
import { renderHome } from "./views/home.ts";
import { renderPlayers } from "./views/players.ts";
import { renderTeams } from "./views/teams.ts";
import { renderStandings } from "./views/standings.ts";
import { renderLeaders } from "./views/leaders.ts";
import { renderDraftAwards } from "./views/draftAwards.ts";
import { renderSearchResults } from "./views/searchResults.ts";
import { mountHeaderSearch } from "./headerSearch.ts";

interface Tab {
  id: string;
  label: string;
  render: (container: HTMLElement, detailId?: string) => void | Promise<void>;
  /** Excluded from the visible tab bar and from arrow-key cycling — reached
   *  only via navigateToDetail/nba:navigate (e.g. the header search's Enter
   *  key, which jumps straight to "search" without it ever being a nav
   *  button). */
  hidden?: boolean;
}

const TABS: Tab[] = [
  { id: "home", label: "Home", render: renderHome },
  { id: "players", label: "Players", render: renderPlayers },
  { id: "teams", label: "Teams", render: renderTeams },
  { id: "standings", label: "Standings", render: renderStandings },
  { id: "leaders", label: "League Leaders", render: renderLeaders },
  { id: "draft-awards", label: "Draft & Awards", render: renderDraftAwards },
  { id: "search", label: "Search Results", render: renderSearchResults, hidden: true },
];

const VISIBLE_TABS = TABS.filter((t) => !t.hidden);

const tabsEl = document.querySelector<HTMLElement>("#tabs")!;
const viewEl = document.querySelector<HTMLElement>("#view")!;

viewEl.id = "view-panel";
viewEl.setAttribute("role", "tabpanel");
tabsEl.setAttribute("role", "tablist");
tabsEl.setAttribute("aria-label", "Primary");

const tabButtons: HTMLButtonElement[] = [];

function activate(tabId: string, focusTab = false, detailId?: string): void {
  const tab = TABS.find((t) => t.id === tabId) ?? TABS[0];
  for (const btn of tabButtons) {
    const isActive = btn.dataset.tab === tab.id;
    btn.classList.toggle("active", isActive);
    btn.setAttribute("aria-selected", String(isActive));
    btn.tabIndex = isActive ? 0 : -1;
    if (isActive) {
      viewEl.setAttribute("aria-labelledby", btn.id);
    }
  }
  viewEl.replaceChildren();
  announceStatus(`${tab.label} tab selected`);
  void tab.render(viewEl, detailId);
  if (focusTab) {
    tabButtons.find((b) => b.dataset.tab === tab.id)?.focus();
  }
}

window.addEventListener("nba:navigate", (event) => {
  const detail = (event as CustomEvent<{ tab: string; id?: string }>).detail;
  activate(detail.tab, false, detail.id);
});

function handleTabKeydown(e: KeyboardEvent, currentId: string): void {
  const idx = VISIBLE_TABS.findIndex((t) => t.id === currentId);
  let nextIdx: number;
  if (e.key === "ArrowRight" || e.key === "ArrowDown") {
    e.preventDefault();
    nextIdx = (idx + 1) % VISIBLE_TABS.length;
  } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
    e.preventDefault();
    nextIdx = (idx - 1 + VISIBLE_TABS.length) % VISIBLE_TABS.length;
  } else if (e.key === "Home") {
    e.preventDefault();
    nextIdx = 0;
  } else if (e.key === "End") {
    e.preventDefault();
    nextIdx = VISIBLE_TABS.length - 1;
  } else {
    return;
  }
  activate(VISIBLE_TABS[nextIdx].id, true);
}

for (const tab of VISIBLE_TABS) {
  const btn = el("button", {
    type: "button",
    className: "tab",
    text: tab.label,
    role: "tab",
    id: `tab-${tab.id}`,
    "aria-controls": "view-panel",
    "aria-selected": "false",
    tabindex: "-1",
  }) as HTMLButtonElement;
  btn.dataset.tab = tab.id;
  btn.addEventListener("click", () => activate(tab.id));
  btn.addEventListener("keydown", (e) => handleTabKeydown(e, tab.id));
  tabButtons.push(btn);
  tabsEl.append(btn);
}

mountHeaderSearch(document.querySelector<HTMLElement>("#header-search")!);

activate(VISIBLE_TABS[0].id);
