// Plan 5 A3: dragging parts in the tree with a real mouse (pointer events, not HTML5 DnD), from
// the name button — run in Chromium and in WebKit (the app's webview engine) — plus the keyboard way.
import type { Page } from "@playwright/test";
import { expect, test, type FakeControl } from "./fixtures";

const QUAD = "q0quadframe0";

test.beforeEach(async ({ fake }) => { await fake.reset("builds"); });
test.afterEach(async ({ fake }) => { await fake.reset("base"); });

const tree = (page: Page) => page.getByTestId("part-tree");
const rows = async (page: Page) => tree(page).locator("[data-part]").evaluateAll((els) => els.map((e) => `${e.getAttribute("data-depth")}${e.getAttribute("data-part")}`));
const lastReorder = async (fake: FakeControl) => (await fake.calls()).filter((c) => c.method === "POST" && c.path === "/parts/reorder").at(-1)?.body;

/** Press on `from`'s name, move in steps to a point of `to`'s row (fraction of its height), release. */
async function mouseDrag(page: Page, from: string, to: string, at: number): Promise<void> {
  const src = await tree(page).getByRole("button", { name: from, exact: true }).boundingBox();
  const dst = await tree(page).locator(`[data-part="${to}"] .tree-row`).boundingBox();
  if (!src || !dst) throw new Error("rows not laid out");
  await page.mouse.move(src.x + 10, src.y + src.height / 2);
  await page.mouse.down();
  await page.mouse.move(dst.x + 40, dst.y + dst.height * at, { steps: 10 });
  await page.mouse.up();
}

test("drag_from_the_name_reorders_and_nests_then_the_keyboard_way", async ({ page, fake }) => {
  await page.goto("/");
  await expect(tree(page).getByRole("button", { name: "Quad arm", exact: true })).toBeVisible();
  expect(await rows(page)).toEqual(["0quad_arm", "0top_plate"]);

  // the plate above the arm (the arm row's top quarter)
  await mouseDrag(page, "top plate", "quad_arm", 0.1);
  await expect.poll(() => rows(page)).toEqual(["0top_plate", "0quad_arm"]);
  expect(await lastReorder(fake)).toEqual({ project_id: QUAD, parent_id: null, ids: ["top_plate", "quad_arm"] });

  // onto the middle of the arm: the plate nests under it
  await mouseDrag(page, "top plate", "quad_arm", 0.5);
  await expect.poll(() => rows(page)).toEqual(["0quad_arm", "1top_plate"]);
  expect(await lastReorder(fake)).toEqual({ project_id: QUAD, parent_id: "quad_arm", ids: ["top_plate"] });
  // the drag ended on a name button: that is not a selection
  await expect(tree(page).getByRole("button", { name: "top plate", exact: true })).toHaveAttribute("aria-pressed", "false");

  // a parent onto its own child: no indicator, nothing sent
  const before = (await fake.calls()).length;
  await mouseDrag(page, "Quad arm", "top_plate", 0.5);
  await expect(tree(page).locator("[data-drop]")).toHaveCount(0);
  expect((await fake.calls()).slice(before).some((c) => c.path === "/parts/reorder")).toBe(false);

  // keyboard: Alt+← moves it back out, Alt+↑ above the arm
  await tree(page).getByRole("button", { name: "top plate", exact: true }).focus();
  await page.keyboard.press("Alt+ArrowLeft");
  await expect.poll(() => rows(page)).toEqual(["0quad_arm", "0top_plate"]);
  await page.keyboard.press("Alt+ArrowUp");
  await expect.poll(() => rows(page)).toEqual(["0top_plate", "0quad_arm"]);
  await expect(tree(page).getByRole("button", { name: "top plate", exact: true })).toBeFocused();
});
