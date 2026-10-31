// Plan 7 U8 (R20–R22): the parts manager against the fake core — right-click menu, one-click
// appearance with finish presets, multi-select, duplicate / delete / export, and the viewer
// background saved to the profile.
import type { Page } from "@playwright/test";
import { expect, test, type FakeCall, type FakeControl } from "./fixtures";

test.beforeEach(async ({ fake }) => { await fake.reset("builds"); });
test.afterEach(async ({ fake }) => { await fake.reset("base"); });

const tree = (page: Page) => page.getByTestId("part-tree");
const rowOf = (page: Page, id: string) => tree(page).locator(`[data-part="${id}"] > .tree-row`);
const callsTo = async (fake: FakeControl, method: string, path: string | RegExp): Promise<FakeCall[]> =>
  (await fake.calls()).filter((c) => c.method === method && (typeof path === "string" ? c.path === path : path.test(c.path)));

async function open(page: Page): Promise<void> {
  await page.goto("/");
  await expect(tree(page).getByRole("button", { name: "Quad arm", exact: true })).toBeVisible();
}

test("right_click_opens_the_part_menu_with_useful_items", async ({ page }) => {
  await open(page);
  await rowOf(page, "top_plate").click({ button: "right" });
  const menu = page.getByRole("menu", { name: "top plate actions" });
  await expect(menu).toBeVisible();
  await expect(menu.getByRole("menuitem")).toHaveText([/Rename/, "Appearance…", /Hide/, "Isolate", "Measure", "Add to assembly",
    /Duplicate/, "Export STEP", "Export STL", /Properties/, "Modify…", "Generative design…", "Delete"]);
  // the right-click selected the row
  await expect(tree(page).getByRole("button", { name: "top plate", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
});

test("one_click_appearance_finish_and_color", async ({ page, fake }) => {
  await open(page);
  await tree(page).getByRole("button", { name: "top plate", exact: true }).click();
  await rowOf(page, "top_plate").locator(".tree-swatch").click();
  const pop = page.getByTestId("appearance-popover");
  await expect(pop).toBeVisible();
  await pop.getByRole("radio", { name: "Polished metal" }).click();
  await expect.poll(async () => (await callsTo(fake, "PATCH", "/parts/top_plate")).at(-1)?.body)
    .toEqual({ appearance: { preset: "polished_metal", color: "#e6e8eb", metalness: 1, roughness: 0.06, opacity: 1 } });
  await expect(page.getByRole("button", { name: "Appearance of top plate: Polished metal" })).toBeVisible();
  // a color keeps the finish (the core recolors the appearance)
  await pop.getByRole("radio", { name: "Steel" }).click();
  await expect.poll(async () => (await callsTo(fake, "PATCH", "/parts/top_plate")).at(-1)?.body).toEqual({ color: "#5b7fa6" });
  await expect(page.getByTestId("viewer-canvas")).toHaveAttribute("data-shown", "top_plate:#5b7fa6");
  await page.keyboard.press("Escape");
  await expect(pop).toHaveCount(0);
  // listed again after a reload: the look is the core's
  await page.reload();
  await expect(page.getByRole("button", { name: "Appearance of top plate: Polished metal" })).toBeVisible();
});

test("multi_select_then_hide_both_from_the_menu", async ({ page }) => {
  await open(page);
  await tree(page).getByRole("button", { name: "Quad arm", exact: true }).click();
  await tree(page).getByRole("button", { name: "top plate", exact: true }).click({ modifiers: ["Meta"] });
  await expect(tree(page).locator(".tree-row.sel")).toHaveCount(2);
  await rowOf(page, "quad_arm").click({ button: "right" });
  await page.getByRole("menuitem", { name: "Hide 2 parts" }).click();
  await expect(tree(page).locator(".tree-row.is-hidden")).toHaveCount(2);
});

test("duplicate_export_and_delete_from_the_menu", async ({ page, fake }) => {
  await open(page);
  await rowOf(page, "top_plate").click({ button: "right" });
  await page.getByRole("menuitem", { name: /Duplicate/ }).click();
  await expect(tree(page).getByRole("button", { name: "top plate copy", exact: true })).toBeVisible();
  await expect(tree(page).getByRole("button", { name: "top plate copy", exact: true })).toHaveAttribute("aria-pressed", "true");

  await rowOf(page, "top_plate").click({ button: "right" });
  await page.getByRole("menuitem", { name: "Export STEP" }).click();
  await expect(page.getByText("Exported top plate.step to Downloads")).toBeVisible();
  expect((await callsTo(fake, "POST", "/parts/top_plate/export")).at(-1)?.body).toEqual({ format: "step" });

  await rowOf(page, "top_plate_copy").click({ button: "right" });
  await page.getByRole("menuitem", { name: "Delete" }).click();
  await page.getByRole("menuitem", { name: "Click again to delete" }).click();
  await expect(tree(page).getByRole("button", { name: "top plate copy", exact: true })).toHaveCount(0);
  expect(await callsTo(fake, "DELETE", "/parts/top_plate_copy")).toHaveLength(1);
});

test("viewer_background_from_the_view_menu_is_saved_to_the_profile", async ({ page, fake }) => {
  await open(page);
  const vp = page.getByTestId("viewer");
  await expect(vp).toHaveAttribute("data-bg-mode", "theme");
  // View ▸ Background… (the sheet keeps only Part | Assembly and View)
  await page.getByRole("button", { name: "View options" }).click();
  await page.getByRole("menuitem", { name: "Background…" }).click();
  await page.getByRole("radio", { name: "Soft gradient" }).click();
  await expect(vp).toHaveAttribute("data-bg-mode", "gradient");
  await expect(vp).toHaveAttribute("data-bg", "light");
  await expect.poll(async () => (await callsTo(fake, "PUT", "/profile")).at(-1)?.body)
    .toEqual({ viewer_background: { mode: "gradient", colors: ["#fdfdfe", "#c9d3df"] } });
  // the profile has it on the next launch
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await expect(page.getByTestId("viewer")).toHaveAttribute("data-bg-mode", "gradient");
});
