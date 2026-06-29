import { expect, test } from "@playwright/test";

/**
 * E2E specs for the `/players/[identifier]` not-found / error paths.
 *
 * `PlayerHub` now intercepts `TypedApiError` before it reaches
 * `QueryBoundary` and routes it to the nearest route boundary:
 * - `invalid_player` → `notFound()` → `not-found.tsx`
 * - everything else (`rate_limit_jailed`, `schema_drift`, …) → `throw err` → `error.tsx`
 *
 * The "non-existent route segment" case is covered by the global
 * `app/not-found.tsx` (rendered by Next.js for any unmatched path).
 */

const status = {
  ok: true,
  endpoint_count: 18,
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

test.beforeEach(async ({ page }) => {
  await page.route("http://127.0.0.1:8765/api/status", async (route) => {
    await route.fulfill({ json: status });
  });
  await page.route("http://127.0.0.1:8765/api/endpoints/player-hub", async (route) => {
    await route.fulfill({ json: catalog });
  });
  await page.route("http://127.0.0.1:8765/api/players/doesnotexist01/summary", async (route) => {
    await route.fulfill({
      status: 404,
      json: { detail: { code: "invalid_player", message: "Player not found", detail: {} } },
    });
  });
});

test("unknown identifier shows not-found page", async ({ page }) => {
  await page.goto("/players/doesnotexist01");

  // The `not-found.tsx` route segment renders an `<EmptyState>` with
  // title="Player not found" and a `<Link href="/players">Back to players</Link>`.
  await expect(page.getByText("Player not found")).toBeVisible({ timeout: 5_000 });
  await expect(page.getByRole("link", { name: "Back to players" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Back to players" })).toHaveAttribute("href", "/players");
});

test("non-existent route segment shows not-found", async ({ page }) => {
  // Visit a path that doesn't match any segment. The global
  // `app/not-found.tsx` should render the "Page not found" empty state
  // plus the "Back to players" CTA. The root 404 doesn't render
  // `PlayerHub`, so no `/api/players/...` calls are made — the existing
  // `beforeEach` mocks are harmless to keep.
  await page.goto("/nonexistent");

  await expect(page.getByText("Page not found")).toBeVisible({ timeout: 5_000 });
  await expect(page.getByRole("link", { name: "Back to players" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Back to players" })).toHaveAttribute("href", "/players");
});
