import { api, type Row } from "../api.ts";
import { announceStatus, el, formatValue, navigateToDetail, playerPhoto } from "../dom.ts";
import { focusHeaderSearch } from "../headerSearch.ts";

const NAV_TILES: { id: string; label: string; description: string }[] = [
  { id: "players", label: "Players", description: "Browse and search player profiles." },
  { id: "teams", label: "Teams", description: "Browse and search franchise profiles." },
  { id: "standings", label: "Standings", description: "Conference standings by season." },
  { id: "draft-awards", label: "Draft & Awards", description: "Draft classes and season awards." },
];

function tile(className: string, children: (Node | string)[], onClick?: () => void): HTMLElement {
  const el_ = el("button", { type: "button", className: `home-tile ${className}` }, children);
  if (onClick) el_.addEventListener("click", onClick);
  return el_;
}

export function renderHome(container: HTMLElement): void {
  container.replaceChildren();
  announceStatus("Home");

  const searchTile = tile("home-tile-search", [
    el("h3", { text: "Search" }),
    el("p", { className: "muted", text: "Find any player or team by name." }),
  ]);
  searchTile.addEventListener("click", () => focusHeaderSearch());

  const navTiles = NAV_TILES.map((t) =>
    tile(
      "home-tile-nav",
      [el("h3", { text: t.label }), el("p", { className: "muted", text: t.description })],
      () => navigateToDetail(t.id),
    ),
  );

  const featuredCard = el("div", { className: "home-tile home-tile-featured" }, [
    el("p", { className: "muted", text: "Loading…" }),
  ]);
  const spotlightCard = el("div", { className: "home-tile home-tile-spotlight" }, [
    el("p", { className: "muted", text: "Loading…" }),
  ]);

  container.append(
    el("div", { className: "home-tiles" }, [searchTile, ...navTiles, featuredCard, spotlightCard]),
  );

  void loadFeaturedPlayer(featuredCard);
  void loadTeamSpotlight(spotlightCard);
}

async function loadFeaturedPlayer(container: HTMLElement): Promise<void> {
  try {
    const player = await api.getFeaturedPlayer();
    container.replaceChildren();
    if (!player) {
      container.append(el("p", { className: "muted", text: "No featured player available." }));
      return;
    }
    const sub = [player.position, player.team_abbreviation].filter(Boolean).join(" · ");
    const ppg =
      player.career_ppg !== null && player.career_ppg !== undefined
        ? `${Number(player.career_ppg).toFixed(1)} career PPG`
        : null;
    const card = el("button", { type: "button", className: "featured-player-card" }, [
      playerPhoto(player.player_id, "player-photo-header", String(player.full_name)),
      el(
        "div",
        {},
        [
          el("h4", { text: "Featured Player" }),
          el("p", { text: String(player.full_name) }),
          el("p", { className: "muted", text: sub }),
          ppg ? el("p", { className: "muted", text: ppg }) : null,
        ].filter((n): n is HTMLElement => n !== null),
      ),
    ]);
    card.addEventListener("click", () => navigateToDetail("players", String(player.player_id)));
    container.append(card);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Failed to load featured player.";
    container.replaceChildren(el("p", { className: "muted", text: `Error: ${message}` }));
  }
}

async function loadTeamSpotlight(container: HTMLElement): Promise<void> {
  try {
    const teams = await api.getTeamsByConference();
    container.replaceChildren();
    if (teams.length === 0) {
      container.append(el("p", { className: "muted", text: "No team data available." }));
      return;
    }
    const east = teams.filter((t) => t.conference === "East");
    const west = teams.filter((t) => t.conference === "West");
    const column = (label: string, rows: Row[]): HTMLElement =>
      el("div", { className: "conference-column" }, [
        el("h5", { text: label }),
        el(
          "ul",
          { className: "team-chip-list" },
          rows.map((t) => {
            const li = el("li", {});
            const button = el("button", {
              type: "button",
              className: "team-chip",
              text: formatValue(t.abbreviation),
              "aria-label": String(t.team_name),
            });
            button.addEventListener("click", () => navigateToDetail("teams", String(t.team_id)));
            li.append(button);
            return li;
          }),
        ),
      ]);
    container.append(
      el("h4", { text: "Teams by Conference" }),
      el("div", { className: "conference-columns" }, [
        column("Eastern", east),
        column("Western", west),
      ]),
    );
  } catch (err) {
    const message = err instanceof Error ? err.message : "Failed to load teams.";
    container.replaceChildren(el("p", { className: "muted", text: `Error: ${message}` }));
  }
}
