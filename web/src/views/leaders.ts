import { api, type Row } from "../api.ts";
import {
  announceStatus,
  el,
  formatValue,
  labeledControl,
  labeledSelect,
  playerCell,
  renderTable,
  type Column,
} from "../dom.ts";

const SEASON_TYPE_OPTIONS = [
  { value: "Regular", label: "Regular Season" },
  { value: "Playoffs", label: "Playoffs" },
  { value: "Combined", label: "Combined" },
];

const ALL_TIME_STAT_OPTIONS = [
  { value: "pts", label: "PTS" },
  { value: "ast", label: "AST" },
  { value: "reb", label: "TRB" },
];

const STAT_KEY_GROUPS: { category: string; keys: { value: string; label: string }[] }[] = [
  {
    category: "Scoring",
    keys: [
      { value: "pts", label: "PTS" },
      { value: "fg", label: "FG" },
      { value: "fga", label: "FGA" },
      { value: "2p", label: "2P" },
      { value: "2pa", label: "2PA" },
      { value: "tp", label: "3P" },
      { value: "tpa", label: "3PA" },
      { value: "ft", label: "FT" },
      { value: "fta", label: "FTA" },
      { value: "ftr", label: "FTr" },
      { value: "tpar", label: "3PAr" },
    ],
  },
  {
    category: "Rebounds",
    keys: [
      { value: "trb", label: "TRB" },
      { value: "orb", label: "ORB" },
      { value: "drb", label: "DRB" },
    ],
  },
  {
    category: "Assists",
    keys: [{ value: "ast", label: "AST" }],
  },
  {
    category: "Shooting",
    keys: [
      { value: "fgp", label: "FG%" },
      { value: "2pp", label: "2P%" },
      { value: "tpp", label: "3P%" },
      { value: "ftp", label: "FT%" },
      { value: "efg", label: "eFG%" },
      { value: "tsp", label: "TS%" },
    ],
  },
  {
    category: "Advanced",
    keys: [
      { value: "min", label: "MIN" },
      { value: "gp", label: "GP" },
      { value: "gs", label: "GS" },
      { value: "per", label: "PER" },
      { value: "ws", label: "WS" },
      { value: "ws48", label: "WS/48" },
      { value: "ows", label: "OWS" },
      { value: "dws", label: "DWS" },
      { value: "bpm", label: "BPM" },
      { value: "obpm", label: "OBPM" },
      { value: "dbpm", label: "DBPM" },
      { value: "vorp", label: "VORP" },
      { value: "ortg", label: "ORtg" },
      { value: "drtg", label: "DRtg" },
      { value: "usgp", label: "USG%" },
      { value: "tovp", label: "TOV%" },
      { value: "astp", label: "AST%" },
      { value: "stlp", label: "STL%" },
      { value: "blkp", label: "BLK%" },
      { value: "orbp", label: "ORB%" },
      { value: "drbp", label: "DRB%" },
      { value: "trbp", label: "TRB%" },
      { value: "ewa", label: "EWA" },
      { value: "pm100", label: "+/-" },
      { value: "onOff100", label: "On-Off" },
    ],
  },
  {
    category: "Hustle",
    keys: [
      { value: "stl", label: "STL" },
      { value: "blk", label: "BLK" },
      { value: "pf", label: "PF" },
      { value: "ba", label: "BA" },
      { value: "qd", label: "QD" },
      { value: "fxf", label: "FxF" },
    ],
  },
  {
    category: "Ratings",
    keys: [
      { value: "hgt", label: "HGT" },
      { value: "stre", label: "STR" },
      { value: "spd", label: "SPD" },
      { value: "jmp", label: "JMP" },
      { value: "endu", label: "END" },
      { value: "ins", label: "INS" },
      { value: "dnk", label: "DNK" },
      { value: "pss", label: "PAS" },
      { value: "oiq", label: "OIQ" },
      { value: "diq", label: "DIQ" },
      { value: "reb", label: "REB" },
      { value: "ovr", label: "OVR" },
      { value: "pot", label: "POT" },
    ],
  },
  {
    category: "Other",
    keys: [
      { value: "age", label: "Age" },
      { value: "td", label: "TD" },
    ],
  },
];

const PERCENT_KEYS = new Set([
  "fgp",
  "2pp",
  "tpp",
  "ftp",
  "efg",
  "tsp",
  "ftr",
  "tpar",
  "astp",
  "stlp",
  "blkp",
  "orbp",
  "drbp",
  "trbp",
  "usgp",
  "tovp",
]);

function formatStatValue(statKey: string, value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (statKey === "ws48") {
    const n = Number(value);
    return Number.isFinite(n) ? n.toFixed(3) : "—";
  }
  if (PERCENT_KEYS.has(statKey)) {
    const n = Number(value);
    return Number.isFinite(n) ? `${n.toFixed(1)}%` : "—";
  }
  return formatValue(value);
}

function findStatLabel(statKey: string): string {
  for (const group of STAT_KEY_GROUPS) {
    const match = group.keys.find((k) => k.value === statKey);
    if (match) return match.label;
  }
  return statKey.toUpperCase();
}

function buildSeasonStatSelect(supportedKeys: string[]): HTMLSelectElement {
  const select = document.createElement("select");
  const supported = new Set(supportedKeys);
  const mappedKeys = new Set<string>();

  for (const group of STAT_KEY_GROUPS) {
    const options = group.keys.filter((k) => supported.has(k.value));
    if (options.length === 0) continue;
    const optgroup = document.createElement("optgroup");
    optgroup.label = group.category;
    for (const opt of options) {
      mappedKeys.add(opt.value);
      const option = document.createElement("option");
      option.value = opt.value;
      option.textContent = opt.label;
      optgroup.append(option);
    }
    select.append(optgroup);
  }

  const unmapped = supportedKeys.filter((k) => !mappedKeys.has(k));
  if (unmapped.length > 0) {
    const optgroup = document.createElement("optgroup");
    optgroup.label = "Other";
    for (const key of unmapped) {
      const option = document.createElement("option");
      option.value = key;
      option.textContent = key.toUpperCase();
      optgroup.append(option);
    }
    select.append(optgroup);
  }

  const defaultOption = select.querySelector<HTMLOptionElement>(`option[value="pts"]`);
  if (defaultOption) select.value = "pts";
  else if (select.options.length > 0) select.selectedIndex = 0;

  return select;
}

function setBusy(container: HTMLElement, busy: boolean): void {
  container.setAttribute("aria-busy", busy ? "true" : "false");
}

export async function renderLeaders(container: HTMLElement): Promise<void> {
  const seasonSection = el("section", { className: "subsection" });
  const allTimeSection = el("section", { className: "subsection" });
  container.append(seasonSection, allTimeSection);
  await Promise.all([renderSeasonSection(seasonSection), renderAllTimeSection(allTimeSection)]);
}

async function renderSeasonSection(container: HTMLElement): Promise<void> {
  container.append(
    el("h2", { text: "Season Leaders" }),
    el("p", { className: "muted", text: "Loading…" }),
  );
  announceStatus("Loading season leaders…");

  try {
    const [seasons, supportedKeys] = await Promise.all([
      api.listLeaderSeasons(),
      api.listLeaderStatKeys(),
    ]);

    container.replaceChildren(el("h2", { text: "Season Leaders" }));

    const latestSeason = seasons[0] ?? "";
    const { wrapper: seasonWrapper, select: seasonSelect } = labeledSelect(
      "Season",
      seasons.map((s) => ({ value: s, label: s })),
      "leaders-season",
    );
    seasonSelect.value = latestSeason;

    const { wrapper: typeWrapper, select: seasonTypeSelect } = labeledSelect(
      "Season type",
      SEASON_TYPE_OPTIONS,
      "leaders-season-type",
    );

    const statSelect = buildSeasonStatSelect(supportedKeys);
    const statWrapper = labeledControl("Stat", statSelect, "leaders-stat");

    const resultDiv = el("div", {
      "aria-live": "polite",
      "aria-atomic": "false",
      "aria-busy": "false",
    });

    container.append(
      el("div", { className: "controls" }, [seasonWrapper, typeWrapper, statWrapper]),
      resultDiv,
    );

    async function load(): Promise<void> {
      setBusy(resultDiv, true);
      resultDiv.replaceChildren(el("p", { className: "muted", text: "Loading…" }));
      announceStatus("Loading season leaders…");

      try {
        if (seasonTypeSelect.value !== "Regular") {
          resultDiv.replaceChildren(
            el("p", {
              className: "muted",
              text: "Playoff and combined season leaders are not yet available.",
            }),
          );
          announceStatus("Playoff and combined season leaders are not yet available.");
          setBusy(resultDiv, false);
          return;
        }

        const statKey = statSelect.value;
        const rows = await api.getSeasonLeaders(seasonSelect.value, statKey);
        resultDiv.replaceChildren();

        if (rows.length === 0) {
          resultDiv.append(el("p", { className: "muted", text: "No leaders for this selection." }));
          announceStatus("No leaders for this selection.");
          setBusy(resultDiv, false);
          return;
        }

        const valueLabel = findStatLabel(statKey);
        const columns: Column[] = [
          { key: "stat_rank", label: "#", align: "right" },
          { key: "full_name", label: "Player", render: playerCell },
          { key: "team_abbreviation", label: "Team", format: formatValue },
          {
            key: "stat_value",
            label: valueLabel,
            align: "right",
            format: (_v, row) => formatStatValue(statKey, row.stat_value),
          },
        ];

        resultDiv.append(renderTable(columns, rows as unknown as Row[]));
        announceStatus(`Loaded ${seasonSelect.value} ${valueLabel.toLowerCase()} season leaders.`);
      } catch (err) {
        const message = err instanceof Error ? err.message : "Failed to load season leaders.";
        resultDiv.replaceChildren(el("p", { className: "muted", text: `Error: ${message}` }));
        announceStatus(`Failed to load season leaders: ${message}`);
      } finally {
        setBusy(resultDiv, false);
      }
    }

    seasonSelect.addEventListener("change", () => void load());
    seasonTypeSelect.addEventListener("change", () => void load());
    statSelect.addEventListener("change", () => void load());

    await load();
  } catch (err) {
    const message = err instanceof Error ? err.message : "Failed to load season leader metadata.";
    container.replaceChildren(
      el("h2", { text: "Season Leaders" }),
      el("p", { className: "muted", text: `Error: ${message}` }),
    );
    announceStatus(`Failed to load season leader metadata: ${message}`);
  }
}

async function renderAllTimeSection(container: HTMLElement): Promise<void> {
  container.append(
    el("h2", { text: "All-Time Leaders" }),
    el("p", { className: "muted", text: "Loading…" }),
  );
  announceStatus("Loading all-time leaders…");

  try {
    container.replaceChildren(el("h2", { text: "All-Time Leaders" }));

    const { wrapper: statWrapper, select: statSelect } = labeledSelect(
      "Stat",
      ALL_TIME_STAT_OPTIONS,
      "leaders-all-time-stat",
    );
    statSelect.value = "pts";

    const resultDiv = el("div", {
      "aria-live": "polite",
      "aria-atomic": "false",
      "aria-busy": "false",
    });

    container.append(el("div", { className: "controls" }, [statWrapper]), resultDiv);

    async function load(): Promise<void> {
      setBusy(resultDiv, true);
      resultDiv.replaceChildren(el("p", { className: "muted", text: "Loading…" }));
      announceStatus("Loading all-time leaders…");

      try {
        const statKey = statSelect.value as "pts" | "ast" | "reb";
        const rows = await api.getAllTimeLeaders(statKey);
        resultDiv.replaceChildren();

        if (rows.length === 0) {
          resultDiv.append(el("p", { className: "muted", text: "No leaders for this selection." }));
          announceStatus("No leaders for this selection.");
          setBusy(resultDiv, false);
          return;
        }

        const valueLabel = findStatLabel(statKey);
        const columns: Column[] = [
          { key: "stat_rank", label: "#", align: "right" },
          { key: "full_name", label: "Player", render: playerCell },
          {
            key: "stat_value",
            label: valueLabel,
            align: "right",
            format: (_v, row) => formatStatValue(statKey, row.stat_value),
          },
        ];

        resultDiv.append(renderTable(columns, rows as unknown as Row[]));
        announceStatus(`Loaded ${valueLabel} all-time leaders.`);
      } catch (err) {
        const message = err instanceof Error ? err.message : "Failed to load all-time leaders.";
        resultDiv.replaceChildren(el("p", { className: "muted", text: `Error: ${message}` }));
        announceStatus(`Failed to load all-time leaders: ${message}`);
      } finally {
        setBusy(resultDiv, false);
      }
    }

    statSelect.addEventListener("change", () => void load());

    await load();
  } catch (err) {
    const message = err instanceof Error ? err.message : "Failed to load all-time leaders.";
    container.replaceChildren(
      el("h2", { text: "All-Time Leaders" }),
      el("p", { className: "muted", text: `Error: ${message}` }),
    );
    announceStatus(`Failed to load all-time leaders: ${message}`);
  }
}
