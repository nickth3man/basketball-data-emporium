import { announceStatus, el } from "./dom.ts";
import { renderPlayers } from "./views/players.ts";
import { renderTeams } from "./views/teams.ts";
import { renderStandings } from "./views/standings.ts";
import { renderDraftAwards } from "./views/draftAwards.ts";

interface Tab {
  id: string;
  label: string;
  render: (container: HTMLElement) => void | Promise<void>;
}

const TABS: Tab[] = [
  { id: "players", label: "Players", render: renderPlayers },
  { id: "teams", label: "Teams", render: renderTeams },
  { id: "standings", label: "Standings", render: renderStandings },
  { id: "draft-awards", label: "Draft & Awards", render: renderDraftAwards },
];

const tabsEl = document.querySelector<HTMLElement>("#tabs")!;
const viewEl = document.querySelector<HTMLElement>("#view")!;

viewEl.id = "view-panel";
viewEl.setAttribute("role", "tabpanel");
tabsEl.setAttribute("role", "tablist");
tabsEl.setAttribute("aria-label", "Primary");

const tabButtons: HTMLButtonElement[] = [];

function activate(tabId: string, focusTab = false): void {
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
  void tab.render(viewEl);
  if (focusTab) {
    tabButtons.find((b) => b.dataset.tab === tab.id)?.focus();
  }
}

function handleTabKeydown(e: KeyboardEvent, currentId: string): void {
  const idx = TABS.findIndex((t) => t.id === currentId);
  let nextIdx: number;
  if (e.key === "ArrowRight" || e.key === "ArrowDown") {
    e.preventDefault();
    nextIdx = (idx + 1) % TABS.length;
  } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
    e.preventDefault();
    nextIdx = (idx - 1 + TABS.length) % TABS.length;
  } else if (e.key === "Home") {
    e.preventDefault();
    nextIdx = 0;
  } else if (e.key === "End") {
    e.preventDefault();
    nextIdx = TABS.length - 1;
  } else {
    return;
  }
  activate(TABS[nextIdx].id, true);
}

for (const tab of TABS) {
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

activate(TABS[0].id);
