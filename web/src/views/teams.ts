import { api, type Row } from "../api.ts";
import {
  announceStatus,
  boxScoreCell,
  el,
  formatPct,
  formatValue,
  playerCell,
  renderDefList,
  renderJumpNav,
  renderTable,
  sectionHeading,
  tableNote,
  teamLogo,
} from "../dom.ts";

export function renderTeams(container: HTMLElement, initialTeamId?: string): void {
  const resultsList = el("ul", { className: "result-list" });
  const detail = el("div", { className: "detail" });

  container.append(el("div", { className: "search-panel" }, [resultsList]), detail);

  // Search now lives in the persistent global header (see headerSearch.ts);
  // this tab just shows a small curated default subset until you navigate
  // to a specific team's profile.
  async function loadCurated(): Promise<void> {
    resultsList.replaceChildren();
    announceStatus("Loading teams…");
    try {
      const teams = await api.searchTeams("");
      if (teams.length === 0) {
        resultsList.append(el("li", { className: "muted", text: "No teams found." }));
        announceStatus("No teams found.");
        return;
      }
      announceStatus(`Showing ${teams.length} teams.`);
      for (const t of teams) {
        const button = el("button", { type: "button", className: "result-row" }, [
          el("span", { text: String(t.team_name) }),
          el("span", { className: "muted", text: String(t.abbreviation) }),
        ]);
        button.addEventListener("click", () => void showTeam(String(t.team_id)));
        resultsList.append(el("li", {}, [button]));
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load teams.";
      resultsList.append(el("li", { className: "muted", text: `Error: ${message}` }));
      announceStatus(`Failed to load teams: ${message}`);
    }
  }

  async function showTeam(id: string): Promise<void> {
    detail.replaceChildren(el("p", { className: "muted", text: "Loading…" }));
    announceStatus("Loading team profile…");
    try {
      const profile = await api.getTeam(id);
      detail.replaceChildren();
      if (!profile.bio) {
        detail.append(el("p", { text: "Team not found." }));
        announceStatus("Team not found.");
        return;
      }
      renderBio(detail, profile.bio, profile.currentStanding);
      const jumpNav = el("nav", {
        className: "jump-nav",
        "aria-label": `${String(profile.bio.nickname)} sections`,
      });
      detail.append(jumpNav);
      if (profile.franchiseHistory.length > 1)
        renderFranchiseHistory(detail, profile.franchiseHistory);
      if (profile.seasons.length > 0) renderSeasons(detail, profile.seasons);
      if (profile.recentGames.length > 0) renderRecentGames(detail, profile.recentGames);
      announceStatus(`Loaded profile for ${String(profile.bio.nickname)}.`);

      const [roster, playoffSeries, lineups, coaches, ranks, opponentStats, headToHead, context] =
        await Promise.allSettled([
          api.getTeamRoster(id),
          api.getTeamPlayoffSeries(id),
          api.getTeamLineups(id),
          api.getTeamCoaches(id),
          api.getTeamRanks(id),
          api.getTeamOpponentStats(id),
          api.getTeamHeadToHead(id),
          api.getTeamSeasonContext(id),
        ]);
      if (roster.status === "fulfilled" && roster.value.length > 0)
        renderRoster(detail, roster.value);
      if (coaches.status === "fulfilled" && coaches.value.length > 0)
        renderCoachHistory(detail, coaches.value);
      if (playoffSeries.status === "fulfilled" && playoffSeries.value.length > 0)
        renderPlayoffSeries(detail, playoffSeries.value);
      if (lineups.status === "fulfilled" && lineups.value.length > 0)
        renderLineups(detail, lineups.value);
      if (ranks.status === "fulfilled" && ranks.value.length > 0) renderRanks(detail, ranks.value);
      if (opponentStats.status === "fulfilled" && opponentStats.value.length > 0)
        renderOpponentStats(detail, opponentStats.value);
      if (context.status === "fulfilled" && context.value.length > 0)
        renderSeasonContext(detail, context.value);
      if (headToHead.status === "fulfilled" && headToHead.value.length > 0)
        renderHeadToHead(detail, headToHead.value);
      renderJumpNav(jumpNav, [
        profile.franchiseHistory.length > 1 ? ["Franchise", "team-franchise"] : null,
        profile.seasons.length > 0 ? ["Seasons", "team-seasons"] : null,
        profile.recentGames.length > 0 ? ["Games", "team-games"] : null,
        roster.status === "fulfilled" && roster.value.length > 0 ? ["Roster", "team-roster"] : null,
        coaches.status === "fulfilled" && coaches.value.length > 0
          ? ["Coaches", "team-coaches"]
          : null,
        playoffSeries.status === "fulfilled" && playoffSeries.value.length > 0
          ? ["Playoffs", "team-playoffs"]
          : null,
        lineups.status === "fulfilled" && lineups.value.length > 0
          ? ["Lineups", "team-lineups"]
          : null,
        ranks.status === "fulfilled" && ranks.value.length > 0 ? ["Ranks", "team-ranks"] : null,
        opponentStats.status === "fulfilled" && opponentStats.value.length > 0
          ? ["Opponent", "team-opponent"]
          : null,
        context.status === "fulfilled" && context.value.length > 0
          ? ["Context", "team-season-context"]
          : null,
        headToHead.status === "fulfilled" && headToHead.value.length > 0
          ? ["H2H", "team-head-to-head"]
          : null,
      ]);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load team.";
      detail.replaceChildren(el("p", { className: "muted", text: `Error: ${message}` }));
      announceStatus(`Failed to load team: ${message}`);
    }
  }

  if (initialTeamId) void showTeam(initialTeamId);
  else void loadCurated();
}

function renderBio(container: HTMLElement, bio: Row, standing: Row | null): void {
  const header = el("div", { className: "team-header" }, [
    teamLogo(bio.team_id, String(bio.abbreviation), "team-logo-lg", String(bio.nickname)),
    el("h2", { text: String(bio.nickname) }),
  ]);
  container.append(
    header,
    renderDefList([
      ["Abbreviation", bio.abbreviation],
      ["Conference", standing?.conference],
      ["Division", standing?.division],
      ["Arena", bio.arena],
      ["Arena capacity", bio.arenacapacity],
      ["Founded", bio.year_founded],
      ["Owner", bio.owner],
      ["General Manager", bio.generalmanager],
      ["Head Coach", bio.headcoach],
      ["D-League affiliate", bio.dleagueaffiliation],
      [
        "Latest record",
        standing
          ? `${formatValue(standing.wins)}-${formatValue(standing.losses)} (${formatValue(standing.season_year)} ${formatValue(standing.season_type)})`
          : "—",
      ],
    ]),
  );
  const socialLinks = [
    typeof bio.facebook === "string" && bio.facebook
      ? el("a", { href: bio.facebook, target: "_blank", rel: "noreferrer", text: "Facebook" })
      : null,
    typeof bio.instagram === "string" && bio.instagram
      ? el("a", { href: bio.instagram, target: "_blank", rel: "noreferrer", text: "Instagram" })
      : null,
    typeof bio.twitter === "string" && bio.twitter
      ? el("a", { href: bio.twitter, target: "_blank", rel: "noreferrer", text: "Twitter" })
      : null,
  ].filter((n): n is HTMLElement => n !== null);
  if (socialLinks.length > 0) {
    const linkRow = el("p", { className: "bio-line" });
    socialLinks.forEach((link, i) => {
      if (i > 0) linkRow.append(" · ");
      linkRow.append(link);
    });
    container.append(linkRow);
  }
}

function renderRoster(container: HTMLElement, roster: Row[]): void {
  container.append(
    el("section", {}, [
      sectionHeading("team-roster", "Current roster"),
      renderTable(
        [
          { key: "full_name", label: "Player", render: playerCell },
          { key: "position", label: "Pos" },
          { key: "jersey_number", label: "#" },
          { key: "height", label: "Ht" },
          { key: "weight", label: "Wt" },
        ],
        roster,
      ),
    ]),
  );
}

function renderCoachHistory(container: HTMLElement, coaches: Row[]): void {
  container.append(
    el("section", {}, [
      sectionHeading("team-coaches", "Coaching history"),
      tableNote("Historical coaches are supplemented from the Basketball-Reference anchor corpus."),
      renderTable(
        [
          { key: "season_year", label: "Season" },
          { key: "coach_name", label: "Coach" },
          {
            key: "wins",
            label: "Record",
            format: (_v, row) => `${formatValue(row.wins)}-${formatValue(row.losses)}`,
          },
        ],
        coaches,
      ),
    ]),
  );
}

const ROUND_LABELS: Record<number, string> = {
  1: "First Round",
  2: "Conference Semifinals",
  3: "Conference Finals",
  4: "Finals",
};

function renderPlayoffSeries(container: HTMLElement, series: Row[]): void {
  container.append(
    el("section", {}, [
      sectionHeading("team-playoffs", "Playoff series by season"),
      tableNote(
        "Series results are re-derived from game-level wins and losses, not fact_playoff_series counters.",
      ),
      renderTable(
        [
          { key: "season_id", label: "Season" },
          {
            key: "round_number",
            label: "Round",
            format: (v) => ROUND_LABELS[Number(v)] ?? `Round ${formatValue(v)}`,
          },
          { key: "opponent_name", label: "Opponent" },
          {
            key: "wins",
            label: "Result",
            format: (_v, row) => `${formatValue(row.wins)}-${formatValue(row.losses)}`,
          },
        ],
        series,
      ),
    ]),
  );
}

function renderLineups(container: HTMLElement, lineups: Row[]): void {
  container.append(
    el("section", {}, [
      sectionHeading("team-lineups", "Most-used lineup outings"),
      tableNote(
        "Lineup rows are single-game samples ordered by minutes, not full-season lineup totals.",
      ),
      renderTable(
        [
          { key: "season_year", label: "Season" },
          { key: "total_min", label: "MP" },
          { key: "pts_per48", label: "PTS/48" },
          { key: "avg_net_rating", label: "NetRtg" },
        ],
        lineups,
      ),
    ]),
  );
}

function renderRanks(container: HTMLElement, rows: Row[]): void {
  container.append(
    el("section", {}, [
      sectionHeading("team-ranks", "League ranks"),
      renderTable(
        [
          { key: "season_id", label: "Season" },
          { key: "season_type", label: "Type" },
          {
            key: "pts_rank",
            label: "PTS",
            format: (_v, row) => `${formatValue(row.pts_rank)} (${formatValue(row.pts_pg)})`,
          },
          {
            key: "reb_rank",
            label: "TRB",
            format: (_v, row) => `${formatValue(row.reb_rank)} (${formatValue(row.reb_pg)})`,
          },
          {
            key: "ast_rank",
            label: "AST",
            format: (_v, row) => `${formatValue(row.ast_rank)} (${formatValue(row.ast_pg)})`,
          },
          {
            key: "opp_pts_rank",
            label: "Opp PTS",
            format: (_v, row) =>
              `${formatValue(row.opp_pts_rank)} (${formatValue(row.opp_pts_pg)})`,
          },
        ],
        rows,
      ),
    ]),
  );
}

function renderOpponentStats(container: HTMLElement, rows: Row[]): void {
  container.append(
    el("section", {}, [
      sectionHeading("team-opponent", "Opponent four-factors"),
      tableNote("Tracking-era defensive and hustle fields; pre-1996-97 seasons may be absent."),
      renderTable(
        [
          { key: "season_year", label: "Season" },
          { key: "season_type", label: "Type" },
          { key: "gp", label: "G" },
          { key: "avg_def_rating", label: "DRtg" },
          { key: "avg_net_rating", label: "NetRtg" },
          { key: "avg_opp_efg_pct", label: "Opp eFG%", format: formatPct },
          { key: "avg_opp_tov_pct", label: "Opp TOV%", format: formatPct },
          { key: "avg_opp_oreb_pct", label: "Opp ORB%", format: formatPct },
          { key: "avg_opp_fta_rate", label: "Opp FTr", format: formatPct },
        ],
        rows,
        [
          { label: "Context", span: 3 },
          { label: "Ratings", span: 2 },
          { label: "Four Factors", span: 4 },
        ],
      ),
    ]),
  );
}

function renderFranchiseHistory(container: HTMLElement, history: Row[]): void {
  container.append(
    el("section", {}, [
      sectionHeading("team-franchise", "Franchise history"),
      renderTable(
        [
          { key: "nickname", label: "Name" },
          { key: "city", label: "City" },
          { key: "abbreviation", label: "Abbr." },
          { key: "valid_from", label: "From" },
          { key: "valid_to", label: "To" },
        ],
        history,
      ),
    ]),
  );
}

function renderSeasons(container: HTMLElement, seasons: Row[]): void {
  container.append(
    el("section", {}, [
      sectionHeading("team-seasons", "Season by season"),
      renderTable(
        [
          { key: "season_year", label: "Season" },
          { key: "season_type", label: "Type" },
          { key: "gp", label: "G" },
          { key: "avg_pts", label: "PTS" },
          { key: "avg_reb", label: "TRB" },
          { key: "avg_ast", label: "AST" },
          { key: "fg_pct", label: "FG%", format: formatPct },
          { key: "avg_pace", label: "Pace" },
          { key: "avg_ortg", label: "ORtg" },
          { key: "avg_drtg", label: "DRtg" },
          { key: "avg_net_rtg", label: "NetRtg" },
        ],
        seasons,
        [
          { label: "Context", span: 3 },
          { label: "Per Game", span: 4 },
          { label: "Ratings", span: 4 },
        ],
      ),
    ]),
  );
}

function renderRecentGames(container: HTMLElement, games: Row[]): void {
  container.append(
    el("section", {}, [
      sectionHeading("team-games", "Recent games"),
      renderTable(
        [
          { key: "game_id", label: "", headerLabel: "Box score", render: boxScoreCell },
          { key: "game_date", label: "Date", format: (v) => String(v).slice(0, 10) },
          { key: "location", label: "", headerLabel: "Home or away" },
          { key: "opponent", label: "Opp" },
          { key: "team_pts", label: "PTS" },
          { key: "opp_pts", label: "Opp PTS" },
          { key: "result", label: "Result" },
        ],
        games,
      ),
    ]),
  );
}

// ---------------------------------------------------------------------------
// BBR season context (SRS / pace / ratings / four factors both ways) and
// all-time head-to-head records vs every opponent franchise.
// ---------------------------------------------------------------------------

function renderSeasonContext(container: HTMLElement, rows: Row[]): void {
  container.append(
    el("section", {}, [
      sectionHeading("team-season-context", "Season context (SRS, pace, four factors)"),
      tableNote(
        "Basketball-Reference team summaries: Simple Rating System, pythagorean expected record, pace, and four factors for both offense and defense.",
      ),
      renderTable(
        [
          { key: "season", label: "Season" },
          { key: "w", label: "W" },
          { key: "l", label: "L" },
          { key: "pw", label: "pyth W" },
          { key: "pl", label: "pyth L" },
          { key: "srs", label: "SRS" },
          { key: "sos", label: "SOS" },
          { key: "pace", label: "Pace" },
          { key: "o_rtg", label: "ORtg" },
          { key: "d_rtg", label: "DRtg" },
          { key: "n_rtg", label: "NetRtg" },
          { key: "e_fg_percent", label: "eFG%" },
          { key: "tov_percent", label: "TOV%" },
          { key: "orb_percent", label: "ORB%" },
          { key: "ft_fga", label: "FT/FGA" },
          { key: "opp_e_fg_percent", label: "Opp eFG%" },
          { key: "opp_tov_percent", label: "Opp TOV%" },
          { key: "drb_percent", label: "DRB%" },
          { key: "opp_ft_fga", label: "Opp FT/FGA" },
          { key: "attend_g", label: "Att./G" },
        ],
        rows,
        [
          { label: "Record", span: 5 },
          { label: "Rating", span: 6 },
          { label: "Offense", span: 4 },
          { label: "Defense", span: 4 },
          { label: "", span: 1 },
        ],
      ),
    ]),
  );
}

function renderHeadToHead(container: HTMLElement, rows: Row[]): void {
  container.append(
    el("section", {}, [
      sectionHeading("team-head-to-head", "All-time head-to-head"),
      tableNote("Regular-season records vs every opponent franchise, all eras combined."),
      renderTable(
        [
          { key: "opponent_abbreviation", label: "Opp" },
          { key: "opponent_name", label: "Opponent" },
          { key: "gp", label: "G" },
          { key: "wins", label: "W" },
          { key: "losses", label: "L" },
          { key: "avg_pts_scored", label: "PTS for" },
          { key: "avg_pts_allowed", label: "PTS against" },
          { key: "avg_margin", label: "Margin" },
        ],
        rows,
      ),
    ]),
  );
}
