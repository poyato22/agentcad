import { expect, test } from "./fixtures";

test.beforeEach(async ({ fake }) => { await fake.reset("left"); });

test("left_pane_shows_parts_and_history_and_no_variables", async ({ page }) => {
  await page.goto("/");
  const left = page.locator("[data-region=left]");
  await expect(left.getByRole("button", { name: "mount plate", exact: true })).toBeVisible();
  // the tree mass: volume x the build default material (Plan 9 B2), not the scenario's stale metrics.mass_g
  await expect(left.getByText("5.7 g")).toBeVisible();
  // Plan 7 U2b (R14): the Variables panel left the main UI
  await expect(left.getByRole("region", { name: "Variables" })).toHaveCount(0);
  await expect(page.getByTestId("var-thickness")).toHaveCount(0);
  const rows = page.getByTestId("run-row");
  await expect(rows).toHaveCount(2);
  await expect(rows.first()).toContainText("designer · thicken mount plate to");
  await expect(rows.nth(1)).toContainText("designer · drill mounting holes");
});

test("history_row_expands", async ({ page }) => {
  await page.goto("/");
  const first = page.getByTestId("run-row").first().getByRole("button").first();
  await first.click();
  await expect(first).toHaveAttribute("aria-expanded", "true");
});
