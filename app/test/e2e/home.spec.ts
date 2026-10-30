// R1 + R17: the Home build grid on launch, opening a build into the shell, and the in-build
// "All parts" overview. The fake core serves no thumbnail route, so tiles fall back to the
// placeholder glyph — the grid, search, open and overview still work.
import { expect, openHome, test } from "./fixtures";

test.beforeEach(async ({ fake, page }) => {
  await fake.reset("base");
  await openHome(page);
  await page.setViewportSize({ width: 1440, height: 900 });
});

test("launch_shows_the_build_grid_not_the_viewer", async ({ page }) => {
  await page.goto("/");
  const home = page.locator('[data-testid="home"]');
  await expect(home).toBeVisible();
  await expect(home.getByTitle("Open Default")).toBeVisible();
  await expect(home.getByText("New build")).toBeVisible();
  await expect(page.locator('[data-region="viewer"]')).toHaveCount(0);
});

test("searching_filters_the_tiles", async ({ page }) => {
  await page.goto("/");
  const home = page.locator('[data-testid="home"]');
  await page.getByLabel("Search builds").fill("zzz");
  await expect(home.getByTitle("Open Default")).toHaveCount(0);
  await page.getByLabel("Search builds").fill("def");
  await expect(home.getByTitle("Open Default")).toBeVisible();
});

test("opening_a_tile_opens_the_build_and_home_button_returns", async ({ page }) => {
  await page.goto("/");
  await page.locator('[data-testid="home"]').getByTitle("Open Default").click();
  await expect(page.locator('[data-region="viewer"]')).toBeVisible();
  await expect(page.locator('[data-testid="home"]')).toHaveCount(0);
  // the Home button returns to the grid
  await page.getByRole("button", { name: "Home" }).click();
  await expect(page.locator('[data-testid="home"]')).toBeVisible();
});

test("all_parts_overview_opens_and_closes", async ({ page }) => {
  await page.goto("/");
  await page.locator('[data-testid="home"]').getByTitle("Open Default").click();
  await page.getByRole("button", { name: "All parts" }).click();
  await expect(page.getByRole("dialog", { name: "All parts" })).toBeVisible();
  await page.getByRole("button", { name: "Close all-parts view" }).click();
  await expect(page.getByRole("dialog", { name: "All parts" })).toHaveCount(0);
});
