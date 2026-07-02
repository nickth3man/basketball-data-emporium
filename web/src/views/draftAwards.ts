import { labelAwardType } from "../awards.ts";
import { api, type Row } from "../api.ts";
import {
  announceStatus,
  el,
  formatPct,
  formatValue,
  labeledSelect,
  navigateToDetail,
  playerCell,
  renderTable,
} from "../dom.ts";

const CAREER_VALUE_LIMIT = 50;
const CAREER_VALUE_DEFAULT_SORT = "career_ppg";
const CAREER_VALUE_SORTS: readonly { value: string; label: string }[] = [
  { value: "career_ppg", label: "PPG" },
  { value: "career_rpg", label: "RPG" },
  { value: "career_apg", label: "APG" },
  { value: "career_gp", label: "GP" },
  { value: "career_fg_pct", label: "FG%" },
  { value: "career_fg3_pct", label: "3P%" },
  { value: "seasons_played", label: "Seasons" },
];

export async function renderDraftAwards(container: HTMLElement): Promise<void> {
  const draftSection = el("section", { className: "subsection" });
  const awardsSection = el("section", { className: "subsection" });
  const careerValueSection = el("section", { className: "subsection" });
  container.append(draftSection, awardsSection, careerValueSection);
  await Promise.all([
    renderDraftSection(draftSection),
    renderAwardsSection(awardsSection),
    renderCareerValueSection(careerValueSection),
  ]);
}

async function renderDraftSection(container: HTMLElement): Promise<void> {
  container.append(el("h2", { text: "Draft" }), el("p", { className: "muted", text: "Loading…" }));
  announceStatus("Loading draft years…");
  try {
    const years = await api.draftYears();
    container.replaceChildren(el("h2", { text: "Draft" }));

    const { wrapper: yearWrapper, select: yearSelect } = labeledSelect(
      "Draft year",
      years.map((y) => ({ value: y, label: y })),
      "draft-year",
    );
    const resultDiv = el("div");
    container.append(yearWrapper, resultDiv);

    async function load(): Promise<void> {
      resultDiv.replaceChildren(el("p", { className: "muted", text: "Loading…" }));
      announceStatus("Loading draft picks…");
      try {
        const rows = await api.draft(yearSelect.value);
        resultDiv.replaceChildren(
          renderTable(
            [
              { key: "overall_pick", label: "Pick" },
              { key: "round_number", label: "Rd" },
              { key: "player_name", label: "Player", render: draftPlayerCell },
              { key: "team_abbreviation", label: "Team", render: teamCell },
              { key: "organization", label: "From" },
            ],
            rows,
          ),
        );
        announceStatus(`Loaded ${yearSelect.value} draft picks.`);
      } catch (err) {
        const message = err instanceof Error ? err.message : "Failed to load draft.";
        resultDiv.replaceChildren(el("p", { className: "muted", text: `Error: ${message}` }));
        announceStatus(`Failed to load draft: ${message}`);
      }
    }

    yearSelect.addEventListener("change", () => void load());
    await load();
  } catch (err) {
    const message = err instanceof Error ? err.message : "Failed to load draft years.";
    container.replaceChildren(
      el("h2", { text: "Draft" }),
      el("p", { className: "muted", text: `Error: ${message}` }),
    );
    announceStatus(`Failed to load draft years: ${message}`);
  }
}

const VOTED_AWARD_TYPES: ReadonlySet<string> = new Set([
  "nba mvp",
  "nba roy",
  "nba dpoy",
  "nba mip",
  "nba smoy",
]);

async function renderAwardsSection(container: HTMLElement): Promise<void> {
  container.append(el("h2", { text: "Awards" }), el("p", { className: "muted", text: "Loading…" }));
  announceStatus("Loading award seasons…");
  try {
    const [seasons, types] = await Promise.all([api.awardSeasons(), api.awardTypes()]);
    container.replaceChildren(el("h2", { text: "Awards" }));

    const { wrapper: seasonWrapper, select: seasonSelect } = labeledSelect(
      "Award season",
      seasons.map((s) => ({ value: s, label: s })),
      "awards-season",
    );
    const { wrapper: typeWrapper, select: typeSelect } = labeledSelect(
      "Award type",
      [
        { value: "", label: "All award types" },
        ...types.map((t) => ({ value: t, label: labelAwardType(t) })),
      ],
      "awards-type",
    );
    const resultDiv = el("div");
    container.append(el("div", { className: "controls" }, [seasonWrapper, typeWrapper]), resultDiv);

    async function load(): Promise<void> {
      resultDiv.replaceChildren(el("p", { className: "muted", text: "Loading…" }));
      announceStatus("Loading awards…");
      try {
        const rows = await api.awards(seasonSelect.value, typeSelect.value || null);
        const pieces: HTMLElement[] = [
          renderTable(
            [
              { key: "full_name", label: "Player", render: awardPlayerCell },
              { key: "award_type", label: "Award", format: (v) => labelAwardType(String(v)) },
              { key: "description", label: "Detail" },
            ],
            rows,
          ),
        ];
        // Major awards carry full BBR voting records — show the ballot
        // finish below the winner when one specific award is selected.
        if (VOTED_AWARD_TYPES.has(typeSelect.value)) {
          const voting = await api.getAwardVoting(seasonSelect.value, typeSelect.value);
          if (voting.length > 0) {
            pieces.push(
              el("h3", { text: `${labelAwardType(typeSelect.value)} voting` }),
              renderTable(
                [
                  { key: "full_name", label: "Player", render: awardPlayerCell },
                  { key: "age", label: "Age" },
                  { key: "first_place_votes", label: "1st-place votes" },
                  { key: "pts_won", label: "Points" },
                  { key: "pts_max", label: "Max points" },
                  { key: "share", label: "Share" },
                  { key: "winner", label: "Won", format: (v) => (v === true ? "✓" : "") },
                ],
                voting,
              ),
            );
          }
        }
        resultDiv.replaceChildren(...pieces);
        announceStatus(`Loaded ${seasonSelect.value} awards.`);
      } catch (err) {
        const message = err instanceof Error ? err.message : "Failed to load awards.";
        resultDiv.replaceChildren(el("p", { className: "muted", text: `Error: ${message}` }));
        announceStatus(`Failed to load awards: ${message}`);
      }
    }

    seasonSelect.addEventListener("change", () => void load());
    typeSelect.addEventListener("change", () => void load());
    await load();
  } catch (err) {
    const message = err instanceof Error ? err.message : "Failed to load award seasons.";
    container.replaceChildren(
      el("h2", { text: "Awards" }),
      el("p", { className: "muted", text: `Error: ${message}` }),
    );
    announceStatus(`Failed to load award seasons: ${message}`);
  }
}

function cellButton(label: string, onClick: () => void, ariaLabel: string): HTMLElement {
  const button = el("button", {
    type: "button",
    className: "cell-link",
    text: label,
    "aria-label": ariaLabel,
  });
  button.addEventListener("click", onClick);
  return button;
}

async function renderCareerValueSection(container: HTMLElement): Promise<void> {
  container.append(
    el("h2", { text: "Best career value" }),
    el("p", { className: "muted", text: "Loading…" }),
  );
  announceStatus("Loading career value…");
  try {
    const rounds = await api.listDraftValueRounds();
    container.replaceChildren(el("h2", { text: "Best career value" }));

    const roundOptions = [
      { value: "", label: "All rounds" },
      ...rounds.map((r) => ({ value: String(r), label: String(r) })),
    ];
    const defaultSort = CAREER_VALUE_SORTS.find((s) => s.value === CAREER_VALUE_DEFAULT_SORT);
    if (!defaultSort) {
      throw new Error(`Default sort key '${CAREER_VALUE_DEFAULT_SORT}' is not registered.`);
    }

    const { wrapper: roundWrapper, select: roundSelect } = labeledSelect(
      "Round",
      roundOptions,
      "career-value-round",
    );
    const { wrapper: sortWrapper, select: sortSelect } = labeledSelect(
      "Sort by",
      CAREER_VALUE_SORTS.map((s) => ({ value: s.value, label: s.label })),
      "career-value-sort",
    );
    sortSelect.value = defaultSort.value;
    const resultDiv = el("div");
    container.append(el("div", { className: "controls" }, [roundWrapper, sortWrapper]), resultDiv);

    async function load(): Promise<void> {
      resultDiv.replaceChildren(el("p", { className: "muted", text: "Loading…" }));
      announceStatus("Loading career value…");
      try {
        const roundRaw = roundSelect.value;
        const round = roundRaw !== "" ? Number(roundRaw) : undefined;
        const sort = sortSelect.value;
        const sortLabel = CAREER_VALUE_SORTS.find((s) => s.value === sort)?.label ?? sort;
        const rows = await api.getDraftValueBoard({
          round,
          sort,
          limit: CAREER_VALUE_LIMIT,
        });
        if (rows.length === 0) {
          resultDiv.replaceChildren(
            el("p", { className: "muted", text: "No rows for this selection." }),
          );
          announceStatus("No rows for this selection.");
          return;
        }
        resultDiv.replaceChildren(
          renderTable(
            [
              {
                key: "__index",
                label: "#",
                format: (_v, row) => formatValue(row.__index),
              },
              { key: "full_name", label: "Player", render: playerCell },
              { key: "overall_pick", label: "Pick" },
              { key: "round_number", label: "Rd" },
              { key: "position", label: "Pos" },
              { key: "seasons_played", label: "Seasons" },
              { key: "career_gp", label: "GP" },
              { key: "career_ppg", label: "PPG" },
              { key: "career_rpg", label: "RPG" },
              { key: "career_apg", label: "APG" },
              { key: "career_fg_pct", label: "FG%", format: formatPct },
              { key: "career_fg3_pct", label: "3P%", format: formatPct },
            ],
            rows.map((row, index) => ({ ...row, __index: index + 1 })),
          ),
        );
        announceStatus(`Loaded top ${rows.length} by career ${sortLabel}.`);
      } catch (err) {
        const message = err instanceof Error ? err.message : "Failed to load career value.";
        resultDiv.replaceChildren(el("p", { className: "muted", text: `Error: ${message}` }));
        announceStatus(`Failed to load career value: ${message}`);
      }
    }

    roundSelect.addEventListener("change", () => void load());
    sortSelect.addEventListener("change", () => void load());
    await load();
  } catch (err) {
    const message = err instanceof Error ? err.message : "Failed to load career value rounds.";
    container.replaceChildren(
      el("h2", { text: "Best career value" }),
      el("p", { className: "muted", text: `Error: ${message}` }),
    );
    announceStatus(`Failed to load career value: ${message}`);
  }
}

function draftPlayerCell(value: unknown, row: Row): Node | string {
  const label = formatValue(value);
  const playerId = Number(row.person_id);
  if (!Number.isFinite(playerId) || label === "—") return label;
  return cellButton(
    label,
    () => navigateToDetail("players", String(playerId)),
    `${label} player profile`,
  );
}

function awardPlayerCell(value: unknown, row: Row): Node | string {
  const label = formatValue(value);
  const playerId = Number(row.player_id);
  if (!Number.isFinite(playerId) || label === "—") return label;
  return cellButton(
    label,
    () => navigateToDetail("players", String(playerId)),
    `${label} player profile`,
  );
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
