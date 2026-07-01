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
    el("label", { className: "visually-hidden", for: controlId, text: label }),
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
  format?: (value: unknown, row: Record<string, unknown>) => string;
}

export function renderTable(columns: Column[], rows: Record<string, unknown>[]): HTMLElement {
  if (rows.length === 0) {
    return el("p", { className: "muted", text: "No rows." });
  }
  const headRow = el(
    "tr",
    {},
    columns.map((c) => {
      const props: Props = { scope: "col" };
      if (c.label) props.text = c.label;
      else if (c.headerLabel) props["aria-label"] = c.headerLabel;
      return el("th", props);
    }),
  );
  const body = rows.map((row) =>
    el(
      "tr",
      {},
      columns.map((c) =>
        el("td", { text: c.format ? c.format(row[c.key], row) : formatValue(row[c.key]) }),
      ),
    ),
  );
  const table = el("table", { className: "result" }, [
    el("thead", {}, [headRow]),
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
