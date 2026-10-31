import { expect, test } from "./fixtures";

test.beforeEach(async ({ fake }) => { await fake.reset("left"); });

test("left_pane_shows_parts_with_mass_the_total_and_the_bom_no_history_no_variables", async ({ page }) => {
  await page.goto("/");
  const left = page.locator("[data-region=left]");
  await expect(left.getByRole("button", { name: "mount plate", exact: true })).toBeVisible();
  await expect(left.getByTestId("row-mass")).toHaveText("4.8 g");
  await expect(left.getByTestId("parts-total")).toContainText("4.8 g");
  // Plan 7 U2b (R14): the Variables panel left the main UI
  await expect(left.getByRole("region", { name: "Variables" })).toHaveCount(0);
  await expect(page.getByTestId("var-thickness")).toHaveCount(0);
  // Plan 9 D9: the BOM summary replaced History
  await expect(left.getByTestId("bom-summary")).toBeVisible();
  await expect(page.getByTestId("run-row")).toHaveCount(0);
});
