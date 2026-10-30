// Plan 7 U2a: what a click picked (the label), Onshape-style measure (M) with the core's exact
// values, and feedback pins (Comment) that reach the build's agent and stay on the model.
import type { Page } from "@playwright/test";
import { expect, test, type FakeCall, type FakeControl } from "./fixtures";

test.beforeEach(async ({ fake }) => { await fake.reset("builds"); });
test.afterEach(async ({ fake }) => { await fake.reset("base"); });

const tree = (page: Page) => page.getByTestId("part-tree");
const tools = (page: Page) => page.getByRole("toolbar", { name: "View tools" });
const callsTo = async (fake: FakeControl, method: string, path: string): Promise<FakeCall[]> =>
  (await fake.calls()).filter((c) => c.method === method && c.path === path);

async function openPlate(page: Page): Promise<{ cx: number; cy: number }> {
  await page.goto("/");
  await expect(page.getByRole("tab", { name: "Quad frame" })).toHaveAttribute("aria-selected", "true");
  await tree(page).getByRole("button", { name: "top plate", exact: true }).click();
  const canvas = page.getByTestId("viewer-canvas");
  await expect(canvas).toHaveAttribute("data-bbox-size", "40.00,40.00,3.00", { timeout: 15_000 });
  const b = await canvas.boundingBox();
  return { cx: (b?.x ?? 0) + (b?.width ?? 0) / 2, cy: (b?.y ?? 0) + (b?.height ?? 0) / 2 };
}

test("a_click_names_what_it_hit_and_escape_clears_it", async ({ page }) => {
  const { cx, cy } = await openPlate(page);
  await page.mouse.click(cx, cy);
  const label = page.getByTestId("pick-label");
  await expect(label).toContainText("Face · planar · 1,600 mm²");
  await expect(label.getByRole("button", { name: "Modify…" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(label).toHaveCount(0);
});

test("measure_with_m_shows_the_exact_area_then_shift_click_toggles_the_pick", async ({ page, fake }) => {
  const { cx, cy } = await openPlate(page);
  await page.getByTestId("viewer-canvas").focus();
  await page.keyboard.press("m");
  await expect(tools(page).getByRole("button", { name: "measure", exact: true })).toHaveAttribute("aria-pressed", "true");
  const panel = page.getByTestId("measure-panel");
  await expect(panel).toContainText("Click an edge, a face or a corner");
  await page.mouse.click(cx, cy);
  await expect(panel.locator("[data-exact]")).toBeVisible();
  await expect(panel.locator('[data-value="area"] dd')).toHaveText("1,600 mm²");
  const post = (await callsTo(fake, "POST", "/parts/top_plate/measure")).at(-1)?.body as { refs: { face_id: string }[]; base_version: number };
  expect(post.refs).toHaveLength(1);
  expect(post.refs[0].face_id).toMatch(/^f\d+$/);
  expect(post.base_version).toBe(1);
  // the same face again with Shift: dropped from the picks
  await page.keyboard.down("Shift");
  await page.mouse.click(cx + 8, cy + 4);
  await page.keyboard.up("Shift");
  await expect(panel).toContainText("Click an edge, a face or a corner");
  await page.keyboard.press("Escape");
  await expect(panel).toHaveCount(0);
  await expect(tools(page).getByRole("button", { name: "measure", exact: true })).toHaveAttribute("aria-pressed", "false");
});

test("comment_pins_feedback_for_the_agent_and_it_stays_on_the_model", async ({ page, fake }) => {
  const { cx, cy } = await openPlate(page);
  await tools(page).getByRole("button", { name: "comment", exact: true }).click();
  await expect(page.getByText("Click the model where something should change")).toBeVisible();
  await page.mouse.click(cx, cy);
  const box = page.getByTestId("comment-box");
  await expect(box).toContainText("top plate · Face · planar");
  await box.getByRole("textbox", { name: "Your comment" }).fill("add a 3 mm chamfer around this face");
  await box.getByRole("button", { name: "Send to agent" }).click();
  await expect(box).toHaveCount(0);
  const post = (await callsTo(fake, "POST", "/projects/q0quadframe0/feedback")).at(-1)?.body as { part_id: string; face_id: string; point: number[]; text: string };
  expect(post).toMatchObject({ part_id: "top_plate", text: "add a 3 mm chamfer around this face" });
  expect(post.face_id).toMatch(/^f\d+$/);
  expect(post.point).toHaveLength(3);
  const pin = page.getByTestId("pin");
  await expect(pin).toHaveText("1");
  await pin.click();
  await expect(page.getByTestId("pin-note")).toContainText("add a 3 mm chamfer around this face");
  // in the thread: the words and the pin, never the raw location block
  const thread = page.locator("[data-conversation]");
  await expect(thread.locator("[data-feedback-pin]")).toContainText("on top plate", { timeout: 15_000 });
  await expect(thread).not.toContainText("untrusted_user_data");
});
