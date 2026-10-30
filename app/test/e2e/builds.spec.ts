// Plan 5 A3: several builds at once, the part tree (drag + keyboard), part colors, live
// variables, feature ops from a pick, the code editor, and editing alongside an agent (409s).
import type { Page } from "@playwright/test";
import { expect, test, type FakeCall, type FakeControl } from "./fixtures";

const QUAD = "q0quadframe0";
const BENCH = "b0benchrig00";

test.beforeEach(async ({ fake }) => { await fake.reset("builds"); });
test.afterEach(async ({ fake }) => { await fake.reset("base"); });

const tree = (page: Page) => page.getByTestId("part-tree");
/** Picks an item from the viewer's View menu (Plan 7 U2b: the display controls live there). */
async function viewMenu(page: Page, item: string): Promise<void> {
  await page.getByRole("button", { name: "View options" }).click();
  await page.locator("#view-menu [role^=menuitem]", { hasText: item }).click();
}
const callsTo = async (fake: FakeControl, method: string, path: string | RegExp): Promise<FakeCall[]> =>
  (await fake.calls()).filter((c) => c.method === method && (typeof path === "string" ? c.path === path : path.test(c.path)));

async function open(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.getByRole("tab", { name: "Quad frame" })).toHaveAttribute("aria-selected", "true");
  await expect(tree(page).getByRole("button", { name: "Quad arm", exact: true })).toBeVisible();
}

test("build_tabs_open_switch_close_with_their_own_parts", async ({ page, fake }) => {
  await open(page);
  await expect(tree(page).getByRole("button", { name: "top plate", exact: true })).toBeVisible();
  await expect(tree(page).getByRole("button", { name: "bench plate", exact: true })).toHaveCount(0);

  await page.getByRole("button", { name: "All builds and the Trash" }).click();
  await page.getByRole("menuitemradio", { name: /Bench rig/ }).click();
  await expect(page.getByRole("tab", { name: "Bench rig" })).toHaveAttribute("aria-selected", "true");
  await expect(tree(page).getByRole("button", { name: "bench plate", exact: true })).toBeVisible();
  await expect(tree(page).getByRole("button", { name: "Quad arm", exact: true })).toHaveCount(0);
  expect((await callsTo(fake, "GET", "/parts")).some((c) => c.query.project_id === BENCH)).toBe(true);
  const put = (await callsTo(fake, "PUT", "/profile")).at(-1);
  expect(put?.body).toEqual({ active_project_id: BENCH, open_project_ids: [QUAD, BENCH] });

  // keyboard: ← to the first tab, Enter switches
  await page.getByRole("tab", { name: "Bench rig" }).focus();
  await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("tab", { name: "Quad frame" })).toHaveAttribute("aria-selected", "true");
  await expect(tree(page).getByRole("button", { name: "Quad arm", exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Close Bench rig" }).click();
  await expect(page.getByTestId("build-tabs").getByRole("tab")).toHaveCount(1);
});

test("new_build_rename_trash_restore", async ({ page, fake }) => {
  await open(page);
  await page.getByRole("button", { name: "Add a build" }).click();
  await page.getByRole("textbox", { name: "New build name" }).fill("Gimbal mount");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("tab", { name: "Gimbal mount" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("left-pane")).toContainText("No parts in this build yet.");

  await page.getByRole("tab", { name: "Gimbal mount" }).dblclick();
  await page.getByRole("textbox", { name: "Rename Gimbal mount" }).fill("Gimbal arm");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("tab", { name: "Gimbal arm" })).toBeVisible();

  await page.getByRole("button", { name: "All builds and the Trash" }).click();
  await page.getByRole("menuitem", { name: "Move “Gimbal arm” to the Trash" }).click();
  await expect(page.getByRole("tab", { name: "Gimbal arm" })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Quad frame" })).toHaveAttribute("aria-selected", "true");
  expect((await callsTo(fake, "DELETE", /^\/projects\//)).length).toBe(1);

  await page.getByRole("button", { name: "All builds and the Trash" }).click();
  await page.getByRole("menuitem", { name: /Gimbal arm\s*Restore/ }).click();
  await expect(page.getByRole("tab", { name: "Gimbal arm" })).toHaveAttribute("aria-selected", "true");
});

// dragging and the keyboard way: builds-drag.spec.ts (Chromium and WebKit)

test("color_a_part_from_the_palette_and_the_viewer_paints_it", async ({ page, fake }) => {
  await open(page);
  await tree(page).getByRole("button", { name: "top plate", exact: true }).click();
  await page.getByRole("button", { name: /Appearance of top plate/ }).click();
  await page.getByRole("radio", { name: "Teal" }).click();
  await expect(page.getByRole("button", { name: /Appearance of top plate: Teal/ })).toBeVisible();
  expect((await callsTo(fake, "PATCH", "/parts/top_plate")).at(-1)?.body).toEqual({ color: "#2f9a9c" });
  await expect(page.getByTestId("viewer-canvas")).toHaveAttribute("data-shown", "top_plate:#2f9a9c");
  // the whole build: both parts, each in its color
  await viewMenu(page, "Whole build");
  await expect(page.getByTestId("viewer-canvas")).toHaveAttribute("data-shown", "quad_arm:#d9822b top_plate:#2f9a9c");
});

test("click_a_face_to_add_a_hole_there", async ({ page, fake }) => {
  await open(page);
  await tree(page).getByRole("button", { name: "top plate", exact: true }).click();
  const canvas = page.getByTestId("viewer-canvas");
  await expect(canvas).toHaveAttribute("data-bbox-size", "40.00,40.00,3.00", { timeout: 15_000 });
  const b = await canvas.boundingBox();
  await page.mouse.click((b?.x ?? 0) + (b?.width ?? 0) / 2, (b?.y ?? 0) + (b?.height ?? 0) / 2);
  // a click says what it hit (no ops callout); Modify… there opens the feature ops (U2a)
  const label = page.getByTestId("pick-label");
  await expect(label).toContainText("Face · planar · 1,600 mm²");
  await expect(page.getByTestId("ops-menu")).toHaveCount(0);
  await label.getByRole("button", { name: "Modify…" }).click();
  const menu = page.getByTestId("ops-menu");
  await expect(menu).toBeVisible();
  await expect(menu).toContainText(/f\d+ · plane facing \+Z · 1600 mm²/);
  await menu.getByRole("textbox", { name: "Diameter (mm)" }).fill("4");
  await menu.getByRole("button", { name: "Add hole" }).click();
  await expect(menu).toHaveCount(0);
  const op = (await callsTo(fake, "POST", "/parts/top_plate/ops")).at(-1)?.body as { op: string; target: { face_id: string }; params: { diameter: number; through: boolean }; base_version: number };
  expect(op.op).toBe("hole");
  expect(op.target.face_id).toMatch(/^f\d+$/);
  expect(op.params).toMatchObject({ diameter: 4, through: true });
  expect(op.base_version).toBe(1);
  await expect(page.locator("[data-you-edit] .cv-body").last()).toContainText("You · added a Ø4 mm through hole on top plate");
});

test("a_second_pick_while_the_callout_is_open_starts_it_fresh", async ({ page, fake }) => {
  await open(page);
  await tree(page).getByRole("button", { name: "top plate", exact: true }).click();
  const canvas = page.getByTestId("viewer-canvas");
  await expect(canvas).toHaveAttribute("data-bbox-size", "40.00,40.00,3.00", { timeout: 15_000 });
  const b = await canvas.boundingBox();
  const cx = (b?.x ?? 0) + (b?.width ?? 0) / 2; const cy = (b?.y ?? 0) + (b?.height ?? 0) / 2;
  await page.mouse.click(cx, cy);
  await page.getByTestId("pick-label").getByRole("button", { name: "Modify…" }).click();
  const menu = page.getByTestId("ops-menu");
  await menu.getByRole("radio", { name: "Shell" }).check();
  await menu.getByRole("textbox", { name: "Wall (mm)" }).fill("9");
  await page.mouse.click(cx + 30, cy + 10);                 // pick again, elsewhere on the top
  await expect(menu.getByRole("radio", { name: "Hole" })).toBeChecked();
  await expect(menu.getByRole("textbox", { name: "Diameter (mm)" })).not.toHaveValue("9");
  await menu.getByRole("button", { name: "Add hole" }).click();
  await expect(menu).toHaveCount(0);
  const op = (await callsTo(fake, "POST", "/parts/top_plate/ops")).at(-1)?.body as { op: string; params: { point: number[] }; base_version: number };
  expect(op.op).toBe("hole");
  expect(op.base_version).toBe(1);
});

test("modify_from_the_part_menu_rounds_an_edge", async ({ page, fake }) => {
  await open(page);
  await tree(page).getByRole("button", { name: "top plate", exact: true }).click();
  await tree(page).getByRole("button", { name: "Actions for top plate" }).click();
  await page.getByRole("menuitem", { name: "Modify…" }).click();
  const menu = page.getByTestId("ops-menu");
  await menu.getByRole("radio", { name: "Fillet" }).check();
  await menu.getByRole("combobox").selectOption({ index: 1 });
  await menu.getByRole("textbox", { name: "Radius (mm)" }).fill("0.5");
  await page.keyboard.press("Enter");
  await expect(menu).toHaveCount(0);
  const op = (await callsTo(fake, "POST", "/parts/top_plate/ops")).at(-1)?.body as { op: string; target: { edge_ids: string[] }; params: { radius: number } };
  expect(op).toMatchObject({ op: "fillet", params: { radius: 0.5 } });
  expect(op.target.edge_ids).toHaveLength(1);
});

test("code_tab_edit_save_and_a_conflict", async ({ page, fake }) => {
  await open(page);
  await tree(page).getByRole("button", { name: "Quad arm", exact: true }).click();
  await viewMenu(page, "Part script");
  const editor = page.getByTestId("source-editor");
  await expect(editor.locator(".cm-content")).toContainText('"thickness": 4.0,  # mises: min=2 max=12');
  await expect(editor.locator(".cm-mises-op")).toHaveCount(0);
  expect((await callsTo(fake, "GET", "/parts/quad_arm/source")).length).toBeGreaterThan(0);

  await editor.locator(".cm-content").click();
  await page.keyboard.press("ControlOrMeta+End");
  await page.keyboard.type("\n# a note from me");
  await expect(editor.getByRole("status")).toHaveText("Unsaved changes");
  await page.keyboard.press("ControlOrMeta+s");
  await expect(editor.getByRole("status")).toHaveText("Saved and rebuilt.");
  const put = (await callsTo(fake, "PUT", "/parts/quad_arm/source")).at(-1)?.body as { source: string; base_version: number };
  expect(put.base_version).toBe(5);
  expect(put.source).toContain("# a note from me");

  // the agent edits while you type: the save is refused, you keep yours on top of theirs
  await page.keyboard.type("\n# and another");
  await fake.agentEdit({ part_id: "quad_arm", vars: { thickness: 7 }, run_id: "a3quadrun001" });
  await expect(editor.getByRole("alert")).toContainText("Saving will ask what to keep.");
  await editor.getByRole("button", { name: "Save" }).click();
  await expect(editor.locator("[data-conflict]")).toContainText("Your edit was not saved.");
  await editor.getByRole("button", { name: "Keep mine" }).click();
  await expect(editor.getByRole("status")).toHaveText("Saved and rebuilt.");
  const puts = await callsTo(fake, "PUT", "/parts/quad_arm/source");
  expect(puts.map((c) => (c.body as { base_version: number }).base_version)).toEqual([5, 6, 7]);
  await expect(page.locator("[data-you-edit] .cv-body").last()).toHaveText("You · edited the script of Quad arm");
});

test("a_failed_rebuild_names_the_op", async ({ page }) => {
  await open(page);
  await tree(page).getByRole("button", { name: "Quad arm", exact: true }).click();
  await viewMenu(page, "Part script");
  const editor = page.getByTestId("source-editor");
  await expect(editor.locator(".cm-content")).toContainText("Box(");
  await editor.locator(".cm-content").click();
  await page.keyboard.press("ControlOrMeta+End");
  await page.keyboard.type('\nraise ValueError("wall too thin")');
  await page.keyboard.press("ControlOrMeta+s");
  await expect(editor.getByRole("alert")).toHaveText("The part didn't rebuild, nothing changed: ValueError: wall too thin");
});
