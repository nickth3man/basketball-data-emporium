import { expect, test } from "@playwright/test";

/**
 * Mobile (Pixel 7) E2E spec for the player hub.
 *
 * The `playwright.config.ts` projects are `chromium` (Desktop Chrome) and
 * `mobile` (Pixel 7). The config has no per-project `testMatch`, so this
 * file would otherwise be picked up by both projects. The `beforeEach`
 * below guards on the project name so the mobile-specific assertions
 * (overflow check, Pixel 7 viewport) only run against the `mobile` project.
 */

const status = {
  ok: true,
  endpoint_count: 61,
};

const catalog = {
  tabs: [
    { id: "overview", label: "Overview", description: "Overview", scope: "player", datasets: ["career"], default_dataset: "career" },
    { id: "career", label: "Career", description: "Career", scope: "player", datasets: ["career"], default_dataset: "career" },
    { id: "shooting", label: "Shooting", description: "Shooting", scope: "player", datasets: ["adjusted-shooting", "shooting-breakdown"], default_dataset: "adjusted-shooting" },
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
    {
      id: "adjusted-shooting",
      label: "Adjusted Shooting",
      endpoint_name: "player_adjusted_shooting",
      scope: "player",
      description: "Adjusted shooting",
      supports_export: true,
      default_visible_columns: ["year_id", "adjusted_true_shooting_percentage"],
      columns: [
        { key: "year_id", label: "Season", default_visible: true, numeric: false },
        { key: "adjusted_true_shooting_percentage", label: "Adj TS%", default_visible: true, numeric: true },
      ],
    },
    {
      id: "shooting-breakdown",
      label: "Shooting Breakdown",
      endpoint_name: "player_shot_charts",
      scope: "season",
      description: "Breakdown",
      supports_export: true,
      default_visible_columns: ["split_value", "field_goal_percentage"],
      columns: [
        { key: "split_value", label: "Split", default_visible: true, numeric: false },
        { key: "field_goal_percentage", label: "FG%", default_visible: true, numeric: true },
      ],
    },
  ],
};

const career = {
  dataset: "career",
  endpoint_name: "player_career_stats",
  params: { player_identifier: "jamesle01" },
  row_count: 2,
  columns: catalog.datasets[0].columns,
  default_visible_columns: catalog.datasets[0].default_visible_columns,
  rows: [
    { season: "2022-23", team_name_abbr: "LAL", points_per_game: 28.9, total_rebounds_per_game: 8.3, assists_per_game: 6.8 },
    { season: "2023-24", team_name_abbr: "LAL", points_per_game: 25.7, total_rebounds_per_game: 7.3, assists_per_game: 8.3 },
  ],
};

test.describe.configure({ mode: "serial" });

// Scoping guard: only run on the `mobile` project (Pixel 7 viewport).
// On the `chromium` (Desktop Chrome) project the mobile assertions are
// meaningless, so we skip with a clear annotation in the report.
test.beforeEach(async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "mobile-only spec (Pixel 7 viewport)");

  await page.route("http://127.0.0.1:8765/api/status", async (route) => {
    await route.fulfill({ json: status });
  });
  await page.route("http://127.0.0.1:8765/api/endpoints/player-hub", async (route) => {
    await route.fulfill({ json: catalog });
  });
  await page.route("http://127.0.0.1:8765/api/players/jamesle01/summary", async (route) => {
    await route.fulfill({
      json: {
        identifier: "jamesle01",
        display_name: "LeBron James",
        leagues: [],
        default_season: 2024,
        available_seasons: [2024, 2023],
        hero_stats: career.rows[1],
        career,
      },
    });
  });
  await page.route("http://127.0.0.1:8765/api/players/jamesle01/career", async (route) => {
    await route.fulfill({ json: career });
  });
  await page.route("http://127.0.0.1:8765/api/players/jamesle01/adjusted-shooting", async (route) => {
    await route.fulfill({
      json: {
        dataset: "adjusted-shooting",
        endpoint_name: "player_adjusted_shooting",
        params: { player_identifier: "jamesle01" },
        row_count: 1,
        columns: catalog.datasets[1].columns,
        default_visible_columns: catalog.datasets[1].default_visible_columns,
        rows: [{ year_id: "2023-24", adjusted_true_shooting_percentage: 110 }],
      },
    });
  });
  await page.route("http://127.0.0.1:8765/api/players/jamesle01/seasons/2024/shooting-breakdown", async (route) => {
    await route.fulfill({
      json: {
        dataset: "shooting-breakdown",
        endpoint_name: "player_shot_charts",
        params: { player_identifier: "jamesle01", season_end_year: 2024 },
        row_count: 1,
        columns: catalog.datasets[2].columns,
        default_visible_columns: catalog.datasets[2].default_visible_columns,
        rows: [{ split_value: "At Rim", field_goal_percentage: 0.741 }],
      },
    });
  });
});

test("player hub renders on mobile viewport", async ({ page }) => {
  await page.goto("/players/jamesle01?season=2024");

  await expect(page.getByRole("heading", { name: "LeBron James" })).toBeVisible();

  // Layout sanity check: the page must not overflow horizontally on a
  // 412px-wide Pixel 7. We compare `documentElement.scrollWidth` (the
  // full content width, including overflow) against the viewport.
  const overflows = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  );
  expect(overflows).toBe(false);
});

test("tab navigation is reachable on mobile", async ({ page }) => {
  await page.goto("/players/jamesle01?season=2024");

  const tabNav = page.getByRole("navigation", { name: "Player Hub tabs" });
  await expect(tabNav).toBeVisible();

  await page.getByRole("button", { name: "Career" }).click();
  await expect(page).toHaveURL(/tab=career/);
});

test("data table is scrollable horizontally on mobile", async ({ page }) => {
  await page.goto("/players/jamesle01?season=2024&tab=stats");

  const scrollRegion = page.getByTestId("data-table-scroll").first();
  await expect(scrollRegion).toBeVisible();
  const metrics = await scrollRegion.evaluate((node) => ({
    clientWidth: node.clientWidth,
    scrollWidth: node.scrollWidth,
  }));
  expect(metrics.scrollWidth).toBeGreaterThanOrEqual(metrics.clientWidth);
  const pageOverflows = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  );
  expect(pageOverflows).toBe(false);
});
