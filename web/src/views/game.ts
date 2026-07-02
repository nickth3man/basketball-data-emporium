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
