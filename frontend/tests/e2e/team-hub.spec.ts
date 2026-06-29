import { expect, test } from "@playwright/test";

const status = {
  ok: true,
  endpoint_count: 15,
  data_state: "passed",
  data_state_reason: "verified",
  data_verified: true,
  data_stale: false,
  latest_pipeline_run_id: "run-1",
  latest_pipeline_stage: "load",
  latest_pipeline_status: "success",
  latest_pipeline_started_at: "2026-06-29T00:00:00Z",
  latest_dq_status: "passed",
};

const catalog = {
  tabs: [
    {
      id: "overview",
      label: "Overview",
      description: "Overview",
      scope: "team",
      datasets: ["roster"],
      default_dataset: "roster",
    },
    {
      id: "franchise",
      label: "Franchise",
      description: "Franchise",
      scope: "team",
      datasets: ["franchise-arc"],
      default_dataset: "franchise-arc",
    },
  ],
  datasets: [
    {
      id: "roster",
      label: "Roster",
      endpoint_name: "team_roster",
      scope: "team",
      description: "Roster rows",
      supports_export: true,
      supports_include_inactive_games: false,
      default_visible_columns: ["full_name", "per"],
      columns: [
        { key: "full_name", label: "Player", default_visible: true, numeric: false },
        { key: "per", label: "PER", default_visible: true, numeric: true },
      ],
    },
    {
      id: "franchise-arc",
      label: "Franchise Arc",
      endpoint_name: "franchise_arc",
      scope: "team",
      description: "Season-by-season franchise wins and losses.",
      supports_export: true,
      supports_include_inactive_games: false,
      default_visible_columns: ["season_end_year", "wins", "losses", "win_pct"],
      columns: [
        { key: "season_end_year", label: "Season", default_visible: true, numeric: true },
        { key: "wins", label: "W", default_visible: true, numeric: true },
        { key: "losses", label: "L", default_visible: true, numeric: true },
        { key: "win_pct", label: "Win%", default_visible: true, numeric: true },
      ],
    },
  ],
};

const roster = {
  dataset: "roster",
  endpoint_name: "team_roster",
  params: { team_identifier: "BOS" },
  row_count: 2,
  columns: catalog.datasets[0].columns,
  default_visible_columns: catalog.datasets[0].default_visible_columns,
  rows: [
    { full_name: "Jayson Tatum", per: 22.3 },
    { full_name: "Jaylen Brown", per: 18.6 },
  ],
};

const franchiseArcRows = [
  { season_end_year: 2023, wins: 57, losses: 25, win_pct: 0.695 },
  { season_end_year: 2024, wins: 64, losses: 18, win_pct: 0.78 },
];

const franchiseArc = {
  dataset: "franchise-arc",
  endpoint_name: "franchise_arc",
  params: { team_identifier: "BOS" },
  row_count: franchiseArcRows.length,
  columns: catalog.datasets[1].columns,
  default_visible_columns: catalog.datasets[1].default_visible_columns,
  rows: franchiseArcRows,
};

test.beforeEach(async ({ page }) => {
  await page.route("http://127.0.0.1:8765/api/status", async (route) => {
    await route.fulfill({ json: status });
  });
  await page.route("http://127.0.0.1:8765/api/endpoints/team-hub", async (route) => {
    await route.fulfill({ json: catalog });
  });
  await page.route("http://127.0.0.1:8765/api/teams/BOS/summary", async (route) => {
    await route.fulfill({
      json: {
        identifier: "BOS",
        display_name: "Boston Celtics",
        leagues: ["NBA"],
        default_season: 2024,
        available_seasons: [2024, 2023],
        hero_stats: {
          team: "BOS",
          season: 2024,
          wins: 64,
          losses: 18,
          win_pct: 0.78,
        },
        roster,
        franchise_arc: franchiseArcRows,
      },
    });
  });
  await page.route("http://127.0.0.1:8765/api/teams/BOS/franchise-arc", async (route) => {
    await route.fulfill({ json: franchiseArc });
  });
  await page.route("http://127.0.0.1:8765/api/teams/BOS/export?dataset=franchise-arc", async (route) => {
    await route.fulfill({
      body: "season_end_year,wins,losses,win_pct\n2024,64,18,0.780\n",
      headers: { "content-type": "text/csv; charset=utf-8" },
    });
  });
});

test("loads the team hub, switches tabs, and exports CSV", async ({ page }) => {
  await page.goto("/teams/BOS?season=2024");

  await expect(page.getByRole("heading", { name: "Boston Celtics" })).toBeVisible();
  await expect(page.getByText("Jayson Tatum")).toBeVisible();

  await page.getByRole("button", { name: "Franchise" }).click();
  await expect(page).toHaveURL(/tab=franchise/);
  await expect(page.getByRole("heading", { name: "Franchise Arc" })).toBeVisible();
  await expect(page.getByRole("table").getByText("2,024")).toBeVisible();
  await expect(page.getByRole("table").getByText("64")).toBeVisible();

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "CSV" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("BOS-franchise-arc.csv");
});
