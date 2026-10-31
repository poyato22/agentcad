// R1 + R17 + Plan 9: the Home build grid on launch (no top bar, D23), opening a build into the
// shell (a top bar with exactly four tabs, D9), ⌂ / ⌘0 back, the build covers (D24), Onshape and
// the parts library as Home sub-views (D22), and Trash with Undo / Delete forever (D30).
import { expect, openHome, test } from "./fixtures";

test.beforeEach(async ({ fake, page }) => {
  await fake.reset("base");
  await openHome(page);
  await page.setViewportSize({ width: 1440, height: 900 });
});

test("launch_shows_the_build_grid_with_no_top_bar", async ({ page }) => {
  await page.goto("/");
  const home = page.locator('[data-testid="home"]');
  await expect(home).toBeVisible();
  await expect(home.getByTitle("Open Default")).toBeVisible();
  await expect(home.getByText("New build")).toBeVisible();
  await expect(page.locator('[data-region="viewer"]')).toHaveCount(0);
  // D23: no top bar on Home — not hidden, not there
  await expect(page.locator('[data-region="topbar"]')).toHaveCount(0);
  await expect(page.locator(".topbar")).toHaveCount(0);
});

test("searching_filters_the_tiles", async ({ page }) => {
  await page.goto("/");
  const home = page.locator('[data-testid="home"]');
  await page.getByLabel("Search builds").fill("zzz");
  await expect(home.getByTitle("Open Default")).toHaveCount(0);
  await page.getByLabel("Search builds").fill("def");
  await expect(home.getByTitle("Open Default")).toBeVisible();
});

test("opening_a_build_shows_the_top_bar_with_four_tabs_and_home_returns", async ({ page }) => {
  await page.goto("/");
  await page.locator('[data-testid="home"]').getByTitle("Open Default").click();
  await expect(page.locator('[data-region="viewer"]')).toBeVisible();
  await expect(page.locator('[data-testid="home"]')).toHaveCount(0);
  const bar = page.locator('[data-region="topbar"]');
  await expect(bar).toBeVisible();
  await expect(bar.getByRole("tab")).toHaveText(["Design", "Studies", "Print", "BOM"]);
  await expect(bar.getByRole("button", { name: /^Build: Default/ })).toBeVisible();
  await expect(page.getByTestId("build-tabs")).toHaveCount(0);
  // ⌂ returns to the grid, and the top bar goes with it
  await bar.getByRole("button", { name: "Home" }).click();
  await expect(page.locator('[data-testid="home"]')).toBeVisible();
  await expect(page.locator('[data-region="topbar"]')).toHaveCount(0);
});

test("all_parts_overview_opens_and_closes", async ({ page }) => {
  await page.goto("/");
  await page.locator('[data-testid="home"]').getByTitle("Open Default").click();
  await page.getByRole("button", { name: "All parts" }).click();
  await expect(page.getByRole("dialog", { name: "All parts" })).toBeVisible();
  await page.getByRole("button", { name: "Close all-parts view" }).click();
  await expect(page.getByRole("dialog", { name: "All parts" })).toHaveCount(0);
});

// I1 + D23: Home is the whole window — no top bar band, no dead console column.
for (const width of [1440, 1280]) {
  test(`home_fills_the_window_at_${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 });
    await page.goto("/");
    const home = page.locator('[data-testid="home"]');
    await expect(home).toBeVisible();
    const box = (await home.boundingBox())!;
    expect(box.x).toBe(0);
    expect(box.y).toBe(0);
    expect(Math.round(box.width)).toBe(width);
    expect(Math.round(box.height)).toBe(800);
    // its header keeps clear of the macOS traffic lights (78px inset)
    const title = (await home.locator(".home-title").boundingBox())!;
    expect(title.x).toBeGreaterThanOrEqual(78);
    const grid = (await home.locator(".home-grid").boundingBox())!;
    expect(grid.x + grid.width).toBeGreaterThan(width - 4);
  });
}

// D24: the cover is the core's assembly render, shown 16:10 and full-bleed in its tile.
test("tiles_show_the_assembly_cover_full_bleed_16_10", async ({ page, fake }) => {
  await page.goto("/");
  const tile = page.locator('[data-testid="home"] [data-build]').filter({ hasText: "Default" });
  const img = tile.locator("img.tile-img");
  await expect(img).toBeVisible();
  expect((await fake.calls()).some((c) => c.method === "GET" && /^\/projects\/[^/]+\/thumb\.png$/.test(c.path))).toBe(true);
  const thumb = (await tile.locator(".tile-thumb").boundingBox())!;
  expect(Math.abs(thumb.width / thumb.height - 1.6)).toBeLessThan(0.02);
  const ib = (await img.boundingBox())!;
  expect(Math.round(ib.width)).toBe(Math.round(thumb.width));
});

// I7: Home has a visible way into every build's agents.
test("home_agents_button_opens_the_agents_panel", async ({ page }) => {
  await page.goto("/");
  await page.locator('[data-testid="home"]').getByRole("button", { name: /^Agents/ }).click();
  await expect(page.getByRole("dialog", { name: /agent/i })).toBeVisible();
});

// D22: Onshape lives on Home only (and the parts library beside it), never in a build's tabs.
test("onshape_and_the_parts_library_are_home_sub_views", async ({ page }) => {
  await page.goto("/");
  const home = page.locator('[data-testid="home"]');
  await home.getByRole("button", { name: "Onshape", exact: true }).click();
  await expect(home).toHaveAttribute("data-home-view", "onshape");
  await expect(page.locator('[data-page-shell][aria-label="Onshape"]')).toBeVisible();
  await expect(page.locator('[data-region="topbar"]')).toHaveCount(0);
  await home.getByRole("button", { name: /Builds/ }).click();
  await expect(home).toHaveAttribute("data-home-view", "builds");
  await home.getByRole("button", { name: "Parts library" }).click();
  await expect(page.locator('[data-page-shell][aria-label="Parts directory"]')).toBeVisible();
  // ⌘0 lands back on the build grid
  await page.keyboard.press("Meta+0");
  await expect(home).toHaveAttribute("data-home-view", "builds");
});

// D30: delete from a tile → Undo; the Trash deletes forever after a confirm.
test("delete_a_build_then_undo_then_delete_forever", async ({ page, fake }) => {
  await page.goto("/");
  const home = page.locator('[data-testid="home"]');
  await home.getByRole("button", { name: "New build" }).click();
  await page.getByRole("textbox", { name: "New build name" }).fill("Scrap rig");
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "Home" }).click();

  await home.getByRole("button", { name: "Open Scrap rig" }).click({ button: "right" });
  await page.getByRole("menuitem", { name: "Move to Trash" }).click();
  await expect(home.getByRole("button", { name: "Open Scrap rig" })).toHaveCount(0);
  const toast = home.getByRole("status");
  await expect(toast).toContainText("Moved “Scrap rig” to the Trash");
  await toast.getByRole("button", { name: "Undo" }).click();
  await expect(home.getByRole("button", { name: "Open Scrap rig" })).toBeVisible();
  expect((await fake.calls()).some((c) => c.method === "POST" && /\/restore$/.test(c.path))).toBe(true);

  await home.locator("[data-build]", { hasText: "Scrap rig" }).hover();
  await home.getByRole("button", { name: "More for Scrap rig" }).click();
  await page.getByRole("menuitem", { name: "Move to Trash" }).click();
  await home.getByRole("button", { name: /^Trash/ }).click();
  const trashed = home.locator("[data-build]", { hasText: "Scrap rig" });
  await trashed.getByRole("button", { name: "Delete forever" }).click();
  await trashed.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(home.locator("[data-build]", { hasText: "Scrap rig" })).toHaveCount(0);
  expect((await fake.calls()).some((c) => c.method === "POST" && /\/purge$/.test(c.path))).toBe(true);
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
