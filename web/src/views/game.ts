import { api, type GameDetail, type Row } from "../api.ts";
import {
  announceStatus,
  el,
  formatPct,
  formatValue,
  playerCell,
  renderDefList,
  renderTable,
} from "../dom.ts";

// Hidden "game" tab: a per-game box-score page reached from recent-games
// rows (players/teams). Everything comes from one /api/games/:id call over
// fact_game, box-score, period-score, official, lineup, context, and PBP
// tables.

export function renderGame(container: HTMLElement, gameId?: string): void {
  const detail = el("div", { className: "detail" });
  container.append(detail);
  if (!gameId) {
    detail.append(
      el("p", {
        className: "muted",
        text: "Open a game from a player's or team's recent-games table.",
      }),
    );
    return;
  }
  void load(gameId);

  async function load(id: string): Promise<void> {
    detail.replaceChildren(el("p", { className: "muted", text: "Loading..." }));
    announceStatus("Loading game...");
    try {
      const game = await api.getGameDetail(id);
      detail.replaceChildren();
      if (!game.header) {
        detail.append(el("p", { text: "Game not found." }));
        announceStatus("Game not found.");
        return;
      }
      renderHeader(detail, game.header);
      renderCoverage(detail, game.coverage);
      renderLineScore(detail, game);
      if (game.teamBoxes.length > 0) renderTeamBoxes(detail, game.teamBoxes);
      if (game.playerBoxes.length > 0) renderPlayerBoxes(detail, game.playerBoxes, game.header);
      if (game.leaders.length > 0) renderLeaders(detail, game.leaders);
      if (game.starters.length > 0) renderStarters(detail, game.starters, game.header);
      if (game.context.length > 0) renderContext(detail, game.context);
      void loadGameFlow(detail, id, game.header);
      void loadFourFactors(detail, id);
      if (game.lastPlays.length > 0) renderLastPlays(detail, game.lastPlays, game.header);
      if (game.officials.length > 0) renderOfficials(detail, game.officials);
      announceStatus("Game loaded.");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load game.";
      detail.replaceChildren(el("p", { className: "muted", text: `Error: ${message}` }));
      announceStatus(`Failed to load game: ${message}`);
    }
  }
}

function renderHeader(container: HTMLElement, h: Row): void {
  const date = formatValue(h.game_date).slice(0, 10);
  const title = `${formatValue(h.away_name)} ${formatValue(h.away_score)} @ ${formatValue(h.home_name)} ${formatValue(h.home_score)}`;
  container.append(el("h2", { text: title }));
  const label = [h.game_label, h.game_sub_label].filter(Boolean).map(String).join(" - ");
  container.append(
    renderDefList([
      ["Date", date],
      ["Season", `${formatValue(h.season_year)} ${formatValue(h.season_type)}`],
      ["Round", label || null],
      ["Series game", h.series_game_number],
      ["Status", h.game_status_text],
      ["Duration", h.game_duration],
      [
        "Arena",
        h.arena_name
          ? `${formatValue(h.arena_name)}${h.arena_city ? ` (${formatValue(h.arena_city)}${h.arena_state ? `, ${formatValue(h.arena_state)}` : ""})` : ""}`
          : null,
      ],
      ["Attendance", h.attendance],
      ["Overtime", h.is_overtime === true ? "Yes" : null],
      [
        "Closing odds (home/away)",
        h.odds_home != null && h.odds_away != null
          ? `${formatValue(h.odds_home)} / ${formatValue(h.odds_away)}`
          : null,
      ],
    ]),
  );
}

function renderCoverage(container: HTMLElement, coverage: Row): void {
  const coverageLabel =
    typeof coverage.coverage_label === "string" ? coverage.coverage_label : "Partial box score";
  const chips = [
    coverageLabel,
    coverage.has_period_scores === true ? null : "Final score only",
    coverage.has_team_box === true ? null : "Team totals missing",
    coverage.has_officials === true ? null : "Officials missing",
    coverage.has_attendance === true ? null : "Attendance missing",
    coverage.has_pbp === true ? null : "Play-by-play missing",
  ].filter((chip): chip is string => Boolean(chip));
  container.append(
    el(
      "div",
      { className: "coverage-pills", "aria-label": "Game data coverage" },
      chips.map((chip, index) =>
        el("span", {
          className: index === 0 ? "coverage-pill" : "coverage-pill coverage-pill-muted",
          text: chip,
        }),
      ),
    ),
  );
}

function renderLineScore(container: HTMLElement, game: GameDetail): void {
  const ls = game.lineScore;
  const h = game.header;
  if (!ls || !h) return;
  // The API normalizes legacy line_score orientation against fact_game, but
  // keep the same team-id check here so the view still renders correctly when
  // tests or future callers pass an older/raw shape.
  const trueHomeSide: "home" | "away" =
    String(ls.team_id_home) === String(h.home_team_id) ? "home" : "away";
  const trueAwaySide: "home" | "away" = trueHomeSide === "home" ? "away" : "home";
  const hasPeriodScores = ["home", "away"].some((side) =>
    ["1", "2", "3", "4"].some((q) => ls[`pts_qtr${q}_${side}`] != null),
  );
  const quarters = hasPeriodScores ? ["1", "2", "3", "4"] : [];
  const overtimes: string[] = [];
  if (hasPeriodScores) {
    for (let ot = 1; ot <= 10; ot++) {
      if (ls[`pts_ot${ot}_home`] != null || ls[`pts_ot${ot}_away`] != null)
        overtimes.push(String(ot));
    }
  }
  const sideRow = (side: "home" | "away"): Row => {
    const row: Row = {
      team: [ls[`team_city_name_${side}`], ls[`team_nickname_${side}`]]
        .filter(Boolean)
        .map(formatValue)
        .join(" "),
    };
    for (const q of quarters) row[`q${q}`] = ls[`pts_qtr${q}_${side}`];
    for (const ot of overtimes) row[`ot${ot}`] = ls[`pts_ot${ot}_${side}`];
    row.total = ls[`pts_${side}`];
    return row;
  };
  container.append(
    el("section", {}, [
      el("h3", { id: "game-line-score", text: "Line score" }),
      renderTable(
        [
          { key: "team", label: "Team" },
          ...quarters.map((q) => ({ key: `q${q}`, label: `Q${q}` })),
          ...overtimes.map((ot) => ({ key: `ot${ot}`, label: `OT${ot}` })),
          { key: "total", label: "Final" },
        ],
        [sideRow(trueAwaySide), sideRow(trueHomeSide)],
      ),
    ]),
  );
}

function madeAttempt(row: Record<string, unknown>, madeKey: string, attemptKey: string): string {
  const made = row[madeKey];
  const attempts = row[attemptKey];
  if (made == null && attempts == null) return "—";
  if (attempts == null) return formatValue(made);
  return `${formatValue(made)}-${formatValue(attempts)}`;
}

function formatMaybePercent(value: unknown): string {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const pct = Math.abs(n) > 1 ? n : n * 100;
  return `${pct.toFixed(1)}%`;
}

function renderTeamBoxes(container: HTMLElement, rows: Row[]): void {
  container.append(
    el("section", {}, [
      el("h3", { id: "game-team-box", text: "Team box" }),
      renderTable(
        [
          { key: "team_name", label: "Team" },
          { key: "fg", label: "FG", render: (_v, row) => madeAttempt(row, "fgm", "fga") },
          { key: "fg_pct", label: "FG%", format: formatPct },
          { key: "fg3", label: "3P", render: (_v, row) => madeAttempt(row, "fg3m", "fg3a") },
          { key: "fg3_pct", label: "3P%", format: formatPct },
          { key: "ft", label: "FT", render: (_v, row) => madeAttempt(row, "ftm", "fta") },
          { key: "ft_pct", label: "FT%", format: formatPct },
          { key: "oreb", label: "OREB" },
          { key: "dreb", label: "DREB" },
          { key: "reb", label: "REB" },
          { key: "ast", label: "AST" },
          { key: "stl", label: "STL" },
          { key: "blk", label: "BLK" },
          { key: "tov", label: "TO" },
          { key: "pf", label: "PF" },
          { key: "pts", label: "PTS" },
          { key: "plus_minus", label: "+/-" },
        ],
        rows,
      ),
    ]),
  );
}

function playerBoxCell(value: unknown, row: Record<string, unknown>): Node | string {
  return playerCell(value, row);
}

function teamRows(rows: Row[], side: "away" | "home", header: Row): Row[] {
  const sideId = side === "home" ? header.home_team_id : header.away_team_id;
  return rows.filter((row) => String(row.team_id) === String(sideId) || row.team_side === side);
}

function teamLabel(side: "away" | "home", header: Row): string {
  return side === "home" ? formatValue(header.home_name) : formatValue(header.away_name);
}

function renderPlayerBoxes(container: HTMLElement, rows: Row[], header: Row): void {
  const section = el("section", {}, [el("h3", { id: "game-player-box", text: "Player box" })]);
  for (const side of ["away", "home"] as const) {
    const rowsForTeam = teamRows(rows, side, header);
    if (rowsForTeam.length === 0) continue;
    const label = teamLabel(side, header);
    const scoringOnly = rowsForTeam.every((row) => row.coverage_level === "scoring_only");
    section.append(
      el("h4", { text: scoringOnly ? `${label} scoring` : `${label} traditional` }),
      renderTable(
        scoringOnly ? historicalPlayerColumns() : traditionalPlayerColumns(),
        rowsForTeam,
      ),
    );
    if (!scoringOnly && rowsForTeam.some((row) => row.off_rating != null || row.ts_pct != null)) {
      section.append(
        el("h4", { text: `${label} advanced` }),
        renderTable(advancedPlayerColumns(), rowsForTeam),
      );
    }
  }
  container.append(section);
}

function historicalPlayerColumns(): Parameters<typeof renderTable>[0] {
  return [
    { key: "full_name", label: "Player", render: playerBoxCell },
    { key: "fgm", label: "FGM" },
    { key: "ftm", label: "FTM" },
    { key: "points", label: "PTS" },
    { key: "comment", label: "Status" },
  ];
}

function traditionalPlayerColumns(): Parameters<typeof renderTable>[0] {
  return [
    { key: "starting_position", label: "Pos" },
    { key: "full_name", label: "Player", render: playerBoxCell },
    { key: "min", label: "MIN" },
    { key: "fg", label: "FG", render: (_v, row) => madeAttempt(row, "fgm", "fga") },
    { key: "fg3", label: "3P", render: (_v, row) => madeAttempt(row, "fg3m", "fg3a") },
    { key: "ft", label: "FT", render: (_v, row) => madeAttempt(row, "ftm", "fta") },
    { key: "oreb", label: "OREB" },
    { key: "dreb", label: "DREB" },
    { key: "reb", label: "REB" },
    { key: "assists", label: "AST" },
    { key: "steals", label: "STL" },
    { key: "blocks", label: "BLK" },
    { key: "turnovers", label: "TO" },
    { key: "fouls_personal", label: "PF" },
    { key: "points", label: "PTS" },
    { key: "plus_minus", label: "+/-" },
    { key: "comment", label: "Status" },
  ];
}

function advancedPlayerColumns(): Parameters<typeof renderTable>[0] {
  return [
    { key: "full_name", label: "Player", render: playerBoxCell },
    { key: "off_rating", label: "ORtg" },
    { key: "def_rating", label: "DRtg" },
    { key: "net_rating", label: "Net" },
    { key: "ast_pct", label: "AST%", format: formatPct },
    { key: "reb_pct", label: "REB%", format: formatPct },
    { key: "tov_pct", label: "TO%", format: formatMaybePercent },
    { key: "efg_pct", label: "eFG%", format: formatPct },
    { key: "ts_pct", label: "TS%", format: formatPct },
    { key: "usg_pct", label: "USG%", format: formatPct },
    { key: "pace", label: "Pace" },
    { key: "pie", label: "PIE", format: formatPct },
  ];
}

function renderLeaders(container: HTMLElement, leaders: Row[]): void {
  container.append(
    el("section", {}, [
      el("h3", { id: "game-leaders", text: "Game leaders" }),
      renderTable(
        [
          { key: "leader_type", label: "Category" },
          { key: "team_tricode", label: "Team" },
          { key: "name", label: "Player", render: leaderPlayerCell },
          { key: "points", label: "PTS" },
          { key: "rebounds", label: "REB" },
          { key: "assists", label: "AST" },
        ],
        leaders,
      ),
    ]),
  );
}

// fact_game_leaders keys the player as person_id; reuse the shared player
// cell by aliasing it into the shape playerCell expects.
function leaderPlayerCell(value: unknown, row: Record<string, unknown>): Node | string {
  return playerCell(value, { ...row, player_id: row.person_id });
}

function renderStarters(container: HTMLElement, starters: Row[], header: Row): void {
  const bySide = (teamId: unknown): Row[] =>
    starters.filter((s) => String(s.team_id) === String(teamId));
  const sideTable = (rows: Row[], label: string): HTMLElement =>
    el("div", {}, [
      el("h4", { text: label }),
      renderTable(
        [
          { key: "starting_position", label: "Pos" },
          { key: "full_name", label: "Player", render: starterPlayerCell },
        ],
        rows,
      ),
    ]);
  container.append(
    el("section", {}, [
      el("h3", { id: "game-starters", text: "Starting lineups" }),
      el("div", { className: "split-columns" }, [
        sideTable(
          bySide(header.away_team_id),
          header.away_name ? formatValue(header.away_name) : "Away",
        ),
        sideTable(
          bySide(header.home_team_id),
          header.home_name ? formatValue(header.home_name) : "Home",
        ),
      ]),
    ]),
  );
}

function starterPlayerCell(value: unknown, row: Record<string, unknown>): Node | string {
  return playerCell(value, { ...row, player_id: row.person_id });
}

// ---------------------------------------------------------------------------
// Game flow — a score-margin timeline computed from the play-by-play's
// scoring events (available 1996-97 onward). Margin is home minus away, so
// the top half of the chart is the home team leading. Lead changes, ties,
// and the biggest unanswered run are derived client-side from the same
// event series.
// ---------------------------------------------------------------------------

const FLOW_SVG_NS = "http://www.w3.org/2000/svg";

function flowSvgEl(tag: string, attrs: Record<string, string | number> = {}): SVGElement {
  const node = document.createElementNS(FLOW_SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  return node;
}

interface FlowPoint {
  seconds: number;
  home: number;
  away: number;
}

interface FlowStats {
  leadChanges: number;
  ties: number;
  biggestRun: number;
  biggestRunSide: "home" | "away" | null;
  maxHomeLead: number;
  maxAwayLead: number;
}

function computeFlowStats(points: FlowPoint[]): FlowStats {
  let leadChanges = 0;
  let ties = 0;
  let lastLeader: 1 | -1 | 0 = 0;
  let runSide: "home" | "away" | null = null;
  let runPts = 0;
  let biggestRun = 0;
  let biggestRunSide: FlowStats["biggestRunSide"] = null;
  let maxHomeLead = 0;
  let maxAwayLead = 0;
  let prev: FlowPoint = { seconds: 0, home: 0, away: 0 };
  for (const point of points) {
    const margin = point.home - point.away;
    if (margin === 0) ties += 1;
    const leader = margin > 0 ? 1 : margin < 0 ? -1 : 0;
    if (leader !== 0) {
      if (lastLeader !== 0 && leader !== lastLeader) leadChanges += 1;
      lastLeader = leader;
    }
    maxHomeLead = Math.max(maxHomeLead, margin);
    maxAwayLead = Math.max(maxAwayLead, -margin);
    const scoredBy: "home" | "away" | null =
      point.home > prev.home ? "home" : point.away > prev.away ? "away" : null;
    const delta = scoredBy === "home" ? point.home - prev.home : point.away - prev.away;
    if (scoredBy) {
      if (scoredBy === runSide) runPts += delta;
      else {
        runSide = scoredBy;
        runPts = delta;
      }
      if (runPts > biggestRun) {
        biggestRun = runPts;
        biggestRunSide = runSide;
      }
    }
    prev = point;
  }
  return { leadChanges, ties, biggestRun, biggestRunSide, maxHomeLead, maxAwayLead };
}

function buildFlowChart(points: FlowPoint[], homeLabel: string, awayLabel: string): HTMLElement {
  const width = 720;
  const height = 260;
  const left = 34;
  const right = 12;
  const top = 16;
  const bottom = 22;
  const plotW = width - left - right;
  const plotH = height - top - bottom;

  const lastSeconds = points[points.length - 1]?.seconds ?? 2880;
  const regulation = 2880;
  const endSeconds = Math.max(regulation, lastSeconds);
  const maxAbs = Math.max(5, ...points.map((p) => Math.abs(p.home - p.away)));
  const x = (s: number): number => left + (s / endSeconds) * plotW;
  const y = (margin: number): number => top + plotH / 2 - (margin / maxAbs) * (plotH / 2);

  const svg = flowSvgEl("svg", {
    viewBox: `0 0 ${width} ${height}`,
    class: "flow-chart",
    role: "img",
    "aria-label": `Score margin timeline, ${homeLabel} minus ${awayLabel}`,
  });

  // Period boundaries: quarters, then 5-minute overtimes.
  const boundaries: number[] = [720, 1440, 2160, 2880];
  for (let ot = regulation + 300; ot <= endSeconds; ot += 300) boundaries.push(ot);
  for (const boundary of boundaries) {
    if (boundary > endSeconds) break;
    svg.append(
      flowSvgEl("line", {
        x1: x(boundary),
        y1: top,
        x2: x(boundary),
        y2: top + plotH,
        class: "form-grid",
      }),
    );
  }
  const labels: [number, string][] = [
    [360, "Q1"],
    [1080, "Q2"],
    [1800, "Q3"],
    [2520, "Q4"],
  ];
  for (let ot = 1; regulation + ot * 300 <= endSeconds; ot++) {
    labels.push([regulation + ot * 300 - 150, `OT${ot}`]);
  }
  for (const [mid, text] of labels) {
    const label = flowSvgEl("text", {
      x: x(mid),
      y: height - 6,
      "text-anchor": "middle",
      class: "form-axis-label",
    });
    label.textContent = text;
    svg.append(label);
  }

  // Zero line + margin extents.
  svg.append(
    flowSvgEl("line", {
      x1: left,
      y1: y(0),
      x2: width - right,
      y2: y(0),
      class: "flow-zero-line",
    }),
  );
  for (const extent of [maxAbs, -maxAbs]) {
    const tick = flowSvgEl("text", {
      x: left - 4,
      y: y(extent) + 4,
      "text-anchor": "end",
      class: "form-axis-label",
    });
    tick.textContent = `${extent > 0 ? "+" : ""}${extent}`;
    svg.append(tick);
  }
  const homeTag = flowSvgEl("text", { x: left + 4, y: top + 10, class: "flow-side-label" });
  homeTag.textContent = `${homeLabel} leads`;
  const awayTag = flowSvgEl("text", {
    x: left + 4,
    y: top + plotH - 4,
    class: "flow-side-label",
  });
  awayTag.textContent = `${awayLabel} leads`;
  svg.append(homeTag, awayTag);

  // Step path through every scoring event.
  let d = `M ${x(0).toFixed(1)} ${y(0).toFixed(1)}`;
  let prevY = y(0);
  for (const point of points) {
    const px = x(point.seconds).toFixed(1);
    const py = y(point.home - point.away);
    d += ` L ${px} ${prevY.toFixed(1)} L ${px} ${py.toFixed(1)}`;
    prevY = py;
  }
  svg.append(flowSvgEl("path", { d, class: "flow-line" }));

  return el("div", { className: "form-chart-wrap flow-chart-wrap" }, [svg]);
}

async function loadGameFlow(container: HTMLElement, gameId: string, header: Row): Promise<void> {
  try {
    const rows = await api.getGameFlow(gameId);
    if (rows.length < 5) return;
    const points: FlowPoint[] = rows.map((row) => ({
      seconds: Number(row.seconds_elapsed),
      home: Number(row.score_home),
      away: Number(row.score_away),
    }));
    const homeLabel = formatValue(header.home_abbreviation);
    const awayLabel = formatValue(header.away_abbreviation);
    const stats = computeFlowStats(points);
    const runLabel =
      stats.biggestRun > 0
        ? `${stats.biggestRun}-0 ${stats.biggestRunSide === "home" ? homeLabel : awayLabel}`
        : "—";
    const summary = el("p", {
      className: "table-note",
      text:
        `Lead changes: ${stats.leadChanges} · Ties: ${stats.ties} · ` +
        `Biggest run: ${runLabel} · Largest lead: ${homeLabel} +${stats.maxHomeLead} / ${awayLabel} +${stats.maxAwayLead}`,
    });
    const section = el("section", {}, [
      el("h3", { id: "game-flow", text: "Game flow" }),
      summary,
      buildFlowChart(points, homeLabel, awayLabel),
    ]);
    // Keep the reading order sensible: place game flow right after the line
    // score when it exists, otherwise append at the end.
    const lineScoreHeading = container.querySelector("#game-line-score");
    const anchor = lineScoreHeading?.closest("section") ?? null;
    if (anchor?.nextSibling) container.insertBefore(section, anchor.nextSibling);
    else container.append(section);
  } catch {
    // Flow is an enrichment; the box score page works without it.
  }
}

// Four factors are a separate endpoint/table (2000-01 onward) so they load
// after the main box score and are simply skipped for games without rows.
async function loadFourFactors(container: HTMLElement, gameId: string): Promise<void> {
  try {
    const rows = await api.getGameFourFactors(gameId);
    if (rows.length === 0) return;
    container.append(
      el("section", {}, [
        el("h3", { id: "game-four-factors", text: "Four factors" }),
        renderTable(
          [
            { key: "team_name", label: "Team" },
            { key: "side", label: "Site" },
            { key: "efg_pct", label: "eFG%", format: formatPct },
            { key: "tov_pct", label: "TOV%", format: formatPct },
            { key: "oreb_pct", label: "ORB%", format: formatPct },
            { key: "ft_rate", label: "FT rate", format: formatPct },
          ],
          rows,
        ),
      ]),
    );
  } catch {
    // Non-critical enrichment; the box score stands on its own.
  }
}

function renderContext(container: HTMLElement, rows: Row[]): void {
  container.append(
    el("section", {}, [
      el("h3", { id: "game-context", text: "Game context" }),
      renderTable(
        [
          { key: "team_name", label: "Team" },
          { key: "pts_paint", label: "Paint" },
          { key: "pts_2nd_chance", label: "2nd chance" },
          { key: "pts_fb", label: "Fast break" },
          { key: "pts_off_to", label: "Off TO" },
          { key: "bench_points", label: "Bench" },
          { key: "largest_lead", label: "Largest lead" },
          { key: "biggest_scoring_run", label: "Run" },
          { key: "lead_changes", label: "Lead changes" },
          { key: "times_tied", label: "Ties" },
        ],
        rows,
      ),
    ]),
  );
}

function renderLastPlays(container: HTMLElement, plays: Row[], header: Row): void {
  // Server returns the final scoring plays newest-first; show them oldest-first.
  const ordered = [...plays].reverse();
  container.append(
    el("section", {}, [
      el("h3", { id: "game-last-plays", text: "Final scoring plays" }),
      renderTable(
        [
          { key: "period", label: "Period" },
          { key: "clock", label: "Clock" },
          { key: "description", label: "Play" },
          {
            key: "score_away",
            label: `Score (${formatValue(header.away_abbreviation)}-${formatValue(header.home_abbreviation)})`,
            render: (_v, row) => `${formatValue(row.score_away)}-${formatValue(row.score_home)}`,
          },
        ],
        ordered,
      ),
    ]),
  );
}

function renderOfficials(container: HTMLElement, officials: Row[]): void {
  container.append(
    el("section", {}, [
      el("h3", { id: "game-officials", text: "Officials" }),
      el("p", {
        text: officials
          .map((o) => {
            if (o.name) return formatValue(o.name);
            return `${formatValue(o.first_name)} ${formatValue(o.last_name)}`;
          })
          .join(", "),
      }),
    ]),
  );
}
