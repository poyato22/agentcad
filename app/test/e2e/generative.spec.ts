// Plan 9 D14: generative design against the fake core (its routes replay the real core's
// topopt fixtures) — from a part's menu: the setup, the density converging live, the variant
// grid with CalculiX's numbers and ✓ / ✗, Accept adding the new part; and the Studies entry.
import type { Page } from "@playwright/test";
import { expect, showParts, test, type FakeCall, type FakeControl } from "./fixtures";

test.beforeEach(async ({ fake }) => { await fake.reset("builds"); });
test.afterEach(async ({ fake }) => { await fake.reset("base"); });

const tree = (page: Page) => page.getByTestId("part-tree");
const rowOf = (page: Page, id: string) => tree(page).locator(`[data-part="${id}"] > .tree-row`);
const callsTo = async (fake: FakeControl, method: string, path: string | RegExp): Promise<FakeCall[]> =>
  (await fake.calls()).filter((c) => c.method === method && (typeof path === "string" ? c.path === path : path.test(c.path)));

test("generative_design_from_the_part_menu_runs_and_accepts_a_variant", async ({ page, fake }) => {
  await page.goto("/");
  await showParts(page);
  await expect(tree(page).getByRole("button", { name: "top plate", exact: true })).toBeVisible();
  await rowOf(page, "top_plate").click({ button: "right" });
  await page.getByRole("menuitem", { name: "Generative design…" }).click();
  const dlg = page.getByRole("dialog", { name: "Generative design" });
  await expect(dlg).toBeVisible();
  await expect(dlg.getByRole("heading", { name: /top plate/ })).toBeVisible();
  // the part in 3D: hover names a face, an armed Pick takes the clicked one
  const view = dlg.getByTestId("gd-pick-view");
  await expect(view).toHaveAttribute("data-state", "ok");
  const box = await view.locator("canvas").boundingBox();
  if (!box) throw new Error("no pick canvas");
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await expect(dlg.getByTestId("gd-pick-caption")).toContainText(/f\d+ · /);
  await dlg.getByRole("button", { name: "Add face to keep" }).click();
  await dlg.getByRole("button", { name: "Pick keep face 1 on the part" }).click();
  await expect(dlg.getByTestId("gd-pick-caption")).toContainText("Click a face for keep face 1");
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await expect(dlg.getByRole("button", { name: "Pick keep face 1 on the part" })).toHaveAttribute("aria-pressed", "false");
  await expect(dlg.getByLabel("Keep face 1", { exact: true })).toHaveValue(/^f\d+$/);
  await dlg.getByRole("button", { name: "Remove keep face 1" }).click();
  await dlg.getByLabel("Held face 1", { exact: true }).selectOption("x-min");
  await dlg.getByLabel("Load 1 face", { exact: true }).selectOption("x-max");
  await dlg.getByLabel("Load 1 force (N)").fill("40");
  await dlg.getByLabel("Number of variants").fill("2");
  await dlg.getByRole("button", { name: "Generate" }).click();

  const posted = (await callsTo(fake, "POST", "/topopt"))[0];
  expect(posted.body).toMatchObject({
    part_id: "top_plate", fixtures: [{ kind: "fixed", face: "x-min" }],
    load_cases: [{ loads: [{ face: "x-max", newtons: 40, direction: "-z" }] }], max_variants: 2,
  });
  // running: live progress and the density preview
  const run = dlg.getByTestId("gd-run");
  await expect(run).toHaveAttribute("data-status", "running");
  await expect(dlg.getByTestId("gd-status")).toContainText("Running");
  await expect(dlg.getByTestId("gd-preview")).toBeVisible();
  // done: the variant grid, recommended first, verified in CalculiX
  await expect(run).toHaveAttribute("data-status", "done", { timeout: 15_000 });
  const cards = dlg.getByTestId("gd-variants").locator("[data-variant]");
  await expect(cards).toHaveCount(2);
  const best = cards.first();
  await expect(best).toHaveAttribute("data-recommended", "");
  await expect(best.getByText("Recommended")).toBeVisible();
  await expect(best.getByTestId("gd-verdict")).toHaveText("✓ Verified");
  await expect(best.getByText(/^\d[\d.]* g$/)).toBeVisible();
  await expect(best.getByText(/ MPa$/)).toBeVisible();
  await expect(best.locator("img.gd-render")).toBeVisible();
  await page.screenshot({ path: "test-results/generative-panel-done.png" });
  // Accept → a new part in the build
  await best.getByRole("button", { name: /Accept/ }).click();
  await expect(dlg.getByRole("status")).toContainText("top_plate_generative");
  expect((await callsTo(fake, "POST", /^\/topopt\/to_[0-9a-f]{12}\/accept$/))[0].body).toMatchObject({ mode: "new" });
  await page.keyboard.press("Escape");
  await expect(dlg).toHaveCount(0);
  await expect(tree(page).locator('[data-part="top_plate_generative"]')).toBeVisible();
  // FW-app I3: the run you started from the panel is in the dock's Simulations stream too
  await expect(page.locator("[data-dock] [data-stream=sim] [data-record=topopt] [data-testid=topopt-card]")).toHaveCount(1);
});

test("generative_design_can_be_cancelled_and_opened_from_studies", async ({ page, fake }) => {
  await page.goto("/");
  await showParts(page);
  await expect(tree(page).getByRole("button", { name: "top plate", exact: true })).toBeVisible();
  await tree(page).getByRole("button", { name: "top plate", exact: true }).click();
  await page.keyboard.press("Meta+2");
  // U-D: the manual setup (where the entry lives) is behind "Set up manually"
  await page.getByTestId("manual-setup-toggle").click();
  const open = page.getByTestId("open-generative");
  await expect(open).toBeVisible();
  await open.click();
  const dlg = page.getByRole("dialog", { name: "Generative design" });
  await expect(dlg).toBeVisible();
  // an incomplete setup is refused before anything is sent
  await dlg.getByRole("button", { name: "Remove held face 1" }).click();
  await dlg.getByRole("button", { name: "Generate" }).click();
  await expect(dlg.getByRole("alert")).toContainText("Hold the part");
  expect(await callsTo(fake, "POST", "/topopt")).toHaveLength(0);
  await dlg.getByRole("button", { name: "Add held face" }).click();
  await dlg.getByRole("button", { name: "Generate" }).click();
  await expect(dlg.getByTestId("gd-run")).toHaveAttribute("data-status", "running");
  await dlg.getByRole("button", { name: "Cancel" }).click();
  await expect(dlg.getByTestId("gd-run")).toHaveAttribute("data-status", "cancelled");
  expect(await callsTo(fake, "POST", /^\/topopt\/to_[0-9a-f]{12}\/cancel$/)).toHaveLength(1);
});
