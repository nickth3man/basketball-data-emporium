type Props = Record<string, unknown>;

function attributeValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return formatValue(value);
}

/** Tiny DOM-builder helper. Avoids innerHTML so values pulled from the
 *  database (which include raw scraped HTML page titles in some tables)
 *  can never be interpreted as markup. */
export function el(tag: string, props: Props = {}, children: (Node | string)[] = []): HTMLElement {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null) continue;
    if (key === "text") node.textContent = attributeValue(value);
    else if (key === "className") node.className = attributeValue(value);
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value as EventListener);
    } else if (
      typeof value === "string" ||
      typeof value === "number" ||
      typeof value === "boolean"
    ) {
      node.setAttribute(key, String(value));
    } else {
      node.setAttribute(key, attributeValue(value));
    }
  }
  for (const child of children) node.append(child);
  return node;
}

let liveRegion: HTMLElement | null = null;

/** Screen-reader announcements for async status updates and tab switches. */
export function announceStatus(message: string): void {
  if (!liveRegion) {
    liveRegion = el("div", {
      className: "visually-hidden",
      "aria-live": "polite",
      "aria-atomic": "true",
    });
    document.body.append(liveRegion);
  }
  liveRegion.textContent = message;
}

export function navigateToDetail(tab: string, id?: string): void {
  window.dispatchEvent(new CustomEvent("nba:navigate", { detail: { tab, id } }));
}

/** Standard inline loading indicator (spinner is pure CSS on `.loading`). */
export function loadingEl(label = "Loading…"): HTMLElement {
  return el("p", { className: "loading", text: label });
}

/** Standard error alert for failed fetches. */
export function errorEl(message: string): HTMLElement {
  return el("p", {
    className: "alert-error",
    role: "alert",
    text: `Couldn't load data: ${message}`,
  });
}

export function pageHeader(title: string, description?: string, eyebrow?: string): HTMLElement {
  return el(
    "div",
    { className: "page-header" },
    [
      eyebrow ? el("p", { className: "page-kicker", text: eyebrow }) : null,
      el("h2", { className: "page-title", text: title }),
      description ? el("p", { className: "page-description", text: description }) : null,
    ].filter((node): node is HTMLElement => node !== null),
  );
}

let controlIdCounter = 0;

function nextControlId(prefix: string): string {
  controlIdCounter += 1;
  return `${prefix}-${controlIdCounter}`;
}

/** Wraps a control with a visually-hidden label for screen readers. */
export function labeledControl(label: string, control: HTMLElement, id?: string): HTMLElement {
  const controlId = id ?? nextControlId("control");
  control.id = controlId;
  return el("div", { className: "labeled-control" }, [
    el("label", { className: "control-label", for: controlId, text: label }),
    control,
  ]);
}

export function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(1);
  if (typeof value === "string") return value;
  if (typeof value === "bigint") return String(value);
  return "—";
}

export function formatPct(value: unknown): string {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(1)}%`;
}

export interface Column {
  key: string;
  label: string;
  /** Accessible header text when `label` is empty (e.g. icon-only columns). */
  headerLabel?: string;
  align?: "left" | "right";
  format?: (value: unknown, row: Record<string, unknown>) => string;
  render?: (value: unknown, row: Record<string, unknown>) => Node | string;
}

export interface ColumnGroup {
  label: string;
  span: number;
}

function cellValue(column: Column, row: Record<string, unknown>): Node | string {
  if (column.render) return column.render(row[column.key], row);
  return column.format ? column.format(row[column.key], row) : formatValue(row[column.key]);
}

export function cellButton(label: string, onClick: () => void, ariaLabel = label): HTMLElement {
  const button = el("button", {
    type: "button",
    className: "cell-link",
    text: label,
    "aria-label": ariaLabel,
  });
  button.addEventListener("click", onClick);
  return button;
}

/** Renders a player-name cell as a button that navigates to that player's
 *  profile. Shared by views that need a clickable player column (team
 *  rosters, franchise leaders, league-leader tables). Falls back to a plain
 *  text label when the row has no usable `player_id` or the value is empty. */
export function playerCell(value: unknown, row: Record<string, unknown>): Node | string {
  const label = formatValue(value);
  const playerId = Number(row.player_id);
  if (!Number.isFinite(playerId) || label === "—") return label;
  return cellButton(
    label,
    () => navigateToDetail("players", String(playerId)),
    `${label} player profile`,
  );
}

/** Renders a team cell as a button that navigates to that team's profile. */
export function teamCell(value: unknown, row: Record<string, unknown>): Node | string {
  const label = formatValue(value);
  const teamId = Number(row.team_id);
  if (!Number.isFinite(teamId) || label === "—") return label;
  const teamName = formatValue(row.team_name);
  const ariaName = teamName === "—" ? label : teamName;
  return cellButton(
    label,
    () => navigateToDetail("teams", String(teamId)),
    `${ariaName} team profile`,
  );
}

/** Renders a game-id cell as a "Box" button that opens the hidden per-game
 *  box-score tab. Shared by the recent-games tables on player and team
 *  profiles. Renders nothing when the row has no game id (e.g. seasons the
 *  game dimension doesn't cover). */
export function boxScoreCell(value: unknown): Node | string {
  const gameId = typeof value === "string" || typeof value === "number" ? String(value) : "";
  if (!/^\d{8,10}$/.test(gameId)) return "";
  return cellButton(
    "Box",
    () => navigateToDetail("game", gameId.padStart(10, "0")),
    "Open box score",
  );
}

function isNumericText(value: string): boolean {
  const trimmed = value.trim();
  return trimmed === "—" || /^-?\d+(\.\d+)?%?$/.test(trimmed);
}

export function renderTable(
  columns: Column[],
  rows: Record<string, unknown>[],
  columnGroups: ColumnGroup[] = [],
): HTMLElement {
  if (rows.length === 0) {
    return el("p", { className: "empty-state", text: "No data for this selection." });
  }
  const renderedRows = rows.map((row) => columns.map((column) => cellValue(column, row)));
  const numericColumns = columns.map((column, index) => {
    if (column.align) return column.align === "right";
    if (column.render) return false;
    const values = renderedRows.map((row) => row[index]).filter((value) => value !== "—");
    return (
      values.length > 0 &&
      values.every((value) => typeof value === "string" && isNumericText(value))
    );
  });
  const groupRow =
    columnGroups.length > 0
      ? el(
          "tr",
          {},
          columnGroups.map((group) =>
            el("th", { scope: "colgroup", colspan: group.span, text: group.label }),
          ),
        )
      : null;
  const headRow = el(
    "tr",
    {},
    columns.map((c, index) => {
      const props: Props = { scope: "col" };
      if (numericColumns[index]) props.className = "numeric";
      if (c.label) props.text = c.label;
      else if (c.headerLabel) props["aria-label"] = c.headerLabel;
      return el("th", props);
    }),
  );
  const body = renderedRows.map((row) =>
    el(
      "tr",
      {},
      row.map((value, index) => {
        const props: Props = {};
        if (numericColumns[index]) props.className = "numeric";
        const td = el("td", props);
        td.append(value);
        return td;
      }),
    ),
  );
  const table = el("table", { className: "result" }, [
    el("thead", {}, groupRow ? [groupRow, headRow] : [headRow]),
    el("tbody", {}, body),
  ]);
  return el("div", { className: "table-scroll" }, [table]);
}

export function renderDefList(pairs: [string, unknown][]): HTMLElement {
  const dl = el("dl", { className: "bio" });
  for (const [label, value] of pairs) {
    dl.append(el("dt", { text: label }), el("dd", { text: formatValue(value) }));
  }
  return dl;
}

export function sectionHeading(id: string, text: string): HTMLElement {
  return el("h3", { id, text });
}

export function tableNote(text: string): HTMLElement {
  return el("p", { className: "table-note", text });
}

type JumpNavItem = [label: string, id: string] | null;

export function renderJumpNav(container: HTMLElement, items: JumpNavItem[]): void {
  const links = items
    .filter((item): item is [label: string, id: string] => item !== null)
    .map(([label, id]) => el("a", { href: `#${id}`, text: label }));
  container.replaceChildren(...links);
}

/** Player headshot with graceful fallback. The server-side photo proxy
 *  (`/api/players/:id/photo`) returns a real 404 when NBA's CDN has no real
 *  photo for this id (it otherwise serves a generic silhouette with a 200,
 *  which the proxy detects and normalizes away), so a plain <img onerror>
 *  is enough to show our own placeholder instead of NBA's stock graphic. */
export function playerPhoto(playerId: unknown, sizeClass: string, altText?: string): HTMLElement {
  const wrapper = el("div", { className: `player-photo ${sizeClass}` });
  const alt = altText?.trim() ? altText.trim() : "";
  const img = el("img", {
    src: `/api/players/${encodeURIComponent(formatValue(playerId))}/photo`,
    alt,
    loading: "lazy",
  }) as HTMLImageElement;
  img.addEventListener("error", () => {
    wrapper.replaceChildren();
    wrapper.classList.add("player-photo-empty");
  });
  wrapper.append(img);
  return wrapper;
}

/** Team logo from the NBA's official CDN, with an abbreviation fallback if the
 *  logo fails to load (offline, defunct team, or missing id). */
export function teamLogo(
  teamId: unknown,
  abbreviation: string,
  sizeClass = "team-logo-md",
  altText?: string,
): HTMLElement {
  const wrapper = el("div", { className: `team-logo ${sizeClass}` });
  const id = Number(teamId);
  const alt = altText?.trim() ? altText.trim() : `${abbreviation} logo`;
  if (!Number.isFinite(id)) {
    wrapper.classList.add("team-logo-fallback");
    wrapper.textContent = abbreviation;
    wrapper.setAttribute("aria-label", alt);
    return wrapper;
  }
  const img = el("img", {
    src: `https://cdn.nba.com/logos/nba/${id}/global/L/logo.svg`,
    alt,
    loading: "lazy",
  }) as HTMLImageElement;
  img.addEventListener("error", () => {
    wrapper.replaceChildren();
    wrapper.classList.add("team-logo-fallback");
    wrapper.textContent = abbreviation;
  });
  wrapper.append(img);
  return wrapper;
}

const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(tag: string, attrs: Record<string, string>): SVGElement {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

/** Small jersey-shaped chip showing a number, colored by team. Team/year-range
 *  is exposed via aria-label and keyboard focus (not hover-only). Colors are
 *  resolved server-side (era-accurate per the stint's years) — see
 *  `web/server/teamColorEras.ts`. */
export function jerseyIcon(
  number: string,
  primary: string,
  trim: string,
  tooltip: string,
): HTMLElement {
  const svg = svgEl("svg", { viewBox: "0 0 40 46", class: "jersey-icon", "aria-hidden": "true" });
  const path = svgEl("path", {
    d: "M8,4 L16,4 Q20,9 24,4 L32,4 L38,12 L31,17 L31,42 Q20,45 9,42 L9,17 L2,12 Z",
    fill: primary,
    stroke: trim,
    "stroke-width": "2",
    "stroke-linejoin": "round",
  });
  const text = svgEl("text", {
    x: "20",
    y: "31",
    "text-anchor": "middle",
    "font-size": "13",
    "font-weight": "700",
    fill: "#ffffff",
  });
  text.textContent = number;

  svg.append(path, text);
  return el(
    "div",
    {
      className: "jersey-chip",
      role: "img",
      "aria-label": `${tooltip}, jersey number ${number}`,
      tabindex: "0",
    },
    [svg],
  );
}

export function select(options: { value: string; label: string }[]): HTMLSelectElement {
  const node = document.createElement("select");
  for (const opt of options) {
    const optionEl = document.createElement("option");
    optionEl.value = opt.value;
    optionEl.textContent = opt.label;
    node.append(optionEl);
  }
  return node;
}

/** Builds a labelled `<select>` wrapped for accessibility. */
export function labeledSelect(
  label: string,
  options: { value: string; label: string }[],
  id?: string,
): { wrapper: HTMLElement; select: HTMLSelectElement } {
  const selectEl = select(options);
  const wrapper = labeledControl(label, selectEl, id);
  return { wrapper, select: selectEl };
}

/** Builds a labelled search `<input>` wrapped for accessibility. */
export function labeledSearch(
  label: string,
  placeholder: string,
  className = "search-box",
  id?: string,
): { wrapper: HTMLElement; input: HTMLInputElement } {
  const input = el("input", { type: "search", placeholder, className }) as HTMLInputElement;
  const wrapper = labeledControl(label, input, id);
  return { wrapper, input };
}
