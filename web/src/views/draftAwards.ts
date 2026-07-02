import { labelAwardType } from "../awards.ts";
import { api, type Row } from "../api.ts";
import {
  announceStatus,
  el,
  formatValue,
  labeledSelect,
  navigateToDetail,
  renderTable,
} from "../dom.ts";

export async function renderDraftAwards(container: HTMLElement): Promise<void> {
  const draftSection = el("section", { className: "subsection" });
  const awardsSection = el("section", { className: "subsection" });
  container.append(draftSection, awardsSection);
  await Promise.all([renderDraftSection(draftSection), renderAwardsSection(awardsSection)]);
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
        resultDiv.replaceChildren(
          renderTable(
            [
              { key: "full_name", label: "Player", render: awardPlayerCell },
              { key: "award_type", label: "Award", format: (v) => labelAwardType(String(v)) },
              { key: "description", label: "Detail" },
            ],
            rows,
          ),
        );
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
