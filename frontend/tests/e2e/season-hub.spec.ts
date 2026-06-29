import { expect, test } from "@playwright/test";

const status = {
  ok: true,
  endpoint_count: 18,
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

const seasons = {
  seasons: [2026, 2025, 2024],
  default_season: 2026,
};

const standings = {
  dataset: "standings",
  endpoint_name: "season_standings",
  params: { season_end_year: 2024 },
  row_count: 2,
  columns: [
    { key: "season_end_year", label: "Season", default_visible: true, numeric: true },
    { key: "team", label: "Team", default_visible: true, numeric: false },
    { key: "wins", label: "W", default_visible: true, numeric: true },
    { key: "losses", label: "L", default_visible: true, numeric: true },
    { key: "win_pct", label: "Win%", default_visible: true, numeric: true },
    { key: "net_rating", label: "NetRtg", default_visible: true, numeric: true },
  ],
  default_visible_columns: ["season_end_year", "team", "wins", "losses", "win_pct", "net_rating"],
  rows: [
    { season_end_year: 2024, team: "BOS", wins: 64, losses: 18, win_pct: 0.78, net_rating: 11.7 },
    { season_end_year: 2024, team: "DEN", wins: 57, losses: 25, win_pct: 0.695, net_rating: 5.5 },
  ],
};

const scoringLeaders = {
  dataset: "leaders",
  endpoint_name: "season_leaders",
  params: { season_end_year: 2024, season_type: "Regular", stat: "pts" },
  row_count: 2,
  columns: [
    { key: "season_end_year", label: "Season", default_visible: true, numeric: true },
    { key: "full_name", label: "Player", default_visible: true, numeric: false },
    { key: "gp", label: "GP", default_visible: true, numeric: true },
    { key: "avg_pts", label: "PTS", default_visible: true, numeric: true },
    { key: "avg_reb", label: "REB", default_visible: true, numeric: true },
    { key: "avg_ast", label: "AST", default_visible: true, numeric: true },
  ],
  default_visible_columns: ["season_end_year", "full_name", "gp", "avg_pts", "avg_reb", "avg_ast"],
  rows: [
    { season_end_year: 2024, full_name: "Luka Doncic", gp: 70, avg_pts: 33.9, avg_reb: 9.4, avg_ast: 10.0 },
    { season_end_year: 2024, full_name: "Joel Embiid", gp: 39, avg_pts: 31.0, avg_reb: 9.9, avg_ast: 5.0 },
  ],
};

const reboundLeaders = {
  ...scoringLeaders,
  params: { season_end_year: 2024, season_type: "Regular", stat: "reb" },
  rows: [
    { season_end_year: 2024, full_name: "Domantas Sabonis", gp: 82, avg_pts: 19.4, avg_reb: 13.7, avg_ast: 8.2 },
  ],
};

test.beforeEach(async ({ page }) => {
  await page.route("http://127.0.0.1:8765/api/status", async (route) => {
    await route.fulfill({ json: status });
  });
  await page.route("http://127.0.0.1:8765/api/seasons", async (route) => {
    await route.fulfill({ json: seasons });
  });
  await page.route("http://127.0.0.1:8765/api/seasons/2024/standings", async (route) => {
    await route.fulfill({ json: standings });
  });
  await page.route("http://127.0.0.1:8765/api/seasons/2024/leaders?stat=pts", async (route) => {
    await route.fulfill({ json: scoringLeaders });
  });
  await page.route("http://127.0.0.1:8765/api/seasons/2024/leaders?stat=reb", async (route) => {
    await route.fulfill({ json: reboundLeaders });
  });
});

test("loads the season hub and switches leader stat", async ({ page }) => {
  await page.goto("/seasons?season=2024");

  await expect(page.getByRole("heading", { name: "Season Hub" })).toBeVisible();
  await expect(page.getByRole("table").first().getByText("BOS")).toBeVisible();
  await expect(page.getByText("Luka Doncic")).toBeVisible();

  await page.getByLabel("Leader stat").getByRole("button", { name: "REB" }).click();
  await expect(page).toHaveURL(/stat=reb/);
  await expect(page.getByText("Domantas Sabonis")).toBeVisible();
});
