import { api, type Badge, type JerseyStint, type Row } from "../api.ts";
import {
  announceStatus,
  el,
  formatPct,
  formatValue,
  jerseyIcon,
  navigateToDetail,
  playerPhoto,
  renderDefList,
  renderTable,
} from "../dom.ts";
import { labelAwardType } from "../awards.ts";

export function renderPlayers(container: HTMLElement, initialPlayerId?: string): void {
  const resultsList = el("ul", { className: "result-list" });
  const detail = el("div", { className: "detail" });

  container.append(el("div", { className: "search-panel" }, [resultsList]), detail);

  // Search now lives in the persistent global header (see headerSearch.ts);
  // this tab just shows a small curated default subset until you navigate
  // to a specific player's profile.
  if (initialPlayerId) void showPlayer(initialPlayerId);
  else void loadCurated();

  async function loadCurated(): Promise<void> {
    resultsList.replaceChildren();
    announceStatus("Loading players…");
    try {
      const players = await api.searchPlayers("");
      if (players.length === 0) {
        resultsList.append(el("li", { className: "muted", text: "No players found." }));
        announceStatus("No players found.");
        return;
      }
      announceStatus(`Showing ${players.length} players.`);
      for (const p of players) {
        const sub = [p.position, p.team_abbreviation].filter(Boolean).join(" · ");
        const fullName = String(p.full_name);
        const button = el("button", { type: "button", className: "result-row" }, [
          playerPhoto(p.player_id, "player-photo-thumb", fullName),
          el("div", { className: "result-row-text" }, [
            el("span", { text: fullName }),
            el("span", { className: "muted", text: sub }),
          ]),
        ]);
        button.addEventListener("click", () => void showPlayer(String(p.player_id)));
        resultsList.append(el("li", {}, [button]));
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load players.";
      resultsList.append(el("li", { className: "muted", text: `Error: ${message}` }));
      announceStatus(`Failed to load players: ${message}`);
    }
  }

  async function showPlayer(id: string): Promise<void> {
    detail.replaceChildren(el("p", { className: "muted", text: "Loading…" }));
    announceStatus("Loading player profile…");
    try {
      const profile = await api.getPlayer(id);
      detail.replaceChildren();
      const { bio } = profile;
      if (!bio) {
        detail.append(el("p", { text: "Player not found." }));
        announceStatus("Player not found.");
        return;
      }
      renderBbrHeader(detail, { ...profile, bio });
      const jumpNav = el("nav", {
        className: "jump-nav",
        "aria-label": `${String(bio.full_name)} sections`,
      });
      detail.append(jumpNav);
      if (profile.career) renderStatsSummary(detail, profile.career, profile.careerEfgPct);
      announceStatus(`Loaded profile for ${String(bio.full_name)}.`);

      // Secondary sections load independently of the main profile — each is
      // its own endpoint/table, so one failing or being empty (e.g. a player
      // with no combine data) shouldn't block the others.
      const [highs, recentGames, rates, advanced, per100, shotSplits, onOff, combine, similar] =
        await Promise.allSettled([
          api.getPlayerHighs(id),
          api.getPlayerRecentGames(id),
          api.getPlayerRates(id),
          api.getPlayerAdvanced(id),
          api.getPlayerPer100(id),
          api.getPlayerShotSplits(id),
          api.getPlayerOnOff(id),
          api.getPlayerCombine(id),
          api.getSimilarPlayers(id),
        ]);
      if (recentGames.status === "fulfilled" && recentGames.value.length > 0)
        renderRecentGames(detail, recentGames.value);
      if (profile.seasons.length > 0)
        renderPlayerStats(detail, profile.seasons, rates, per100, advanced);
      if (highs.status === "fulfilled" && highs.value.length > 0) renderHighs(detail, highs.value);
      if (profile.awards.length > 0) renderAwards(detail, profile.awards);
      if (shotSplits.status === "fulfilled" && shotSplits.value.length > 0)
        renderShotSplits(detail, shotSplits.value);
      if (onOff.status === "fulfilled" && onOff.value.length > 0) renderOnOff(detail, onOff.value);
      if (combine.status === "fulfilled" && combine.value) renderCombine(detail, combine.value);
      if (similar.status === "fulfilled" && similar.value.length > 0)
        renderSimilarPlayers(detail, similar.value, (pid) => void showPlayer(pid));
      renderJumpNav(jumpNav, [
        profile.career ? ["Summary", "player-summary"] : null,
        recentGames.status === "fulfilled" && recentGames.value.length > 0
          ? ["Recent", "player-recent"]
          : null,
        profile.seasons.length > 0 ? ["Stats", "player-stats"] : null,
        highs.status === "fulfilled" && highs.value.length > 0 ? ["Highs", "player-highs"] : null,
        profile.awards.length > 0 ? ["Awards", "player-awards"] : null,
        shotSplits.status === "fulfilled" && shotSplits.value.length > 0
          ? ["Shooting", "player-shooting"]
          : null,
        onOff.status === "fulfilled" && onOff.value.length > 0 ? ["On/Off", "player-on-off"] : null,
        combine.status === "fulfilled" && combine.value ? ["Combine", "player-combine"] : null,
        similar.status === "fulfilled" && similar.value.length > 0
          ? ["Similar", "player-similar"]
          : null,
      ]);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load player.";
      detail.replaceChildren(el("p", { className: "muted", text: `Error: ${message}` }));
      announceStatus(`Failed to load player: ${message}`);
    }
  }
}

// ---------------------------------------------------------------------------
// Bio header — modeled on basketball-reference.com's player page header.
// Several BBR fields have no equivalent in this database and are simply
// omitted rather than guessed: nickname, handedness ("Shoots:"), birth
// city/state, death date, NBA debut date, and a College/High-School split
// (the source only has one ambiguous "school" field, shown generically).
// Finals MVP and NBA Champion badges are also omitted — not derivable
// without risking a wrong answer (no clean "Finals winner" signal in the
// available tables).
// ---------------------------------------------------------------------------

const COUNTRY_FLAGS: Record<string, string> = {
  USA: "🇺🇸",
  Canada: "🇨🇦",
  Germany: "🇩🇪",
  France: "🇫🇷",
  Spain: "🇪🇸",
  Australia: "🇦🇺",
  Serbia: "🇷🇸",
  Slovenia: "🇸🇮",
  Greece: "🇬🇷",
  Nigeria: "🇳🇬",
  Cameroon: "🇨🇲",
  Lithuania: "🇱🇹",
  Croatia: "🇭🇷",
  Argentina: "🇦🇷",
  Brazil: "🇧🇷",
  Italy: "🇮🇹",
  Turkey: "🇹🇷",
  Latvia: "🇱🇻",
  Senegal: "🇸🇳",
  "Democratic Republic of the Congo": "🇨🇩",
  "Puerto Rico": "🇵🇷",
};

function ordinal(n: number): string {
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${n}th`;
  switch (n % 10) {
    case 1:
      return `${n}st`;
    case 2:
      return `${n}nd`;
    case 3:
      return `${n}rd`;
    default:
      return `${n}th`;
  }
}

/** "6-7" (feet-inches, as stored) -> 201 (cm), via simple unit conversion —
 *  not a separately sourced fact, just arithmetic on the stored height. */
function heightToCm(height: unknown): number | null {
  const match = /^(\d+)-(\d+)$/.exec(formatValue(height));
  if (match?.[1] === undefined || match[2] === undefined || match[1] === "—") return null;
  const totalInches = Number(match[1]) * 12 + Number(match[2]);
  return Math.round(totalInches * 2.54);
}

function lbToKg(weight: unknown): number | null {
  const lb = Number(weight);
  if (!Number.isFinite(lb) || lb <= 0) return null;
  return Math.round(lb * 0.453592);
}

function formatBirthDate(value: unknown): string {
  if (typeof value !== "string" || value === "") return formatValue(value);
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return formatValue(value);
  return date.toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

function formatDraftLine(draft: Row | null): string {
  if (!draft) return "Undrafted";
  // Territorial picks (used 1949-1965 to let a team claim a local-college
  // star) have no round/pick number in the source data — round_number,
  // round_pick, and overall_pick are all stored as 0, which would otherwise
  // render as the nonsensical "0th round (0th pick, 0th overall)".
  if (draft.draft_type === "Territorial") {
    return `${formatValue(draft.team_name)} (${formatValue(draft.team_abbreviation)}), territorial pick, ${formatValue(draft.season)} NBA Draft`;
  }
  const round = Number(draft.round_number);
  const roundPick = Number(draft.round_pick);
  const overall = Number(draft.overall_pick);
  const parts = [
    `${formatValue(draft.team_name)} (${formatValue(draft.team_abbreviation)})`,
    Number.isFinite(round) ? `${ordinal(round)} round` : null,
    Number.isFinite(roundPick) && Number.isFinite(overall)
      ? `${ordinal(roundPick)} pick, ${ordinal(overall)} overall`
      : null,
  ].filter(Boolean);
  return `${parts[0]}, ${parts.slice(1).join(" (")}${parts.length > 1 ? ")" : ""}, ${formatValue(draft.season)} NBA Draft`;
}

function bioLine(label: string, value: unknown): HTMLElement | null {
  if (value === null || value === undefined || value === "") return null;
  return el("p", { className: "bio-line" }, [
    el("span", { className: "bio-label", text: `${label}: ` }),
    formatValue(value),
  ]);
}

function bornLine(birthDate: string, country: unknown): HTMLElement | null {
  if (!birthDate) return null;
  const countryName = typeof country === "string" && country !== "" ? country : "";
  const flag = countryName && COUNTRY_FLAGS[countryName] ? COUNTRY_FLAGS[countryName] : "";
  const children: (Node | string)[] = [
    el("span", { className: "bio-label", text: "Born: " }),
    birthDate,
  ];
  if (countryName) {
    children.push(" ");
    if (flag) children.push(el("span", { "aria-hidden": "true", text: flag }));
    children.push(el("span", { className: "visually-hidden", text: ` ${countryName}` }));
  }
  return el("p", { className: "bio-line" }, children);
}

function renderBbrHeader(
  container: HTMLElement,
  profile: {
    bio: Row;
    draft: Row | null;
    hallOfFameYear: number | null;
    isGreatest75: boolean;
    allStarCount: number;
    badges: Badge[];
    jerseyHistory: JerseyStint[];
  },
): void {
  const { bio, draft, hallOfFameYear, isGreatest75, allStarCount, badges, jerseyHistory } = profile;
  const fullName = String(bio.full_name);

  const cm = heightToCm(bio.height);
  const kg = lbToKg(bio.weight);
  const heightWeight = [
    bio.height ? formatValue(bio.height) : null,
    bio.weight ? `${formatValue(bio.weight)}lb` : null,
  ]
    .filter(Boolean)
    .join(", ");
  const metric = cm && kg ? ` (${cm}cm, ${kg}kg)` : "";

  const photo = playerPhoto(bio.player_id, "player-photo-header", fullName);

  const birthDate = formatBirthDate(bio.birthdate ?? bio.birth_date);

  const essentialLines = [
    bioLine("Position", bio.position),
    heightWeight ? el("p", { className: "bio-line" }, [`${heightWeight}${metric}`]) : null,
  ].filter((n): n is HTMLElement => n !== null);

  const extraLines = [
    bornLine(birthDate, bio.country),
    bioLine("School", bio.school),
    bioLine("Draft", formatDraftLine(draft)),
    hallOfFameYear ? bioLine("Hall of Fame", `Inducted in ${hallOfFameYear}`) : null,
    bioLine("Career Length", bio.season_exp ? `${formatValue(bio.season_exp)} years` : null),
  ].filter((n): n is HTMLElement => n !== null);

  const bioExtra = el("div", { className: "bio-extra", hidden: "" }, extraLines);
  const bioToggle = el("button", {
    type: "button",
    className: "bio-toggle",
    "aria-expanded": "false",
    text: "More bio, draft & career info ▾",
  });
  bioToggle.addEventListener("click", () => {
    const expanded = bioToggle.getAttribute("aria-expanded") === "true";
    bioExtra.hidden = expanded;
    bioToggle.setAttribute("aria-expanded", String(!expanded));
    bioToggle.textContent = expanded
      ? "More bio, draft & career info ▾"
      : "Less bio, draft & career info ▴";
  });

  const infoLines =
    extraLines.length > 0 ? [...essentialLines, bioToggle, bioExtra] : essentialLines;

  const headlineBadges: HTMLElement[] = [];
  if (hallOfFameYear)
    headlineBadges.push(el("span", { className: "badge badge-hof", text: "Hall of Fame" }));
  if (allStarCount > 0) {
    headlineBadges.push(
      el("span", { className: "badge badge-allstar", text: `${allStarCount}x All Star` }),
    );
  }
  if (isGreatest75)
    headlineBadges.push(el("span", { className: "badge badge-75", text: "NBA 75th Anniv. Team" }));

  const badgeChips = badges.map((b) =>
    el("span", { className: "badge badge-honor", text: `${b.season} ${b.label}` }),
  );

  const jerseyChips = jerseyHistory.map((j) => {
    const years =
      j.start_year === j.end_year ? String(j.start_year) : `${j.start_year}-${j.end_year}`;
    return jerseyIcon(j.jersey_num, j.primary, j.trim, `${j.team_name}, ${years}`);
  });

  const sideColumn = el(
    "div",
    { className: "bbr-side" },
    [
      el("div", { className: "bbr-badges" }, [...headlineBadges, ...badgeChips]),
      jerseyChips.length > 0 ? el("div", { className: "jersey-grid" }, jerseyChips) : null,
    ].filter((n): n is HTMLElement => n !== null),
  );

  container.append(
    el("div", { className: "bbr-header" }, [
      photo,
      el("div", { className: "bbr-info" }, [el("h2", { text: fullName }), ...infoLines]),
      sideColumn,
    ]),
  );
}

function renderStatsSummary(
  container: HTMLElement,
  career: Row,
  careerEfgPct: number | null,
): void {
  container.append(
    el("section", { id: "player-summary" }, [
      renderTable(
        [
          { key: "label", label: "Summary" },
          { key: "gp", label: "G" },
          { key: "ppg", label: "PTS" },
          { key: "rpg", label: "TRB" },
          { key: "apg", label: "AST" },
          { key: "fg_pct", label: "FG%", format: formatPct },
          { key: "fg3_pct", label: "FG3%", format: formatPct },
          { key: "ft_pct", label: "FT%", format: formatPct },
          { key: "efg_pct", label: "eFG%", format: formatPct },
        ],
        [
          {
            label: "Career",
            gp: career.career_gp,
            ppg: career.career_ppg,
            rpg: career.career_rpg,
            apg: career.career_apg,
            fg_pct: career.career_fg_pct,
            fg3_pct: career.career_fg3_pct,
            ft_pct: career.career_ft_pct,
            efg_pct: careerEfgPct,
          },
        ],
      ),
    ]),
  );
}

function renderRecentGames(container: HTMLElement, games: Row[]): void {
  container.append(
    el("section", { id: "player-recent" }, [
      sectionHeading("player-recent", "Recent games"),
      renderTable(
        [
          { key: "game_date", label: "Date", format: (v) => String(v).slice(0, 10) },
          {
            key: "opponent",
            label: "Opp",
            render: (_v, row) => {
              const prefix = row.location === "Home" ? "vs. " : "@ ";
              const color =
                typeof row.opponent_primary_color === "string"
                  ? row.opponent_primary_color
                  : "#777";
              const swatch = el("span", { className: "team-swatch", style: `background:${color}` });
              return el("span", {}, [swatch, `${prefix}${formatValue(row.opponent)}`]);
            },
          },
          { key: "result", label: "W/L" },
          { key: "min", label: "MIN", format: (v) => (v == null ? "—" : Number(v).toFixed(1)) },
          { key: "pts", label: "PTS" },
          { key: "reb", label: "REB" },
          { key: "ast", label: "AST" },
          { key: "stl", label: "STL" },
          { key: "blk", label: "BLK" },
          {
            key: "fgm",
            label: "FG",
            render: (_v, row) => `${formatValue(row.fgm)}-${formatValue(row.fga)}`,
          },
          {
            key: "fg3m",
            label: "3P",
            render: (_v, row) => `${formatValue(row.fg3m)}-${formatValue(row.fg3a)}`,
          },
          {
            key: "ftm",
            label: "FT",
            render: (_v, row) => `${formatValue(row.ftm)}-${formatValue(row.fta)}`,
          },
          { key: "plus_minus", label: "+/-" },
        ],
        games,
      ),
    ]),
  );
}

let seasonTabsIdCounter = 0;
let statTabsIdCounter = 0;

const SEASON_TYPE_TABS = [
  { id: "regular", label: "Regular Season", seasonType: "Regular" },
  { id: "cup", label: "NBA Cup", seasonType: "Cup" },
  { id: "playoffs", label: "Playoffs", seasonType: "Playoffs" },
] as const;

function renderJumpNav(container: HTMLElement, items: ([string, string] | null)[]): void {
  const links = items
    .filter((item): item is [string, string] => item !== null)
    .map(([label, id]) => el("a", { href: `#${id}`, text: label }));
  container.replaceChildren(...links);
}

function sectionHeading(id: string, text: string): HTMLElement {
  return el("h3", { id, text });
}

function tableNote(text: string): HTMLElement {
  return el("p", { className: "table-note", text });
}

function cellButton(label: string, onClick: () => void, ariaLabel?: string): HTMLElement {
  const button = el("button", {
    type: "button",
    className: "cell-link",
    text: label,
    "aria-label": ariaLabel ?? label,
  });
  button.addEventListener("click", onClick);
  return button;
}

function teamCell(value: unknown, row: Row): Node | string {
  const label = formatValue(value);
  const teamId = Number(row.team_id);
  if (!Number.isFinite(teamId) || label === "—") return label;
  return cellButton(
    label,
    () => navigateToDetail("teams", String(teamId)),
    `${label} team profile`,
  );
}

interface StatTab {
  id: string;
  label: string;
  render: () => HTMLElement[];
}

function renderStatTabs(container: HTMLElement, titleId: string, tabs: StatTab[]): void {
  statTabsIdCounter += 1;
  const tabPrefix = `player-stat-${statTabsIdCounter}`;
  const tabList = el("div", {
    className: "stat-tabs",
    role: "tablist",
    "aria-labelledby": titleId,
  });
  const panel = el("div", { className: "stat-tab-panel", role: "tabpanel" });
  const buttons: HTMLButtonElement[] = [];

  const activate = (tabId: string): void => {
    const activeTab = tabs.find((tab) => tab.id === tabId) ?? tabs[0];
    for (const button of buttons) {
      const isActive = button.dataset.statTab === activeTab.id;
      button.classList.toggle("active", isActive);
      button.setAttribute("aria-selected", String(isActive));
      button.tabIndex = isActive ? 0 : -1;
    }
    panel.setAttribute("aria-labelledby", `${tabPrefix}-${activeTab.id}`);
    panel.replaceChildren(...activeTab.render());
    announceStatus(`${activeTab.label} stats selected.`);
  };

  for (const tab of tabs) {
    const button = el("button", {
      type: "button",
      className: "stat-tab",
      text: tab.label,
      role: "tab",
      id: `${tabPrefix}-${tab.id}`,
      "aria-selected": "false",
      tabindex: "-1",
    }) as HTMLButtonElement;
    button.dataset.statTab = tab.id;
    button.addEventListener("click", () => activate(tab.id));
    buttons.push(button);
    tabList.append(button);
  }

  container.append(tabList, panel);
  activate(tabs[0].id);
}

function isCupFinalOnly(row: Row): boolean {
  return row.season_type === "Cup" && row.is_cup_final_only === true;
}

function rowsForSeasonType(rows: Row[], seasonType: string): Row[] {
  if (seasonType === "Cup") {
    return rows.filter(isCupFinalOnly);
  }
  return rows.filter((row) => row.season_type === seasonType);
}

function hasCupRows(rows: Row[]): boolean {
  return rows.some((row) => row.season_type === "Cup");
}

function renderNoCupFinalNote(): HTMLElement {
  return tableNote(
    "No NBA Cup Final-only aggregate is available. NBA Cup group, quarterfinal, and semifinal games already count in Regular Season totals, so duplicate Cup aggregates are hidden.",
  );
}

function renderSeasonTypeSplitTable(
  title: string,
  rows: Row[],
  columns: Parameters<typeof renderTable>[0],
  columnGroups: Parameters<typeof renderTable>[2] = [],
  leadingNotes: HTMLElement[] = [],
): HTMLElement {
  const includesCupRows = hasCupRows(rows);
  const tabs = SEASON_TYPE_TABS.map((tab) => ({
    ...tab,
    rows: rowsForSeasonType(rows, tab.seasonType),
  })).filter((tab) => tab.rows.length > 0 || (tab.seasonType === "Cup" && includesCupRows));

  if (tabs.length <= 1) {
    return el("section", { className: "stat-tabs-section" }, [
      el("h4", { text: title }),
      ...leadingNotes,
      renderTable(columns, tabs[0]?.rows ?? rows, columnGroups),
    ]);
  }

  const section = el("section", { className: "stat-tabs-section" });
  seasonTabsIdCounter += 1;
  const titleId = `season-split-title-${seasonTabsIdCounter}`;
  const tabList = el("div", {
    className: "stat-tabs",
    role: "tablist",
    "aria-labelledby": titleId,
  });
  const panel = el("div", { className: "stat-tab-panel", role: "tabpanel" });
  const buttons: HTMLButtonElement[] = [];

  const activate = (tabId: string): void => {
    const activeTab = tabs.find((tab) => tab.id === tabId) ?? tabs[0];
    for (const button of buttons) {
      const isActive = button.dataset.statTab === activeTab.id;
      button.classList.toggle("active", isActive);
      button.setAttribute("aria-selected", String(isActive));
      button.tabIndex = isActive ? 0 : -1;
    }
    panel.setAttribute("aria-labelledby", `${titleId}-${activeTab.id}`);
    panel.replaceChildren(
      ...leadingNotes,
      activeTab.seasonType === "Cup" && activeTab.rows.length === 0
        ? renderNoCupFinalNote()
        : renderTable(columns, activeTab.rows, columnGroups),
    );
    announceStatus(`${activeTab.label} ${title.toLowerCase()} selected.`);
  };

  for (const tab of tabs) {
    const button = el("button", {
      type: "button",
      className: "stat-tab",
      text: tab.label,
      role: "tab",
      id: `${titleId}-${tab.id}`,
      "aria-selected": "false",
      tabindex: "-1",
    }) as HTMLButtonElement;
    button.dataset.statTab = tab.id;
    button.addEventListener("click", () => activate(tab.id));
    buttons.push(button);
    tabList.append(button);
  }

  section.append(el("h4", { id: titleId, text: title }), tabList, panel);
  activate("regular");
  return section;
}

function seasonAverageColumns(): Parameters<typeof renderTable>[0] {
  return [
    { key: "season_year", label: "Season" },
    { key: "team_abbreviation", label: "Team", render: teamCell },
    { key: "gp", label: "G" },
    { key: "avg_pts", label: "PTS" },
    { key: "avg_reb", label: "TRB" },
    { key: "avg_ast", label: "AST" },
    { key: "fg_pct", label: "FG%", format: formatPct },
  ];
}

function per36Columns(): Parameters<typeof renderTable>[0] {
  return [
    { key: "season_year", label: "Season" },
    { key: "gp", label: "G" },
    { key: "pts_per36", label: "PTS" },
    { key: "reb_per36", label: "TRB" },
    { key: "ast_per36", label: "AST" },
    { key: "stl_per36", label: "STL" },
    { key: "blk_per36", label: "BLK" },
    { key: "tov_per36", label: "TOV" },
  ];
}

function per100Columns(): Parameters<typeof renderTable>[0] {
  return [
    { key: "season_year", label: "Season" },
    { key: "team_abbreviation", label: "Team", render: teamCell },
    { key: "gp", label: "G" },
    { key: "pts_per100", label: "PTS" },
    { key: "reb_per100", label: "TRB" },
    { key: "ast_per100", label: "AST" },
    { key: "stl_per100", label: "STL" },
    { key: "blk_per100", label: "BLK" },
    { key: "tov_per100", label: "TOV" },
    { key: "fgm_per100", label: "FG" },
    { key: "fga_per100", label: "FGA" },
    { key: "fg3m_per100", label: "3P" },
    { key: "fg3a_per100", label: "3PA" },
    { key: "ftm_per100", label: "FT" },
    { key: "fta_per100", label: "FTA" },
  ];
}

function advancedColumns(): Parameters<typeof renderTable>[0] {
  return [
    { key: "season_year", label: "Season" },
    { key: "team_abbreviation", label: "Team", render: teamCell },
    { key: "gp", label: "G" },
    { key: "avg_off_rating", label: "ORtg" },
    { key: "avg_def_rating", label: "DRtg" },
    { key: "avg_net_rating", label: "NetRtg" },
    { key: "avg_pace", label: "Pace" },
    { key: "avg_ts_pct", label: "TS%", format: formatPct },
    { key: "avg_usg_pct", label: "USG%", format: formatPct },
    { key: "avg_efg_pct", label: "eFG%", format: formatPct },
    { key: "avg_ast_pct", label: "AST%", format: formatPct },
    { key: "avg_oreb_pct", label: "ORB%", format: formatPct },
    { key: "avg_dreb_pct", label: "DRB%", format: formatPct },
    { key: "avg_reb_pct", label: "TRB%", format: formatPct },
    { key: "avg_tov_pct", label: "TOV%", format: formatPct },
    { key: "avg_pie", label: "PIE%", format: formatPct },
    { key: "per", label: "PER" },
    { key: "ws", label: "WS" },
    { key: "obpm", label: "OBPM" },
    { key: "dbpm", label: "DBPM" },
    { key: "bpm", label: "BPM" },
    { key: "vorp", label: "VORP" },
  ];
}

function advancedColumnGroups(): Parameters<typeof renderTable>[2] {
  return [
    { label: "Context", span: 3 },
    { label: "Ratings", span: 4 },
    { label: "Efficiency", span: 4 },
    { label: "Rebounding / Ball", span: 5 },
    { label: "BBR Value", span: 6 },
  ];
}

function renderSeasonAverageTable(seasons: Row[]): HTMLElement {
  return renderSeasonTypeSplitTable("Per game averages", seasons, seasonAverageColumns());
}

function renderPer36Table(rows: Row[]): HTMLElement {
  return renderSeasonTypeSplitTable("Per 36 minutes", rows, per36Columns());
}

function renderPer100Table(rows: Row[]): HTMLElement {
  return renderSeasonTypeSplitTable(
    "Per 100 possessions",
    rows,
    per100Columns(),
    [],
    [tableNote("BBR regular-season per-100 possession rows.")],
  );
}

function renderAdvancedStatsTable(rows: Row[]): HTMLElement {
  return renderSeasonTypeSplitTable(
    "Advanced stats",
    rows,
    advancedColumns(),
    advancedColumnGroups(),
    [tableNote("BBR season metrics with NBA tracking context where available.")],
  );
}

function renderPlayerStats(
  container: HTMLElement,
  seasons: Row[],
  rates: PromiseSettledResult<{ per36: Row[]; per48: Row[] }>,
  per100: PromiseSettledResult<Row[]>,
  advanced: PromiseSettledResult<Row[]>,
): void {
  const tabs: StatTab[] = [
    { id: "per-game", label: "Per Game", render: () => [renderSeasonAverageTable(seasons)] },
  ];

  if (rates.status === "fulfilled" && rates.value.per36.length > 0) {
    tabs.push({
      id: "per-36",
      label: "Per 36",
      render: () => [renderPer36Table(rates.value.per36)],
    });
  }

  if (per100.status === "fulfilled" && per100.value.length > 0) {
    tabs.push({
      id: "per-100",
      label: "Per 100",
      render: () => [renderPer100Table(per100.value)],
    });
  }

  if (advanced.status === "fulfilled" && advanced.value.length > 0) {
    tabs.push({
      id: "advanced",
      label: "Advanced",
      render: () => [renderAdvancedStatsTable(advanced.value)],
    });
  }

  const section = el("section", { className: "stat-tabs-section" }, [
    sectionHeading("player-stats", "Season stats"),
  ]);
  renderStatTabs(section, "player-stats", tabs);
  container.append(section);
}

function renderAwards(container: HTMLElement, awards: Row[]): void {
  container.append(
    el("section", {}, [
      sectionHeading("player-awards", "Awards"),
      renderTable(
        [
          { key: "season", label: "Season" },
          {
            key: "award_type",
            label: "Award",
            format: (v) => labelAwardType(String(v)),
          },
          { key: "description", label: "Detail" },
        ],
        awards,
      ),
    ]),
  );
}

function renderHighs(container: HTMLElement, highs: Row[]): void {
  container.append(
    el("section", {}, [
      sectionHeading("player-highs", "Career highs"),
      renderTable(
        [
          { key: "stat", label: "Stat" },
          { key: "value", label: "Value" },
          { key: "game_date", label: "Date", format: (v) => String(v).slice(0, 10) },
          { key: "team_abbreviation", label: "Team" },
        ],
        highs,
      ),
    ]),
  );
}

interface AggregatedShotZone extends Row {
  zone: string;
  area: string;
  attempts: number;
  makes: number;
  fg_pct: number | null;
  league_fg_pct: number | null;
  fg_pct_delta: number | null;
  avg_distance: number | null;
}

interface ShotZoneShape {
  kind: "path" | "rect" | "circle";
  attrs: Record<string, string | number>;
  labelX: number;
  labelY: number;
  labelAnchor?: "start" | "middle" | "end";
  labelClassName?: string;
}

const SVG_NS = "http://www.w3.org/2000/svg";
const COURT_WIDTH_FT = 50;
const HALF_COURT_LENGTH_FT = 47;
const CHART_SCALE = 10;
const HOOP_X_FT = 25;
const HOOP_Y_FT = 5.25;
const BACKBOARD_Y_FT = 4;
const FREE_THROW_Y_FT = 19;
const LANE_LEFT_FT = 17;
const LANE_RIGHT_FT = 33;
const THREE_POINT_RADIUS_FT = 23.75;
const CORNER_THREE_LEFT_X_FT = 3;
const CORNER_THREE_RIGHT_X_FT = 47;
const CORNER_THREE_Y_FT =
  HOOP_Y_FT + Math.sqrt(THREE_POINT_RADIUS_FT ** 2 - (HOOP_X_FT - CORNER_THREE_LEFT_X_FT) ** 2);

const SHOT_ZONE_ORDER = [
  "Restricted Area",
  "In The Paint (Non-RA)",
  "Mid-Range",
  "Left Corner 3",
  "Right Corner 3",
  "Above the Break 3",
  "Backcourt",
];

const SHOT_AREA_ORDER = [
  "Left Side(L)",
  "Left Side Center(LC)",
  "Center(C)",
  "Right Side Center(RC)",
  "Right Side(R)",
  "Back Court(BC)",
];

function svgEl(tag: string, attrs: Record<string, string | number> = {}): SVGElement {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  return node;
}

function courtX(xFt: number): number {
  return xFt * CHART_SCALE;
}

function courtY(yFt: number): number {
  return (HALF_COURT_LENGTH_FT - yFt) * CHART_SCALE;
}

function courtPoint(xFt: number, yFt: number): string {
  return `${courtX(xFt).toFixed(2)} ${courtY(yFt).toFixed(2)}`;
}

function courtRectAttrs(
  xFt: number,
  yFt: number,
  widthFt: number,
  heightFt: number,
): Record<string, number> {
  return {
    x: courtX(xFt),
    y: courtY(yFt + heightFt),
    width: widthFt * CHART_SCALE,
    height: heightFt * CHART_SCALE,
  };
}

function courtRectPath(xFt: number, yFt: number, widthFt: number, heightFt: number): string {
  return [
    `M ${courtPoint(xFt, yFt)}`,
    `L ${courtPoint(xFt + widthFt, yFt)}`,
    `L ${courtPoint(xFt + widthFt, yFt + heightFt)}`,
    `L ${courtPoint(xFt, yFt + heightFt)}`,
    "Z",
  ].join(" ");
}

function arcAngleForX(xFt: number): number {
  return (Math.acos((xFt - HOOP_X_FT) / THREE_POINT_RADIUS_FT) * 180) / Math.PI;
}

function arcYForX(xFt: number): number {
  return HOOP_Y_FT + Math.sqrt(THREE_POINT_RADIUS_FT ** 2 - (xFt - HOOP_X_FT) ** 2);
}

function arcPoint(
  centerXFt: number,
  centerYFt: number,
  radiusFt: number,
  angleDeg: number,
): { x: number; y: number } {
  const radians = (angleDeg * Math.PI) / 180;
  return {
    x: centerXFt + radiusFt * Math.cos(radians),
    y: centerYFt + radiusFt * Math.sin(radians),
  };
}

function arcPoints(
  centerXFt: number,
  centerYFt: number,
  radiusFt: number,
  startDeg: number,
  endDeg: number,
  steps = 32,
): { x: number; y: number }[] {
  return Array.from({ length: steps + 1 }, (_, index) => {
    const t = index / steps;
    return arcPoint(centerXFt, centerYFt, radiusFt, startDeg + (endDeg - startDeg) * t);
  });
}

function courtPath(points: { x: number; y: number }[]): string {
  const [first, ...rest] = points;
  if (!first) return "";
  return [
    `M ${courtPoint(first.x, first.y)}`,
    ...rest.map((point) => `L ${courtPoint(point.x, point.y)}`),
    "Z",
  ].join(" ");
}

function courtPolyline(points: { x: number; y: number }[]): string {
  const [first, ...rest] = points;
  if (!first) return "";
  return [
    `M ${courtPoint(first.x, first.y)}`,
    ...rest.map((point) => `L ${courtPoint(point.x, point.y)}`),
  ].join(" ");
}

function arcBoundaryPoints(
  startXFt: number,
  endXFt: number,
  steps = 24,
): { x: number; y: number }[] {
  return arcPoints(
    HOOP_X_FT,
    HOOP_Y_FT,
    THREE_POINT_RADIUS_FT,
    arcAngleForX(startXFt),
    arcAngleForX(endXFt),
    steps,
  );
}

function numberValue(value: unknown): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function nullableNumber(value: unknown): number | null {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function textValue(value: unknown, fallback = "Unknown"): string {
  if (typeof value === "string" && value.trim() !== "") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return fallback;
}

function shotZoneSortKey(row: AggregatedShotZone): string {
  const zoneIndex = SHOT_ZONE_ORDER.indexOf(row.zone);
  const areaIndex = SHOT_AREA_ORDER.indexOf(row.area);
  return `${zoneIndex === -1 ? 99 : zoneIndex}:${areaIndex === -1 ? 99 : areaIndex}:${row.zone}:${row.area}`;
}

function aggregateShotZones(rows: Row[]): AggregatedShotZone[] {
  const groups = new Map<
    string,
    {
      zone: string;
      area: string;
      attempts: number;
      makes: number;
      leagueWeighted: number;
      leagueWeight: number;
      distanceWeighted: number;
      distanceWeight: number;
    }
  >();

  for (const row of rows) {
    const zone = textValue(row.shot_zone_basic);
    const area = textValue(row.shot_zone_area);
    const key = `${zone}||${area}`;
    const attempts = numberValue(row.attempts);
    const makes = numberValue(row.makes);
    const leaguePct = nullableNumber(row.league_fg_pct);
    const distance = nullableNumber(row.avg_distance);
    const group = groups.get(key) ?? {
      zone,
      area,
      attempts: 0,
      makes: 0,
      leagueWeighted: 0,
      leagueWeight: 0,
      distanceWeighted: 0,
      distanceWeight: 0,
    };

    group.attempts += attempts;
    group.makes += makes;
    if (leaguePct !== null && attempts > 0) {
      group.leagueWeighted += leaguePct * attempts;
      group.leagueWeight += attempts;
    }
    if (distance !== null && attempts > 0) {
      group.distanceWeighted += distance * attempts;
      group.distanceWeight += attempts;
    }
    groups.set(key, group);
  }

  return Array.from(groups.values())
    .map((group) => {
      const fgPct = group.attempts > 0 ? group.makes / group.attempts : null;
      const leaguePct = group.leagueWeight > 0 ? group.leagueWeighted / group.leagueWeight : null;
      return {
        zone: group.zone,
        area: group.area,
        attempts: group.attempts,
        makes: group.makes,
        fg_pct: fgPct,
        league_fg_pct: leaguePct,
        fg_pct_delta: fgPct !== null && leaguePct !== null ? fgPct - leaguePct : null,
        avg_distance:
          group.distanceWeight > 0 ? group.distanceWeighted / group.distanceWeight : null,
      };
    })
    .sort((a, b) => shotZoneSortKey(a).localeCompare(shotZoneSortKey(b)));
}

function shotZoneColor(delta: number | null): string {
  if (delta === null) return "#d4d4d4";
  if (delta >= 0.04) return "#15803d";
  if (delta >= 0.015) return "#86a83f";
  if (delta > -0.015) return "#d8c76a";
  if (delta > -0.04) return "#d8893f";
  return "#b42318";
}

function shotZoneOpacity(attempts: number, maxAttempts: number): string {
  if (maxAttempts <= 0) return "0.45";
  const scaled = Math.sqrt(attempts / maxAttempts);
  return String(Math.min(0.88, Math.max(0.34, 0.28 + scaled * 0.6)).toFixed(2));
}

function zoneShape(zone: string, area: string, fallbackIndex: number): ShotZoneShape {
  const leftArcY = arcYForX(LANE_LEFT_FT);
  const rightArcY = arcYForX(LANE_RIGHT_FT);

  if (zone === "Restricted Area") {
    return {
      kind: "circle",
      attrs: {
        cx: courtX(HOOP_X_FT),
        cy: courtY(HOOP_Y_FT),
        r: 4 * CHART_SCALE,
      },
      labelX: courtX(HOOP_X_FT),
      labelY: courtY(HOOP_Y_FT - 0.4),
    };
  }
  if (zone === "In The Paint (Non-RA)" && area === "Left Side(L)") {
    return {
      kind: "path",
      attrs: { d: courtRectPath(LANE_LEFT_FT, 0, 4, FREE_THROW_Y_FT) },
      labelX: courtX(19),
      labelY: courtY(10.4),
    };
  }
  if (zone === "In The Paint (Non-RA)" && area === "Right Side(R)") {
    return {
      kind: "path",
      attrs: { d: courtRectPath(29, 0, 4, FREE_THROW_Y_FT) },
      labelX: courtX(31),
      labelY: courtY(10.4),
    };
  }
  if (zone === "In The Paint (Non-RA)") {
    return {
      kind: "path",
      attrs: { d: courtRectPath(21, 0, 8, FREE_THROW_Y_FT) },
      labelX: courtX(25),
      labelY: courtY(13.2),
    };
  }
  if (zone === "Mid-Range" && area === "Left Side(L)") {
    return {
      kind: "path",
      attrs: { d: courtRectPath(CORNER_THREE_LEFT_X_FT, 0, LANE_LEFT_FT - 3, 8) },
      labelX: courtX(10),
      labelY: courtY(4.1),
    };
  }
  if (zone === "Mid-Range" && area === "Left Side Center(LC)") {
    return {
      kind: "path",
      attrs: {
        d: courtPath([
          { x: CORNER_THREE_LEFT_X_FT, y: 8 },
          { x: LANE_LEFT_FT, y: 8 },
          { x: LANE_LEFT_FT, y: leftArcY },
          ...arcBoundaryPoints(LANE_LEFT_FT, CORNER_THREE_LEFT_X_FT),
          { x: CORNER_THREE_LEFT_X_FT, y: 8 },
        ]),
      },
      labelX: courtX(10.4),
      labelY: courtY(14.6),
    };
  }
  if (zone === "Mid-Range" && area === "Right Side(R)") {
    return {
      kind: "path",
      attrs: { d: courtRectPath(LANE_RIGHT_FT, 0, CORNER_THREE_RIGHT_X_FT - LANE_RIGHT_FT, 8) },
      labelX: courtX(40),
      labelY: courtY(4.1),
    };
  }
  if (zone === "Mid-Range" && area === "Right Side Center(RC)") {
    return {
      kind: "path",
      attrs: {
        d: courtPath([
          { x: LANE_RIGHT_FT, y: 8 },
          { x: CORNER_THREE_RIGHT_X_FT, y: 8 },
          { x: CORNER_THREE_RIGHT_X_FT, y: CORNER_THREE_Y_FT },
          ...arcBoundaryPoints(CORNER_THREE_RIGHT_X_FT, LANE_RIGHT_FT),
          { x: LANE_RIGHT_FT, y: 8 },
        ]),
      },
      labelX: courtX(39.6),
      labelY: courtY(14.6),
    };
  }
  if (zone === "Mid-Range") {
    return {
      kind: "path",
      attrs: {
        d: courtPath([
          { x: LANE_LEFT_FT, y: FREE_THROW_Y_FT },
          { x: LANE_RIGHT_FT, y: FREE_THROW_Y_FT },
          { x: LANE_RIGHT_FT, y: rightArcY },
          ...arcBoundaryPoints(LANE_RIGHT_FT, LANE_LEFT_FT),
          { x: LANE_LEFT_FT, y: FREE_THROW_Y_FT },
        ]),
      },
      labelX: courtX(25),
      labelY: courtY(24.2),
    };
  }
  if (zone === "Left Corner 3") {
    return {
      kind: "path",
      attrs: { d: courtRectPath(0, 0, CORNER_THREE_LEFT_X_FT, CORNER_THREE_Y_FT) },
      labelX: courtX(0.65),
      labelY: courtY(7),
      labelAnchor: "start",
      labelClassName: "shot-zone-label shot-zone-label-corner",
    };
  }
  if (zone === "Right Corner 3") {
    return {
      kind: "path",
      attrs: { d: courtRectPath(CORNER_THREE_RIGHT_X_FT, 0, 3, CORNER_THREE_Y_FT) },
      labelX: courtX(49.35),
      labelY: courtY(7),
      labelAnchor: "end",
      labelClassName: "shot-zone-label shot-zone-label-corner",
    };
  }
  if (
    zone === "Above the Break 3" &&
    (area === "Left Side(L)" || area === "Left Side Center(LC)")
  ) {
    return {
      kind: "path",
      attrs: {
        d: courtPath([
          { x: CORNER_THREE_LEFT_X_FT, y: CORNER_THREE_Y_FT },
          { x: CORNER_THREE_LEFT_X_FT, y: HALF_COURT_LENGTH_FT },
          { x: LANE_LEFT_FT, y: HALF_COURT_LENGTH_FT },
          { x: LANE_LEFT_FT, y: leftArcY },
          ...arcBoundaryPoints(LANE_LEFT_FT, CORNER_THREE_LEFT_X_FT),
        ]),
      },
      labelX: courtX(10.5),
      labelY: courtY(34),
    };
  }
  if (
    zone === "Above the Break 3" &&
    (area === "Right Side(R)" || area === "Right Side Center(RC)")
  ) {
    return {
      kind: "path",
      attrs: {
        d: courtPath([
          { x: LANE_RIGHT_FT, y: rightArcY },
          { x: LANE_RIGHT_FT, y: HALF_COURT_LENGTH_FT },
          { x: CORNER_THREE_RIGHT_X_FT, y: HALF_COURT_LENGTH_FT },
          { x: CORNER_THREE_RIGHT_X_FT, y: CORNER_THREE_Y_FT },
          ...arcBoundaryPoints(CORNER_THREE_RIGHT_X_FT, LANE_RIGHT_FT),
        ]),
      },
      labelX: courtX(39.5),
      labelY: courtY(34),
    };
  }
  if (zone === "Above the Break 3") {
    return {
      kind: "path",
      attrs: {
        d: courtPath([
          { x: LANE_LEFT_FT, y: leftArcY },
          { x: LANE_LEFT_FT, y: HALF_COURT_LENGTH_FT },
          { x: LANE_RIGHT_FT, y: HALF_COURT_LENGTH_FT },
          { x: LANE_RIGHT_FT, y: rightArcY },
          ...arcBoundaryPoints(LANE_RIGHT_FT, LANE_LEFT_FT),
        ]),
      },
      labelX: courtX(25),
      labelY: courtY(34),
    };
  }
  if (zone === "Backcourt" && area === "Left Side(L)") {
    return {
      kind: "path",
      attrs: { d: courtRectPath(0, 43, COURT_WIDTH_FT / 3, 4) },
      labelX: courtX(8.3),
      labelY: courtY(45),
    };
  }
  if (zone === "Backcourt" && area === "Right Side(R)") {
    return {
      kind: "path",
      attrs: { d: courtRectPath((COURT_WIDTH_FT * 2) / 3, 43, COURT_WIDTH_FT / 3, 4) },
      labelX: courtX(41.7),
      labelY: courtY(45),
    };
  }
  if (zone === "Backcourt") {
    return {
      kind: "path",
      attrs: { d: courtRectPath(COURT_WIDTH_FT / 3, 43, COURT_WIDTH_FT / 3, 4) },
      labelX: courtX(25),
      labelY: courtY(45),
    };
  }

  const col = fallbackIndex % 4;
  const row = Math.floor(fallbackIndex / 4);
  return {
    kind: "path",
    attrs: { d: courtRectPath(4 + col * 11, 32 - row * 6, 9, 4) },
    labelX: courtX(8.5 + col * 11),
    labelY: courtY(34 - row * 6),
  };
}

function addCourtLine(svg: SVGElement, tag: string, attrs: Record<string, string | number>): void {
  svg.append(svgEl(tag, { ...attrs, class: "shot-chart-line" }));
}

function addCourtPath(svg: SVGElement, d: string, className = "shot-chart-line"): void {
  svg.append(svgEl("path", { d, class: className }));
}

function renderCourtLines(svg: SVGElement): void {
  addCourtLine(svg, "rect", {
    ...courtRectAttrs(0, 0, COURT_WIDTH_FT, HALF_COURT_LENGTH_FT),
    rx: 2,
  });
  addCourtLine(svg, "rect", {
    ...courtRectAttrs(LANE_LEFT_FT, 0, LANE_RIGHT_FT - LANE_LEFT_FT, FREE_THROW_Y_FT),
  });
  addCourtLine(svg, "line", {
    x1: courtX(22),
    y1: courtY(BACKBOARD_Y_FT),
    x2: courtX(28),
    y2: courtY(BACKBOARD_Y_FT),
  });
  addCourtLine(svg, "circle", {
    cx: courtX(HOOP_X_FT),
    cy: courtY(HOOP_Y_FT),
    r: 0.75 * CHART_SCALE,
  });
  addCourtPath(
    svg,
    courtPolyline(arcPoints(HOOP_X_FT, HOOP_Y_FT, 4, 0, 180, 24)),
    "shot-chart-line shot-chart-line-strong",
  );
  addCourtLine(svg, "line", {
    x1: courtX(HOOP_X_FT - 4),
    y1: courtY(HOOP_Y_FT),
    x2: courtX(HOOP_X_FT - 4),
    y2: courtY(BACKBOARD_Y_FT),
  });
  addCourtLine(svg, "line", {
    x1: courtX(HOOP_X_FT + 4),
    y1: courtY(HOOP_Y_FT),
    x2: courtX(HOOP_X_FT + 4),
    y2: courtY(BACKBOARD_Y_FT),
  });
  addCourtPath(svg, courtPolyline(arcPoints(HOOP_X_FT, FREE_THROW_Y_FT, 6, 0, 180, 32)));
  addCourtPath(
    svg,
    courtPolyline(arcPoints(HOOP_X_FT, FREE_THROW_Y_FT, 6, 180, 360, 32)),
    "shot-chart-line shot-chart-line-dashed",
  );
  addCourtLine(svg, "line", {
    x1: courtX(CORNER_THREE_LEFT_X_FT),
    y1: courtY(0),
    x2: courtX(CORNER_THREE_LEFT_X_FT),
    y2: courtY(CORNER_THREE_Y_FT),
  });
  addCourtLine(svg, "line", {
    x1: courtX(CORNER_THREE_RIGHT_X_FT),
    y1: courtY(0),
    x2: courtX(CORNER_THREE_RIGHT_X_FT),
    y2: courtY(CORNER_THREE_Y_FT),
  });
  addCourtPath(
    svg,
    courtPolyline(
      arcPoints(
        HOOP_X_FT,
        HOOP_Y_FT,
        THREE_POINT_RADIUS_FT,
        arcAngleForX(CORNER_THREE_LEFT_X_FT),
        arcAngleForX(CORNER_THREE_RIGHT_X_FT),
        64,
      ),
    ),
    "shot-chart-line shot-chart-line-strong",
  );
  addCourtPath(svg, courtPolyline(arcPoints(HOOP_X_FT, HALF_COURT_LENGTH_FT, 6, 180, 360, 32)));
  addCourtLine(svg, "line", { x1: courtX(0), y1: courtY(28), x2: courtX(3), y2: courtY(28) });
  addCourtLine(svg, "line", {
    x1: courtX(COURT_WIDTH_FT - 3),
    y1: courtY(28),
    x2: courtX(COURT_WIDTH_FT),
    y2: courtY(28),
  });
  for (const y of [7, 8.75, 11.75, 14.75]) {
    addCourtLine(svg, "line", {
      x1: courtX(LANE_LEFT_FT),
      y1: courtY(y),
      x2: courtX(16),
      y2: courtY(y),
    });
    addCourtLine(svg, "line", {
      x1: courtX(LANE_RIGHT_FT),
      y1: courtY(y),
      x2: courtX(34),
      y2: courtY(y),
    });
  }
}

function shotChartDrawIndex(row: AggregatedShotZone): number {
  const order = [
    "Backcourt",
    "Above the Break 3",
    "Left Corner 3",
    "Right Corner 3",
    "Mid-Range",
    "In The Paint (Non-RA)",
    "Restricted Area",
  ];
  const index = order.indexOf(row.zone);
  return index === -1 ? order.length : index;
}

function renderShotChart(rows: AggregatedShotZone[]): HTMLElement {
  const maxAttempts = Math.max(...rows.map((row) => row.attempts), 0);
  const wrapper = el("div", { className: "shot-chart-wrap" });
  const svg = svgEl("svg", {
    class: "shot-chart",
    viewBox: `0 0 ${COURT_WIDTH_FT * CHART_SCALE} ${HALF_COURT_LENGTH_FT * CHART_SCALE}`,
    role: "img",
    "aria-labelledby": "shot-chart-title shot-chart-desc",
  });
  svg.append(svgEl("title", { id: "shot-chart-title" }), svgEl("desc", { id: "shot-chart-desc" }));
  svg.querySelector("title")!.textContent = "All-season shot zone chart";
  svg.querySelector("desc")!.textContent =
    "Half-court chart summarizing player field-goal percentage and volume by shot zone.";

  const labels: SVGElement[] = [];

  [...rows]
    .sort((a, b) => shotChartDrawIndex(a) - shotChartDrawIndex(b))
    .forEach((row, index) => {
      const shape = zoneShape(row.zone, row.area, index);
      const zone = svgEl(shape.kind, {
        ...shape.attrs,
        class: "shot-zone",
        fill: shotZoneColor(row.fg_pct_delta),
        opacity: shotZoneOpacity(row.attempts, maxAttempts),
      });
      const title = svgEl("title");
      title.textContent = `${row.zone}, ${row.area}: ${formatPct(row.fg_pct)} on ${row.attempts} FGA`;
      zone.append(title);
      svg.append(zone);

      const label = svgEl("text", {
        class: shape.labelClassName ?? "shot-zone-label",
        x: shape.labelX,
        y: shape.labelY,
        "text-anchor": shape.labelAnchor ?? "middle",
      });
      label.append(
        svgEl("tspan", { x: shape.labelX, dy: "-0.35em" }),
        svgEl("tspan", { x: shape.labelX, dy: "1.2em" }),
      );
      label.children[0].textContent = formatPct(row.fg_pct);
      label.children[1].textContent = `${row.attempts} FGA`;
      labels.push(label);
    });

  renderCourtLines(svg);
  svg.append(...labels);

  const legend = el("div", { className: "shot-chart-legend" }, [
    el("span", {}, [el("span", { className: "legend-swatch legend-hot" }), "Above league"]),
    el("span", {}, [el("span", { className: "legend-swatch legend-even" }), "Near league"]),
    el("span", {}, [el("span", { className: "legend-swatch legend-cold" }), "Below league"]),
  ]);
  wrapper.append(svg, legend);
  return wrapper;
}

function renderShotZoneSummary(rows: AggregatedShotZone[]): HTMLElement {
  return el("div", { className: "shot-zone-summary" }, [
    renderShotChart(rows),
    renderTable(
      [
        { key: "zone", label: "Zone" },
        { key: "area", label: "Area" },
        { key: "attempts", label: "FGA" },
        { key: "makes", label: "FG" },
        { key: "fg_pct", label: "FG%", format: formatPct },
        { key: "league_fg_pct", label: "Lg FG%", format: formatPct },
        { key: "fg_pct_delta", label: "+/-", format: formatPct },
        { key: "avg_distance", label: "Dist" },
      ],
      rows,
      [
        { label: "Context", span: 2 },
        { label: "Volume", span: 2 },
        { label: "Accuracy", span: 4 },
      ],
    ),
  ]);
}

function renderShotSplits(container: HTMLElement, splits: Row[]): void {
  const aggregated = aggregateShotZones(splits);
  const section = el("section", { className: "stat-tabs-section" }, [
    sectionHeading("player-shooting", "Shooting by zone"),
  ]);
  renderStatTabs(section, "player-shooting", [
    {
      id: "by-season",
      label: "By Season",
      render: () => [
        tableNote(
          "Shot-zone coverage is derived from raw shot coordinates; league average is joined by season and derived zone.",
        ),
        renderTable(
          [
            { key: "season_year", label: "Season" },
            { key: "season_type", label: "Type" },
            { key: "shot_zone_basic", label: "Zone" },
            { key: "shot_zone_area", label: "Area" },
            { key: "attempts", label: "FGA" },
            { key: "makes", label: "FG" },
            { key: "fg_pct", label: "FG%", format: formatPct },
            { key: "league_fg_pct", label: "Lg FG%", format: formatPct },
            { key: "avg_distance", label: "Dist" },
          ],
          splits,
          [
            { label: "Context", span: 4 },
            { label: "Volume", span: 2 },
            { label: "Accuracy", span: 3 },
          ],
        ),
      ],
    },
    {
      id: "all-seasons-chart",
      label: "All Seasons Chart",
      render: () => [
        tableNote(
          "All seasons are aggregated by derived zone and area; league average is weighted by the player's attempts in each season-zone row.",
        ),
        renderShotZoneSummary(aggregated),
      ],
    },
  ]);
  container.append(section);
}

function renderOnOff(container: HTMLElement, rows: Row[]): void {
  container.append(
    el("section", {}, [
      sectionHeading("player-on-off", "On/off court splits"),
      tableNote("Tracking-era split table; older seasons may not have on/off coverage."),
      renderTable(
        [
          { key: "season_year", label: "Season" },
          { key: "season_type", label: "Type" },
          { key: "on_off", label: "On/Off" },
          { key: "gp", label: "G" },
          { key: "off_rating", label: "ORtg" },
          { key: "def_rating", label: "DRtg" },
          { key: "net_rating", label: "NetRtg" },
        ],
        rows,
      ),
    ]),
  );
}

function renderCombine(container: HTMLElement, combine: Row): void {
  container.append(
    el("section", {}, [
      sectionHeading("player-combine", "Draft combine measurements"),
      renderDefList([
        [
          "Height (no shoes)",
          combine.height_wo_shoes ? `${formatValue(combine.height_wo_shoes)}"` : null,
        ],
        [
          "Height (with shoes)",
          combine.height_w_shoes ? `${formatValue(combine.height_w_shoes)}"` : null,
        ],
        ["Weight", combine.weight ? `${formatValue(combine.weight)} lb` : null],
        ["Wingspan", combine.wingspan ? `${formatValue(combine.wingspan)}"` : null],
        [
          "Standing reach",
          combine.standing_reach ? `${formatValue(combine.standing_reach)}"` : null,
        ],
        ["Body fat %", combine.body_fat_pct],
        [
          "Standing vertical leap",
          combine.standing_vertical_leap ? `${formatValue(combine.standing_vertical_leap)}"` : null,
        ],
        [
          "Max vertical leap",
          combine.max_vertical_leap ? `${formatValue(combine.max_vertical_leap)}"` : null,
        ],
        ["Lane agility time", combine.lane_agility_time],
        ["Three-quarter sprint", combine.three_quarter_sprint],
        ["Bench press (reps)", combine.bench_press],
      ]),
    ]),
  );
}

function renderSimilarPlayers(
  container: HTMLElement,
  rows: Row[],
  onSelect: (playerId: string) => void,
): void {
  const section = el("section", {}, [sectionHeading("player-similar", "Similar players")]);
  const list = el("ul", { className: "result-list" });
  for (const r of rows) {
    const sub = `${formatValue(r.ppg)} PPG, ${formatValue(r.rpg)} RPG, ${formatValue(r.apg)} APG`;
    const button = el("button", { type: "button", className: "result-row" }, [
      el("div", { className: "result-row-text" }, [
        el("span", { text: String(r.full_name) }),
        el("span", { className: "muted", text: sub }),
      ]),
    ]);
    button.addEventListener("click", () => onSelect(String(r.player_id)));
    list.append(el("li", {}, [button]));
  }
  section.append(list);
  container.append(section);
}
