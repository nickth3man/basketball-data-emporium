import { api, type Badge, type JerseyStint, type Row } from "../api.ts";
import {
  announceStatus,
  el,
  formatPct,
  formatValue,
  jerseyIcon,
  labeledSearch,
  playerPhoto,
  renderDefList,
  renderTable,
} from "../dom.ts";

export function renderPlayers(container: HTMLElement): void {
  const { wrapper: searchWrapper, input: searchBox } = labeledSearch(
    "Search players by name",
    "Search players by name…",
    "search-box",
    "players-search",
  );
  const resultsList = el("ul", { className: "result-list" });
  const detail = el("div", { className: "detail" });

  container.append(el("div", { className: "search-panel" }, [searchWrapper, resultsList]), detail);

  let debounce: number | undefined;
  let currentSearchController: AbortController | null = null;
  searchBox.addEventListener("input", () => {
    window.clearTimeout(debounce);
    debounce = window.setTimeout(() => void runSearch(searchBox.value.trim()), 200);
  });

  async function runSearch(query: string): Promise<void> {
    // Abort any in-flight search before starting a new one: without this, a
    // slow response for an earlier keystroke (e.g. "Wilt Chamberla…") can
    // resolve after a faster response for the final query and overwrite it,
    // intermittently showing "No players found" for a query that actually
    // has matches (a race condition, not a search-quality bug).
    currentSearchController?.abort();
    const controller = new AbortController();
    currentSearchController = controller;

    resultsList.replaceChildren();
    if (query.length < 2) return;
    announceStatus("Searching players…");
    try {
      const players = await api.searchPlayers(query, controller.signal);
      if (controller.signal.aborted) return;
      if (players.length === 0) {
        resultsList.append(el("li", { className: "muted", text: "No players found." }));
        announceStatus("No players found.");
        return;
      }
      announceStatus(`${players.length} player${players.length === 1 ? "" : "s"} found.`);
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
      if (controller.signal.aborted) return;
      const message = err instanceof Error ? err.message : "Search failed.";
      resultsList.append(el("li", { className: "muted", text: `Error: ${message}` }));
      announceStatus(`Player search failed: ${message}`);
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
      if (profile.career) renderStatsSummary(detail, profile.career, profile.careerEfgPct);
      if (profile.seasons.length > 0) renderSeasons(detail, profile.seasons);
      if (profile.awards.length > 0) renderAwards(detail, profile.awards);
      announceStatus(`Loaded profile for ${String(bio.full_name)}.`);

      // Secondary sections load independently of the main profile — each is
      // its own endpoint/table, so one failing or being empty (e.g. a player
      // with no combine data) shouldn't block the others.
      const [highs, rates, shotSplits, onOff, combine, similar] = await Promise.allSettled([
        api.getPlayerHighs(id),
        api.getPlayerRates(id),
        api.getPlayerShotSplits(id),
        api.getPlayerOnOff(id),
        api.getPlayerCombine(id),
        api.getSimilarPlayers(id),
      ]);
      if (highs.status === "fulfilled" && highs.value.length > 0) renderHighs(detail, highs.value);
      if (rates.status === "fulfilled")
        renderPerRates(detail, rates.value.per36, rates.value.per48);
      if (shotSplits.status === "fulfilled" && shotSplits.value.length > 0)
        renderShotSplits(detail, shotSplits.value);
      if (onOff.status === "fulfilled" && onOff.value.length > 0) renderOnOff(detail, onOff.value);
      if (combine.status === "fulfilled" && combine.value) renderCombine(detail, combine.value);
      if (similar.status === "fulfilled" && similar.value.length > 0)
        renderSimilarPlayers(detail, similar.value, (pid) => void showPlayer(pid));
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

  const infoLines = [
    bioLine("Position", bio.position),
    heightWeight ? el("p", { className: "bio-line" }, [`${heightWeight}${metric}`]) : null,
    bornLine(birthDate, bio.country),
    bioLine("School", bio.school),
    bioLine("Draft", formatDraftLine(draft)),
    hallOfFameYear ? bioLine("Hall of Fame", `Inducted in ${hallOfFameYear}`) : null,
    bioLine("Career Length", bio.season_exp ? `${formatValue(bio.season_exp)} years` : null),
  ].filter((n): n is HTMLElement => n !== null);

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
  );
}

function renderSeasons(container: HTMLElement, seasons: Row[]): void {
  container.append(
    el("h3", { text: "Season by season" }),
    renderTable(
      [
        { key: "season_year", label: "Season" },
        { key: "season_type", label: "Type" },
        { key: "team_abbreviation", label: "Team" },
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

function renderAwards(container: HTMLElement, awards: Row[]): void {
  container.append(
    el("h3", { text: "Awards" }),
    renderTable(
      [
        { key: "season", label: "Season" },
        { key: "award_type", label: "Award" },
        { key: "description", label: "Detail" },
      ],
      awards,
    ),
  );
}

function renderHighs(container: HTMLElement, highs: Row[]): void {
  container.append(
    el("h3", { text: "Career highs" }),
    renderTable(
      [
        { key: "stat", label: "Stat" },
        { key: "value", label: "Value" },
        { key: "game_date", label: "Date", format: (v) => String(v).slice(0, 10) },
        { key: "team_abbreviation", label: "Team" },
      ],
      highs,
    ),
  );
}

function renderPerRates(container: HTMLElement, per36: Row[], per48: Row[]): void {
  if (per36.length > 0) {
    container.append(
      el("h3", { text: "Per 36 minutes" }),
      renderTable(
        [
          { key: "season_year", label: "Season" },
          { key: "season_type", label: "Type" },
          { key: "gp", label: "GP" },
          { key: "pts_per36", label: "PTS" },
          { key: "reb_per36", label: "TRB" },
          { key: "ast_per36", label: "AST" },
          { key: "stl_per36", label: "STL" },
          { key: "blk_per36", label: "BLK" },
          { key: "tov_per36", label: "TOV" },
        ],
        per36,
      ),
    );
  }
  if (per48.length > 0) {
    container.append(
      el("h3", { text: "Per 48 minutes" }),
      renderTable(
        [
          { key: "season_year", label: "Season" },
          { key: "season_type", label: "Type" },
          { key: "gp", label: "GP" },
          { key: "pts_per48", label: "PTS" },
          { key: "reb_per48", label: "TRB" },
          { key: "ast_per48", label: "AST" },
          { key: "stl_per48", label: "STL" },
          { key: "blk_per48", label: "BLK" },
          { key: "tov_per48", label: "TOV" },
        ],
        per48,
      ),
    );
  }
}

function renderShotSplits(container: HTMLElement, splits: Row[]): void {
  container.append(
    el("h3", { text: "Shooting by zone" }),
    renderTable(
      [
        { key: "season_year", label: "Season" },
        { key: "shot_zone_basic", label: "Zone" },
        { key: "shot_zone_area", label: "Area" },
        { key: "attempts", label: "FGA" },
        { key: "makes", label: "FGM" },
        { key: "fg_pct", label: "FG%", format: formatPct },
        { key: "league_fg_pct", label: "Lg Avg FG%", format: formatPct },
        { key: "avg_distance", label: "Avg Dist (ft)" },
      ],
      splits,
    ),
  );
}

function renderOnOff(container: HTMLElement, rows: Row[]): void {
  container.append(
    el("h3", { text: "On/off court splits" }),
    renderTable(
      [
        { key: "season_year", label: "Season" },
        { key: "season_type", label: "Type" },
        { key: "on_off", label: "On/Off" },
        { key: "gp", label: "GP" },
        { key: "off_rating", label: "OffRtg" },
        { key: "def_rating", label: "DefRtg" },
        { key: "net_rating", label: "NetRtg" },
      ],
      rows,
    ),
  );
}

function renderCombine(container: HTMLElement, combine: Row): void {
  container.append(
    el("h3", { text: "Draft combine measurements" }),
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
      ["Standing reach", combine.standing_reach ? `${formatValue(combine.standing_reach)}"` : null],
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
  );
}

function renderSimilarPlayers(
  container: HTMLElement,
  rows: Row[],
  onSelect: (playerId: string) => void,
): void {
  container.append(el("h3", { text: "Similar players" }));
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
  container.append(list);
}
