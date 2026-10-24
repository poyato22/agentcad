// Viewer e2e: Chromium with SwiftShader software WebGL.
import type { Page } from "@playwright/test";
import type { PartSummary } from "../../src/api/types";
import { expect, test, type FakeControl } from "./fixtures";

test.use({ launchOptions: { args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] } });

const RUN = "b7c79a57cbb4";

test.beforeEach(async ({ fake, page }) => {
  await fake.reset("viewer");
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await expect(page.locator("[data-testid=viewer-canvas]")).toHaveAttribute("data-bbox-size", /.+/, { timeout: 15_000 });
});

interface Counts { light: number; blue: number; total: number }

/** Reads the WebGL canvas back (preserveDrawingBuffer) and counts pixels by colour. */
async function pixels(page: Page): Promise<Counts> {
  return page.evaluate(() => {
    const src = document.querySelector<HTMLCanvasElement>("[data-testid=viewer-canvas]");
    if (!src) throw new Error("no canvas");
    const c = document.createElement("canvas");
    c.width = src.width; c.height = src.height;
    const ctx = c.getContext("2d");
    if (!ctx) throw new Error("no 2d");
    ctx.drawImage(src, 0, 0);
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    let light = 0; let blue = 0; let total = 0;
    for (let i = 0; i < d.length; i += 4) {
      const r = d[i] ?? 0; const g = d[i + 1] ?? 0; const b = d[i + 2] ?? 0; const a = d[i + 3] ?? 0;
      if (a < 200) continue;
      total++;
      if (r > 0x5e && g > 0x5e && b > 0x5e && Math.abs(r - b) < 30) light++;
      if (Math.hypot(r - 0x3d, g - 0x8b, b - 0xff) < 60) blue++;
    }
    return { light, blue, total };
  });
}

async function getParts(fake: FakeControl): Promise<PartSummary[]> {
  const r = await fetch(`${fake.url}/parts`, { headers: { Authorization: `Bearer ${fake.token}` } });
  return (await r.json()) as PartSummary[];
}

test("renders_part_pixels", async ({ page }) => {
  await expect.poll(async () => (await pixels(page)).light, { timeout: 10_000 }).toBeGreaterThan(500);
  const { total, blue } = await pixels(page);
  expect(total).toBeGreaterThan(500);
  expect(blue).toBe(0);
});

test("model_bbox_matches_fixture_metrics", async ({ page, fake }) => {
  const expected = (await getParts(fake))[0]?.metrics?.bbox.size ?? [];
  expect(expected).toHaveLength(3);
  const attr = await page.locator("[data-testid=viewer-canvas]").getAttribute("data-bbox-size");
  const got = (attr ?? "").split(",").map(Number);
  expect(got).toHaveLength(3);
  got.forEach((v, i) => expect(v).toBeCloseTo(expected[i] ?? Number.NaN, 1));
  await expect(page.getByTestId("title-block")).toContainText("40.00 × 20.00 × 6.00");
  await expect(page.getByTestId("title-block")).toContainText("Part · mount plate");
});

test("drafting_grid_and_fig_label_visible", async ({ page }) => {
  await expect(page.getByText("FIG. 1 — DESIGN")).toBeVisible();
  const style = await page.locator(".vp").evaluate((el) => {
    const s = getComputedStyle(el);
    return { bg: s.backgroundColor, img: s.backgroundImage, size: s.backgroundSize };
  });
  expect(style.bg).toBe("rgb(13, 13, 13)");
  expect(style.img).toContain("rgba(255, 255, 255, 0.07)");
  expect(style.img).toMatch(/rgba\(255, 255, 255, 0\.02[45]\)/); // 8-bit alpha quantisation
  expect(style.size).toContain("100px 100px");
  expect(style.size).toContain("20px 20px");
  await expect(page.locator(".tick")).toHaveCount(4);
  const radius = await page.locator(".title-block .tb-cells").evaluate((el) => getComputedStyle(el).borderRadius);
  expect(radius).toBe("0px");
});

test("highlight_blue_while_agent_changes_part", async ({ page, fake }) => {
  const canvas = page.locator("[data-testid=viewer-canvas]");
  await expect(canvas).toHaveAttribute("data-highlight", "off");
  await fake.emit({ type: "tool_call", run_id: RUN, data: { call_id: "hl1", name: "mirror_part_set_vars", input: { part_id: "mount_plate", vars: { thickness: 7 } } } });
  await expect(canvas).toHaveAttribute("data-highlight", "on");
  await expect.poll(async () => (await pixels(page)).blue, { timeout: 10_000 }).toBeGreaterThan(50);
  await fake.emit({ type: "tool_result", run_id: RUN, data: { call_id: "hl1", name: "mirror_part_set_vars", ok: true, summary: "ok" } });
  await expect(canvas).toHaveAttribute("data-highlight", "off");
  await expect.poll(async () => (await pixels(page)).blue, { timeout: 10_000 }).toBe(0);
  await expect.poll(async () => (await pixels(page)).light, { timeout: 10_000 }).toBeGreaterThan(500);
});

test("reload_only_on_mtime_change", async ({ page, fake }) => {
  const glbCalls = async () => (await fake.calls()).filter((c) => c.path === "/parts/mount_plate/model.glb");
  expect(await glbCalls()).toHaveLength(1);
  const parts = await getParts(fake);
  // same mtime pushed again: no new request
  const result = (id: string) => ({ type: "tool_result", run_id: RUN, data: { call_id: id, name: "mirror_part_set_vars", ok: true, summary: "ok" } });
  const fetchesParts = async () => (await fake.calls()).filter((c) => c.path === "/parts").length;
  const before = await fetchesParts();
  await fake.emit(result("m1"), { parts });
  await expect.poll(fetchesParts).toBeGreaterThan(before);
  await page.waitForTimeout(300);
  expect(await glbCalls()).toHaveLength(1);
  // new mtime: the viewer asks again (conditional first, falling back to plain; see PartLoader) and the model stays
  await fake.emit(result("m2"), { parts: parts.map((p) => ({ ...p, mtime: p.mtime + 5 })) });
  await expect.poll(async () => (await glbCalls()).length).toBe(2);
  await expect(page.locator("[data-testid=viewer-canvas]")).toHaveAttribute("data-bbox-size", /.+/);
  expect((await pixels(page)).light).toBeGreaterThan(500);
});
