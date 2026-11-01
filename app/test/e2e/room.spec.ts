// P10 FWA (final review I1): with one, two or three dock columns open, at 1280 and 1440 px, the
// model keeps at least 520 px between the floating surfaces — the dock gives way first, then the
// Parts surface folds to its tab by itself (and comes back when the room does) — the exploded
// model is drawn in that free area, and the viewer pill sits centred in it, never under the dock
// or the Parts surface (what doesn't fit folds into its More menu). Robot fixture (glb/make_robot.py).
// MISES_SHOTS=<dir> saves each state.
import { readFileSync } from "node:fs";
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

test.use({ launchOptions: { args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] } });

const SHOTS = process.env.MISES_SHOTS;
const PROJECT = (JSON.parse(readFileSync("test/fixtures/core-ui/projects.json", "utf8")) as { id: string }[])[0].id;

function robot(): Record<string, unknown> {
  const base = JSON.parse(readFileSync("test/fake-core/scenarios/base.json", "utf8")) as Record<string, unknown>;
  const parts = (JSON.parse(readFileSync("test/fake-core/scenarios/robot/parts.json", "utf8")) as Record<string, unknown>[])
    .map((p): Record<string, unknown> => ({ ...p, project_id: PROJECT }));
  const stored = JSON.parse(readFileSync("test/fake-core/scenarios/robot/assembly.json", "utf8")) as Record<string, unknown>;
  const glb = Object.fromEntries(parts.map((p) => [p["part_id"] as string, `glb/${p["part_id"] as string}.glb`]));
  const tree = Object.fromEntries(parts.map((p) => [p["part_id"] as string, { label: p["label"], color: p["color"], order: p["order"] }]));
  return { ...base, script: [], parts, glb, builds: { tree }, assembly: { by_project: { [PROJECT]: stored }, interference: "none" } };
}

/** Opaque model pixels on the assembly canvas between CSS x `from` and `to`. */
async function drawnIn(page: Page, from: number, to: number): Promise<number> {
  return page.evaluate(([a, b]) => {
    const src = document.querySelector<HTMLCanvasElement>("[data-testid=assembly-canvas]");
    if (!src) return 0;
    const r = src.getBoundingClientRect();
    const k = src.width / r.width;
    const x0 = Math.max(0, Math.floor((a - r.left) * k));
    const x1 = Math.min(src.width, Math.ceil((b - r.left) * k));
    if (x1 <= x0) return 0;
    const c = document.createElement("canvas");
    c.width = src.width; c.height = src.height;
    const ctx = c.getContext("2d") as CanvasRenderingContext2D;
    ctx.drawImage(src, 0, 0);
    const d = ctx.getImageData(x0, 0, x1 - x0, c.height).data;
    let n = 0;
    for (let i = 3; i < d.length; i += 4) if ((d[i] ?? 0) >= 200) n++;
    return n;
  }, [from, to]);
}

/** The drawn model's left and right edges (CSS px). */
async function drawnSpan(page: Page): Promise<{ min: number; max: number }> {
  return page.evaluate(() => {
    const src = document.querySelector<HTMLCanvasElement>("[data-testid=assembly-canvas]");
    if (!src) return { min: 0, max: 0 };
    const r = src.getBoundingClientRect();
    const k = src.width / r.width;
    const c = document.createElement("canvas");
    c.width = src.width; c.height = src.height;
    const ctx = c.getContext("2d") as CanvasRenderingContext2D;
    ctx.drawImage(src, 0, 0);
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    let lo = Infinity; let hi = -Infinity;
    for (let y = 0; y < c.height; y++) for (let x = 0; x < c.width; x++) {
      if ((d[(y * c.width + x) * 4 + 3] ?? 0) >= 200) { lo = Math.min(lo, x); hi = Math.max(hi, x); }
    }
    return { min: lo / k + r.left, max: hi / k + r.left };
  });
}

async function box(page: Page, sel: string) {
  const b = await page.locator(sel).first().boundingBox();
  if (!b) throw new Error(`no box for ${sel}`);
  return b;
}

const LAYOUT = {
  1: { view: true, sim: true, text: false },
  2: { view: false, sim: true, text: false },
  3: { view: false, sim: false, text: false },
} as const;
// whether the Parts surface must fold for the model's 520 px (ui/shell/room partsMustFold)
// P11 F2: one dock width whatever is open, so it never has to fold at these widths
const FOLDS: Record<number, Record<number, boolean>> = { 1280: { 1: false, 2: false, 3: false }, 1440: { 1: false, 2: false, 3: false } };

for (const width of [1280, 1440]) {
  for (const n of [1, 2, 3] as const) {
    test(`the model keeps its room and the pill stays in it at ${width} px with ${n} dock column${n > 1 ? "s" : ""}`, async ({ page, fake }) => {
      await fake.reset(robot());
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript((c) => { try { localStorage.setItem("mises.dock.stack", JSON.stringify({ collapsed: c })); } catch { /* none */ } }, LAYOUT[n]);
      await page.goto("/");
      await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-instances", "13");
      // G1: the top area (View or Simulations) and the Conversation: at most two parts
      await expect(page.locator("[data-dock]")).toHaveAttribute("data-cols", String(Math.min(n, 2)));
      await expect(page.locator(".app")).toHaveAttribute("data-left", FOLDS[width][n] ? "rail" : "open");
      await page.getByTestId("pill-explode").click();
      await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-explode", "on");
      await page.waitForTimeout(900);                                       // the explode glides

      const dock = await box(page, "[data-region=console]");
      const left = await box(page, "[data-region=left] [data-surface], [data-region=left][data-surface]");
      const free = dock.x - 12 - (left.x + left.width + 12);
      expect(free).toBeGreaterThanOrEqual(519);
      const pill = await box(page, "[data-testid=viewer-pill]");
      expect(pill.x).toBeGreaterThanOrEqual(left.x + left.width);
      expect(pill.x + pill.width).toBeLessThanOrEqual(dock.x);
      // centred in the free area
      const mid = (left.x + left.width + 12 + dock.x - 12) / 2;
      expect(Math.abs(pill.x + pill.width / 2 - mid)).toBeLessThan(3);
      // every pill control is reachable: inline, or in More
      for (const id of ["pill-play", "pill-section", "pill-explode"]) await expect(page.getByTestId(id)).toBeVisible();
      if (await page.getByTestId("pill-overflow").count()) {
        await page.getByTestId("pill-overflow").click();
        await expect(page.getByTestId("pill-overflow-menu")).toBeVisible();
        const menu = await box(page, "[data-testid=pill-overflow-menu]");
        expect(menu.x + menu.width).toBeLessThanOrEqual(dock.x);
        if (SHOTS) await page.screenshot({ path: `${SHOTS}/room-${width}-${n}col-more.png` });
        await page.getByTestId("pill-overflow").click();
        await expect(page.getByTestId("pill-overflow-menu")).toHaveCount(0);
      } else {
        for (const id of ["pill-xray", "pill-layout", "pill-measure", "pill-comment"]) await expect(page.getByTestId(id)).toBeVisible();
      }
      // the exploded model is drawn in the free area (not behind a surface)
      await expect.poll(() => drawnIn(page, left.x + left.width + 12, dock.x - 12), { timeout: 10_000 }).toBeGreaterThan(3000);
      // and all of it: the explode backs the zoom out about the free area's middle (viewer/scene
      // offsetOrtho), so no part drifts under the dock
      const span = await drawnSpan(page);
      expect(span.min).toBeGreaterThanOrEqual(left.x + left.width + 12 - 2);
      expect(span.max).toBeLessThanOrEqual(dock.x - 12 + 2);
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/room-${width}-${n}col.png` });
    });
  }
}

test("a wide dock gives way so the model keeps 520 px and the Parts surface stays open", async ({ page, fake }) => {
  // P11 F2: one width for the dock; a remembered wide one is capped by the room (ui/shell/room)
  await fake.reset(robot());
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.addInitScript(() => { try { localStorage.setItem("mises.dock.stack", JSON.stringify({ width: 1400 })); } catch { /* none */ } });
  await page.goto("/");
  const app = page.locator(".app");
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-instances", "13");
  await expect(app).toHaveAttribute("data-left", "open");
  const dock = await box(page, "[data-region=console]");
  const left = await box(page, "[data-region=left]");
  expect(dock.x - 12 - (left.x + left.width + 12)).toBeGreaterThanOrEqual(519);
  // closing streams never changes the dock's width
  await page.getByRole("button", { name: "Close View and Simulations" }).click();
  await expect(page.locator("[data-dock]")).toHaveAttribute("data-cols", "1");
  expect(Math.round((await box(page, "[data-region=console]")).width)).toBe(Math.round(dock.width));
});
