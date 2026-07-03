import { api, type Row } from "../api.ts";
import {
  announceStatus,
  el,
  errorEl,
  formatPct,
  formatValue,
  labeledSearch,
  loadingEl,
  playerCell,
  renderTable,
} from "../dom.ts";
import { analyticsToolHeader } from "./analytics.ts";

// Matchup Explorer — NBA.com tracking matchups (who guarded whom). The
// source table is a current-season snapshot with no season column, so the
// whole tool is explicitly labeled as "this season". Pick a player to see
// both sides of their matchups, or browse the league defender leaderboards.

function madeAttempt(row: Record<string, unknown>, m: string, a: string): string {
  if (row[m] == null && row[a] == null) return "—";
  return `${formatValue(row[m])}-${formatValue(row[a])}`;
}

const MATCHUP_COLUMNS = (opponentLabel: string): Parameters<typeof renderTable>[0] => [
  { key: "opponent_name", label: opponentLabel, render: playerCell },
  { key: "gp", label: "G" },
  { key: "matchup_min", label: "Matchup min" },
  { key: "partial_poss", label: "Poss" },
  { key: "pts", label: "PTS" },
  { key: "pts_per100", label: "PTS/100" },
  { key: "fg", label: "FG", render: (_v, row) => madeAttempt(row, "fgm", "fga") },
  { key: "fg_pct", label: "FG%", format: formatPct },
  { key: "fg3", label: "3P", render: (_v, row) => madeAttempt(row, "fg3m", "fg3a") },
  { key: "ft", label: "FT", render: (_v, row) => madeAttempt(row, "ftm", "fta") },
  { key: "ast", label: "AST" },
  { key: "tov", label: "TOV" },
  { key: "blk", label: "BLK" },
];

export function renderMatchups(container: HTMLElement, detailId?: string): void {
  container.replaceChildren();
  announceStatus("Matchup Explorer");
  analyticsToolHeader(
    container,
    "Matchup Explorer",
    "NBA.com tracking matchups for the current season: who actually guarded whom, for how many possessions, and what it cost. Partial possessions mean a defender gets fractional credit when coverage switches.",
  );

  const { wrapper: searchWrapper, input: searchInput } = labeledSearch(
    "Find a player's matchups",
    "Search a player (e.g. Jokić)…",
  );
  const resultsList = el("ul", { className: "result-list" });
  const playerSection = el("div", {});
  const leadersSection = el("section", {}, [
    el("h3", { text: "Defender leaderboards" }),
    el("p", {
      className: "table-note",
      text: "Aggregated over every matchup a defender was charged with this season (minimum 750 partial possessions). “Toughest” = fewest points allowed per 100 partial possessions — matchup difficulty varies, so treat it as a conversation starter, not a DPOY ballot.",
    }),
    loadingEl(),
  ]);
  container.append(
    el("div", { className: "controls" }, [searchWrapper]),
    resultsList,
    playerSection,
    leadersSection,
  );

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
      const players = await api.searchPlayers(query, searchAbort.signal);
      resultsList.replaceChildren(
        ...players.slice(0, 8).map((p) => {
          const li = el("li", {});
          const button = el("button", {
            type: "button",
            className: "cell-link",
            text: `${formatValue(p.full_name)}${p.is_active === true ? "" : " (inactive)"}`,
          });
          button.addEventListener("click", () => {
            resultsList.replaceChildren();
            searchInput.value = formatValue(p.full_name);
            void showPlayer(formatValue(p.player_id), formatValue(p.full_name));
          });
          li.append(button);
          return li;
        }),
      );
    } catch {
      // Aborted or failed search; leave the list as-is.
    }
  }

  async function showPlayer(playerId: string, playerName: string): Promise<void> {
    playerSection.replaceChildren(loadingEl("Loading matchups…"));
    const [offense, defense] = await Promise.allSettled([
      api.getPlayerMatchups(playerId, "offense", 25),
      api.getPlayerMatchups(playerId, "defense", 25),
    ]);
    playerSection.replaceChildren();
    const sections: [string, string, PromiseSettledResult<Row[]>][] = [
      [
        `Who guarded ${playerName}`,
        `Stats are what ${playerName} produced against each defender.`,
        offense,
      ],
      [
        `Who ${playerName} guarded`,
        `Stats are what each opponent produced with ${playerName} as the nearest defender.`,
        defense,
      ],
    ];
    let anyRows = false;
    for (const [title, note, result] of sections) {
      const section = el("section", {}, [
        el("h3", { text: title }),
        el("p", { className: "table-note", text: note }),
      ]);
      if (result.status === "fulfilled" && result.value.length > 0) {
        anyRows = true;
        section.append(
          renderTable(
            MATCHUP_COLUMNS(title.startsWith("Who guarded") ? "Defender" : "Opponent"),
            result.value,
          ),
        );
      } else if (result.status === "fulfilled") {
        section.append(
          el("p", { className: "empty-state", text: "No tracked matchups this season." }),
        );
      } else {
        section.append(
          errorEl(result.reason instanceof Error ? result.reason.message : "failed to load"),
        );
      }
      playerSection.append(section);
    }
    announceStatus(
      anyRows ? `Matchups loaded for ${playerName}.` : `No matchups found for ${playerName}.`,
    );
  }

  async function loadLeaders(): Promise<void> {
    const [toughest, workload] = await Promise.allSettled([
      api.getMatchupLeaders("toughest", 20),
      api.getMatchupLeaders("workload", 20),
    ]);
    while (leadersSection.childNodes.length > 2) leadersSection.lastChild?.remove();
    const leaderColumns: Parameters<typeof renderTable>[0] = [
      { key: "defender_name", label: "Defender", render: playerCell },
      { key: "opponents", label: "Matchups" },
      { key: "total_matchup_min", label: "Min" },
      { key: "total_poss", label: "Poss" },
      { key: "pts_allowed", label: "PTS allowed" },
      { key: "pts_per100", label: "PTS/100" },
      { key: "fg_pct_allowed", label: "FG% allowed", format: formatPct },
      { key: "blk", label: "BLK" },
      { key: "tov_forced", label: "TOV forced" },
    ];
    if (toughest.status === "fulfilled") {
      leadersSection.append(
        el("h4", { text: "Toughest matchup defenders" }),
        renderTable(leaderColumns, toughest.value),
      );
    }
    if (workload.status === "fulfilled") {
      leadersSection.append(
        el("h4", { text: "Heaviest defensive workloads" }),
        renderTable(leaderColumns, workload.value),
      );
    }
    if (toughest.status === "rejected" && workload.status === "rejected") {
      const reason = toughest.reason instanceof Error ? toughest.reason.message : "request failed";
      leadersSection.append(errorEl(reason));
    }
  }

  void loadLeaders();
  if (detailId) void showPlayer(detailId, "this player");
}
