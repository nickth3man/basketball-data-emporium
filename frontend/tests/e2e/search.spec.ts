import { expect, test } from "@playwright/test";

/**
 * E2E specs for the player search input on the `/players` landing page.
 *
 * Mirrors the mocking pattern in `player-hub.spec.ts`: every backend call
 * is fulfilled by an in-process `page.route` handler — no live Python
 * backend is required, the Next.js dev server is the only live process.
 *
 * The search route handler below is a URL glob that inspects the
 * `term` query parameter so individual tests can exercise the
 * "has results" / "no results" / "term too short" branches without
 * a per-test handler.
 */

const status = {
  ok: true,
  endpoint_count: 61,
};

const catalog = {
  tabs: [
    { id: "overview", label: "Overview", description: "Overview", scope: "player", datasets: ["career"], default_dataset: "career" },
    { id: "career", label: "Career", description: "Career", scope: "player", datasets: ["career"], default_dataset: "career" },
  ],
  datasets: [
    {
      id: "career",
      label: "Career",
      endpoint_name: "player_career_stats",
      scope: "player",
      description: "Career rows",
      supports_export: true,
      default_visible_columns: ["season", "team_name_abbr", "points_per_game"],
      columns: [
        { key: "season", label: "Season", default_visible: true, numeric: false },
        { key: "team_name_abbr", label: "Team", default_visible: true, numeric: false },
        { key: "points_per_game", label: "Points", default_visible: true, numeric: true },
      ],
    },
  ],
};

const career = {
  dataset: "career",
  endpoint_name: "player_career_stats",
  params: { player_identifier: "jamesle01" },
  row_count: 1,
  columns: catalog.datasets[0].columns,
  default_visible_columns: catalog.datasets[0].default_visible_columns,
  rows: [
    { season: "2023-24", team_name_abbr: "LAL", points_per_game: 25.7, total_rebounds_per_game: 7.3, assists_per_game: 8.3 },
  ],
};

const searchResults: ReadonlyArray<{ name: string; identifier: string; leagues: string[] }> = [
  { name: "LeBron James", identifier: "jamesle01", leagues: ["NBA"] },
];

test.beforeEach(async ({ page }) => {
  await page.route("http://127.0.0.1:8765/api/status", async (route) => {
    await route.fulfill({ json: status });
  });
  await page.route("http://127.0.0.1:8765/api/endpoints/player-hub", async (route) => {
    await route.fulfill({ json: catalog });
  });

  // Search route — URL glob so the handler can dispatch on the query string.
  // Any term that starts with "ja" (covers "ja", "james", "jamesle", …)
  // returns the LeBron James stub; everything else returns an empty array,
  // which the component renders as the "No players found" message.
  await page.route("http://127.0.0.1:8765/api/players/search*", async (route) => {
    const url = new URL(route.request().url());
    const term = url.searchParams.get("term") ?? "";
    const results = term.startsWith("ja") ? searchResults : [];
    await route.fulfill({ json: results });
  });

  await page.route("http://127.0.0.1:8765/api/players/jamesle01/summary", async (route) => {
    await route.fulfill({
      json: {
        identifier: "jamesle01",
        display_name: "LeBron James",
        leagues: [],
        default_season: 2024,
        available_seasons: [2024, 2023],
        hero_stats: career.rows[0],
        career,
      },
    });
  });
  await page.route("http://127.0.0.1:8765/api/players/jamesle01/career", async (route) => {
    await route.fulfill({ json: career });
  });
});

test("shows results after typing a 2-char term", async ({ page }) => {
  await page.goto("/players");

  const input = page.getByPlaceholder("Search players");
  await input.fill("ja");

  // Debounce is 250ms; the request is mocked and resolves immediately,
  // so the result button should be visible well under the 5s timeout.
  await expect(page.getByRole("button", { name: "LeBron James" })).toBeVisible({ timeout: 5_000 });
});

test("shows empty state for no matches", async ({ page }) => {
  await page.goto("/players");

  const input = page.getByPlaceholder("Search players");
  await input.fill("zzz");

  await expect(page.getByText("No players found")).toBeVisible({ timeout: 5_000 });
});

test("does not search for 1-char terms", async ({ page }) => {
  const searchRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/players/search")) {
      searchRequests.push(request.url());
    }
  });

  await page.goto("/players");
  const input = page.getByPlaceholder("Search players");
  await input.fill("j");

  // Wait well past the 250ms debounce — if the guard isn't working,
  // a request would have fired by now.
  await page.waitForTimeout(500);

  expect(searchRequests).toHaveLength(0);
  // Results panel only renders for trimmedTerm.length >= 2, so it
  // must not be present at all for a single-character term.
  await expect(page.getByRole("button", { name: "LeBron James" })).toHaveCount(0);
});

test("Enter submits and navigates to the canonical player page", async ({ page }) => {
  await page.goto("/players");

  const input = page.getByPlaceholder("Search players");
  await input.fill("james");
  await expect(page.getByRole("button", { name: "LeBron James" })).toBeVisible({ timeout: 5_000 });

  await input.press("Enter");

  await expect(page).toHaveURL(/\/players\/jamesle01$/);
  await expect(page.getByRole("heading", { name: "LeBron James" })).toBeVisible();
});
