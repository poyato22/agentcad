// R1 + R17: the Home build grid on launch, opening a build into the shell, and the in-build
// "All parts" overview. The fake core serves no thumbnail route, so tiles fall back to the
// placeholder glyph — the grid, search, open and overview still work.
import { expect, openHome, openPartView, test } from "./fixtures";

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
  await openPartView(page);                       // Plan 9 D7: All parts is the part viewer's
  await page.getByRole("button", { name: "All parts" }).click();
  await expect(page.getByRole("dialog", { name: "All parts" })).toBeVisible();
  await page.getByRole("button", { name: "Close all-parts view" }).click();
  await expect(page.getByRole("dialog", { name: "All parts" })).toHaveCount(0);
});

// I1: Home spans the whole window under the top bar — no dead console column, no blank band.
for (const width of [1440, 1280]) {
  test(`home_is_full_width_with_no_band_at_${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 });
    await page.goto("/");
    const home = page.locator('[data-testid="home"]');
    await expect(home).toBeVisible();
    const box = (await home.boundingBox())!;
    const bar = (await page.locator('[data-region="topbar"]').boundingBox())!;
    expect(box.x).toBe(0);
    expect(Math.round(box.width)).toBe(width);
    expect(Math.round(box.y)).toBe(Math.round(bar.y + bar.height));
    const grid = (await home.locator(".home-grid").boundingBox())!;
    expect(grid.x + grid.width).toBeGreaterThan(width - 4);
  });
}

// I7: Home has a visible way into every build's agents.
test("home_agents_button_opens_the_agents_panel", async ({ page }) => {
  await page.goto("/");
  await page.locator('[data-testid="home"]').getByRole("button", { name: /^Agents/ }).click();
  await expect(page.getByRole("dialog", { name: /agent/i })).toBeVisible();
});

// m1: returning to Home refetches the builds, so part counts and "last edited" never go stale.
test("home_refetches_builds_each_time_it_shows", async ({ page, fake }) => {
  await page.goto("/");
  await page.locator('[data-testid="home"]').getByTitle("Open Default").click();
  await expect(page.locator('[data-region="viewer"]')).toBeVisible();
  const lists = async () => (await fake.calls()).filter((c) => c.method === "GET" && c.path === "/projects").length;
  const before = await lists();
  await page.getByRole("button", { name: "Home" }).click();
  await expect(page.locator('[data-testid="home"]')).toBeVisible();
  await expect.poll(lists).toBeGreaterThan(before);
});
