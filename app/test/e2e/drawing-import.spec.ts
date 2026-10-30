// Drawing import (R2) against the fake core: a file dropped on a build's viewer becomes a part,
// with a progress card, then a result card (dimension match table, the original drawing beside
// Mises's drawing). The fake serves the real core's DXF import fixtures. Both themes.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const DRAWINGS = resolve(import.meta.dirname, "../../../core/tests/fixtures/drawings");
const SHOTS = process.env.MISES_SHOTS_DIR;
const card = (page: Page) => page.locator("[data-import-cards] .imp-card").first();

async function open(page: Page, theme: "dark" | "light") {
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
}

/** Drag a file over the viewer and (unless `hold`) drop it, as Finder would. */
async function dropFile(page: Page, name: string, bytes: Buffer, opts: { hold?: boolean } = {}) {
  await page.evaluate(({ name, b64, hold }) => {
    const target = document.querySelector("[data-region=viewer]") as HTMLElement;
    const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const dt = new DataTransfer();
    dt.items.add(new File([bin], name));
    const fire = (type: string) => target.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt }));
    fire("dragenter");
    fire("dragover");
    if (!hold) fire("drop");
  }, { name, b64: bytes.toString("base64"), hold: opts.hold ?? false });
}

test.beforeEach(async ({ fake, page }) => {
  await fake.reset("base");
  await page.setViewportSize({ width: 1440, height: 900 });
});

for (const theme of ["dark", "light"] as const) {
  test(`dxf_dropped_on_the_viewer_becomes_a_part_with_a_match_table_${theme}`, async ({ page, fake }) => {
    await open(page, theme);
    const dxf = readFileSync(resolve(DRAWINGS, "plate_holes.dxf"));
    await dropFile(page, "Mounting Plate.dxf", dxf, { hold: true });
    const overlay = page.locator("[data-drawing-drop=overlay]");
    await expect(overlay).toBeVisible();
    await expect(overlay).toContainText("Drop a drawing to make a part");
    await dropFile(page, "Mounting Plate.dxf", dxf);
    await expect(overlay).toHaveCount(0);

    await expect(card(page)).toBeVisible();
    await expect(card(page).getByText("Mounting Plate.dxf")).toBeVisible();
    // progress first (the fake moves one stage per poll), then the result
    await expect(card(page)).toHaveAttribute("data-import-state", "done", { timeout: 10_000 });
    await expect(card(page).locator("[data-result]")).toHaveText(/Made mounting_plate in Default · All \d+ dimensions match the drawing/);
    const rows = card(page).locator("[data-match-table] tbody tr");
    await expect(rows.first()).toBeVisible();
    expect(await rows.count()).toBeGreaterThanOrEqual(6);
    await expect(card(page).locator("[data-match-table]")).toContainText("Length");
    await expect(card(page).locator("[data-match-table]")).toContainText("✓ matches");
    // side by side, both decoded as pictures from blob: URLs
    const imgs = card(page).locator("[data-side-by-side] img");
    await expect(imgs).toHaveCount(2);
    for (const i of [0, 1]) {
      await expect.poll(() => imgs.nth(i).evaluate((el) => (el as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
      expect(await imgs.nth(i).getAttribute("src")).toMatch(/^blob:/);
    }
    const calls = await fake.calls();
    const post = calls.find((c) => c.method === "POST" && c.path === "/imports/drawing");
    expect(post).toBeTruthy();
    expect(calls.some((c) => c.path === "/parts/mounting_plate/drawing.svg")).toBe(true);
    if (SHOTS) await card(page).screenshot({ path: `${SHOTS}/u5-result-${theme}.png` });

    await card(page).getByRole("button", { name: "Open part" }).click();
    await expect.poll(async () => (await fake.calls()).some((c) => c.path === "/parts/mounting_plate/model.glb")).toBe(true);
    // m2: opening the part puts the card away (it no longer floats over the viewer)
    await expect(page.locator("[data-import-cards]")).toHaveCount(0);
  });
}

test("a_drawing_no_model_can_read_explains_which_models_can", async ({ page }) => {
  await open(page, "dark");
  await dropFile(page, "novision.png", readFileSync(resolve(DRAWINGS, "plate_drawing.png")));
  const alert = card(page).getByRole("alert");
  await expect(alert).toContainText("cannot read images");
  await expect(alert).toContainText("Claude");
  await expect(alert).toContainText("DXF drawings need no model");
  await expect(card(page)).toHaveAttribute("data-import-state", "failed");
  if (SHOTS) await card(page).screenshot({ path: `${SHOTS}/u5-novision.png` });
});

test("a_mismatch_is_shown_in_the_table", async ({ page }) => {
  await open(page, "dark");
  await dropFile(page, "plate-mismatch.dxf", readFileSync(resolve(DRAWINGS, "plate_holes.dxf")));
  await expect(card(page)).toHaveAttribute("data-import-state", "done", { timeout: 10_000 });
  await expect(card(page).locator("[data-result]")).toHaveText(/\d+ of \d+ dimensions match/);
  await expect(card(page).locator("[data-row-status=mismatch]")).toContainText("off by -0.5");
});

test("an_image_import_says_it_was_checked_against_the_models_reading", async ({ page }) => {
  await open(page, "dark");
  await dropFile(page, "plate.png", readFileSync(resolve(DRAWINGS, "plate_drawing.png")));
  await expect(card(page)).toHaveAttribute("data-import-state", "done", { timeout: 10_000 });
  await expect(card(page).locator("[data-result]")).toHaveText(/dimensions match the model's reading/);
  await expect(card(page).locator("[data-match-table] caption")).toContainText("the reading itself is the model's");
  await expect(card(page).locator("[data-check=text]")).toContainText("only the model's reading was checked");
  await expect(card(page).locator("[data-check=second-look]")).toContainText("Second opinion");
  if (SHOTS) await card(page).screenshot({ path: `${SHOTS}/u5-reading.png` });
});

test("non_file_drags_do_not_open_the_drop_zone", async ({ page }) => {
  await open(page, "dark");
  await page.evaluate(() => {
    const target = document.querySelector("[data-region=viewer]") as HTMLElement;
    const dt = new DataTransfer();
    dt.setData("text/plain", "mount_plate");
    target.dispatchEvent(new DragEvent("dragenter", { bubbles: true, cancelable: true, dataTransfer: dt }));
  });
  await expect(page.locator("[data-drawing-drop=overlay]")).toHaveCount(0);
});
