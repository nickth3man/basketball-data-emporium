import { api, type GameDetail, type Row } from "../api.ts";
import { announceStatus, el, formatValue, playerCell, renderDefList, renderTable } from "../dom.ts";

// Hidden "game" tab: a per-game box-score page reached from recent-games
// rows (players/teams). Everything comes from one /api/games/:id call over
// fact_game + line_score + fact_game_leaders + officials +
// fact_starting_lineup_player + the fact_pbp_events tail.

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
    detail.replaceChildren(el("p", { className: "muted", text: "Loading…" }));
    announceStatus("Loading game…");
    try {
      const game = await api.getGameDetail(id);
      detail.replaceChildren();
      if (!game.header) {
        detail.append(el("p", { text: "Game not found." }));
        announceStatus("Game not found.");
        return;
      }
      renderHeader(detail, game.header);
      renderLineScore(detail, game);
      if (game.leaders.length > 0) renderLeaders(detail, game.leaders);
      if (game.starters.length > 0) renderStarters(detail, game.starters, game.header);
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
  const label = [h.game_label, h.game_sub_label].filter(Boolean).map(String).join(" — ");
  container.append(
    renderDefList([
      ["Date", date],
      ["Season", `${formatValue(h.season_year)} ${formatValue(h.season_type)}`],
      ["Round", label || null],
      ["Series game", h.series_game_number],
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
  const hasPeriodScores = ls.line_score_source !== "fact_game_total";
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
          .map((o) => `${formatValue(o.first_name)} ${formatValue(o.last_name)}`)
          .join(", "),
      }),
    ]),
  );
}
