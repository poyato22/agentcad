// Plan 8 G4 e2e: view modes on the sheet (section caps, explode, X-ray), critique-round and
// presented-view cards in the thread, the full-window view and the model warning.
// Chromium with SwiftShader software WebGL (stencil buffer included).
import type { Page } from "@playwright/test";
import { expect, openPartView, test } from "./fixtures";

test.use({ launchOptions: { args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] } });

test.beforeEach(async ({ fake, page }) => {
  await fake.reset("geometry");
  await page.setViewportSize({ width: 1440, height: 900 });
});

/** The sheet's View ▾ menu (U2b) hosts the view modes and the part / build scope. */
async function viewItem(page: Page, label: string): Promise<void> {
  await page.getByRole("button", { name: "View options" }).click();
  await page.locator("#view-menu [role^=menuitem]", { hasText: label }).first().click();
}

async function openDesign(page: Page): Promise<void> {
  await page.goto("/");
  await openPartView(page);
  await expect(page.locator("[data-testid=viewer-canvas]")).toHaveAttribute("data-bbox-size", /.+/, { timeout: 15_000 });
}

/** RGBA of canvas pixels around CSS point (x, y) of `selector`, read back (preserveDrawingBuffer). */
async function pixelsAt(page: Page, selector: string, x: number, y: number, r = 3): Promise<number[][]> {
  return page.evaluate(({ selector, x, y, r }) => {
    const src = document.querySelector<HTMLCanvasElement>(selector);
    if (!src) throw new Error("no canvas");
    const c = document.createElement("canvas");
    c.width = src.width; c.height = src.height;
    const g = c.getContext("2d");
    if (!g) throw new Error("no 2d");
    g.drawImage(src, 0, 0);
    const k = src.width / src.getBoundingClientRect().width;
    const out: number[][] = [];
    for (let dy = -r; dy <= r; dy++) for (let dx = -r; dx <= r; dx++) {
      const d = g.getImageData(Math.round((x + dx) * k), Math.round((y + dy) * k), 1, 1).data;
      out.push([d[0], d[1], d[2], d[3]]);
    }
    return out;
  }, { selector, x, y, r });
}

test("section_caps_close_the_cut_in_the_part_color", async ({ page }) => {
  await openDesign(page);
  await page.locator('[data-part="pi_battery"] .tree-name').first().click();
  await expect(page.locator("[data-testid=viewer-canvas]")).toHaveAttribute("data-bbox-size", /^40\.00,26\.00,12\.00$/, { timeout: 10_000 });
  await viewItem(page, "Section");
  await page.getByRole("button", { name: "Z", exact: true }).click();
  const handle = page.getByTestId("section-handle");
  await expect(handle).toBeVisible();
  const box = (await handle.boundingBox())!;
  const host = (await page.getByTestId("viewer").boundingBox())!;
  // the handle sits where the plane crosses the middle of the battery: the cut, seen from above
  const at = { x: box.x + box.width / 2 - host.x, y: box.y + box.height / 2 - host.y };
  await expect.poll(async () => {
    const px = await pixelsAt(page, "[data-testid=viewer-canvas]", at.x, at.y, 4);
    // closed: no see-through pixel inside the cut (without caps the open box shows the empty sheet)
    const opaque = px.every((p) => p[3] > 200);
    // colored: the cap grey (lifted well above the --mesh-face grey #2a2a2a), hatched (lines in the
    // edge grey give a second, lighter tone)
    const capped = px.filter((p) => p[0] > 90).length;
    const tones = new Set(px.map((p) => Math.round(p[0] / 20))).size;
    return opaque && capped === px.length && tones >= 2;
  }, { timeout: 10_000 }).toBe(true);

  // dragging the handle moves the plane (the slider follows)
  const slider = page.getByTestId("section-slider");
  const before = Number(await slider.inputValue());
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2 - 40, { steps: 5 });
  await page.mouse.up();
  await expect.poll(async () => Number(await slider.inputValue())).not.toBe(before);
  await page.screenshot({ path: test.info().outputPath("section-caps.png") });
});

const viewStream = (page: Page) => page.locator("[data-dock] [data-stream=view]");
const tile = (page: Page, kind: string) => viewStream(page).locator(`[data-filmstrip] [data-tile-kind="${kind}"]`);

test("view_stream_shows_the_newest_large_the_rest_in_a_filmstrip_and_one_live_card", async ({ page }) => {
  // Plan 9 D4 / D13: views and critique rounds sit in the dock's View stream, not the conversation
  await openDesign(page);
  const strip = viewStream(page).locator("[data-filmstrip] [data-tile]");
  await expect(strip).toHaveCount(7, { timeout: 10_000 });                 // 5 views + 2 critique rounds
  await expect(page.getByRole("log", { name: "Conversation" }).locator("[data-testid=view-card], [data-testid=critique-card]")).toHaveCount(0);
  await expect(viewStream(page).locator("[data-filmstrip] [aria-current=true]")).toHaveCount(1);
  // pick a view: it shows large, the full dock width, and is the one live card
  await tile(page, "exploded").click();
  const big = viewStream(page).getByTestId("view-card");
  await expect(big).toHaveCount(1);
  await expect(big).toHaveAttribute("data-kind", "exploded");
  await expect(big).toHaveAttribute("data-live", "true");
  // the View column's full width (P10 E6: a column of the dock)
  expect((await big.boundingBox())!.width).toBeGreaterThanOrEqual((await viewStream(page).boundingBox())!.width - 30);
  await expect(big.getByTestId("live-view")).toHaveAttribute("data-state", "ready", { timeout: 10_000 });
  await expect(page.locator("[data-testid=live-view]")).toHaveCount(1);
  // a critique round shows large too and expands to its issues
  await tile(page, "critique").first().click();
  const crit = viewStream(page).getByTestId("critique-card");
  await expect(crit).toHaveCount(1);
  await crit.locator("button").first().click();
  await expect(crit.getByTestId("critique-details")).toBeVisible();
  // the small local model on shape work is flagged
  await expect(page.getByTestId("model-warning")).toBeVisible();
  await page.screenshot({ path: test.info().outputPath("thread-cards.png") });
});

test("view_card_opens_full_window_steps_and_closes", async ({ page }) => {
  await openDesign(page);
  await tile(page, "section").click();
  const section = viewStream(page).locator("[data-testid=view-card][data-kind=section]");
  await section.getByTestId("view-card-open").click();
  const imm = page.getByTestId("view-immersive");
  await expect(imm).toHaveAttribute("data-kind", "section");
  await expect(imm).toHaveAttribute("data-ready", "true");
  await expect(page.getByTestId("vimm-pos")).toHaveText("3 / 5");
  await expect(imm.getByTestId("live-view")).toHaveAttribute("data-state", "ready", { timeout: 10_000 });
  await expect(imm.getByRole("button", { name: "section" })).toHaveAttribute("aria-pressed", "true");
  // no card holds a context while the full-window view is open
  await expect(page.locator("[data-testid=view-card][data-live=true]")).toHaveCount(0);
  await page.screenshot({ path: test.info().outputPath("immersive-section.png") });
  await page.keyboard.press("Meta+]");
  await expect(imm).toHaveAttribute("data-kind", "exploded");
  await page.keyboard.press("Meta+[");
  await page.keyboard.press("Meta+[");
  await expect(imm).toHaveAttribute("data-kind", "internal");
  await page.keyboard.press("Escape");
  await expect(imm).toHaveCount(0);
  await expect(page.locator("[data-testid=view-card][data-live=true]")).toHaveCount(1);
});

test("reduced_motion_pauses_the_orbit", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await openDesign(page);
  await tile(page, "orbit").click();
  const orbit = viewStream(page).locator("[data-testid=view-card][data-kind=orbit]");
  await expect(orbit).toHaveAttribute("data-live", "true");
  await expect(orbit.getByTestId("live-view")).toHaveAttribute("data-orbit", "paused");
  await orbit.getByTestId("view-card-play").click();
  await expect(orbit.getByTestId("live-view")).toHaveAttribute("data-orbit", "playing");
});
