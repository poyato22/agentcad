// FW-layout (Plan 9 D20, "the model is the hero") against the fake core with the real-core Pi
// enclosure fixture: the build view is full-bleed under the top bar; the Parts surface and the
// agent dock float over it and fold to slim tabs (⌘[ / ⌘]); the model is framed in the middle of
// what they leave. Studies hides the dock and fits 1280×800; Print and BOM start with it folded;
// the top bar has no Stop all; Home fills the window with 3–5 columns of 280–420px tiles.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";
import { expect, openHome, test } from "./fixtures";

test.use({ launchOptions: { args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] } });

const FX = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui");
const read = (n: string): unknown => JSON.parse(readFileSync(resolve(FX, n), "utf8"));
const BASE = JSON.parse(readFileSync(resolve(import.meta.dirname, "../fake-core/scenarios/base.json"), "utf8")) as { runs: Record<string, unknown>[]; agents: unknown[] } & Record<string, unknown>;
const PROJECT = (JSON.parse(readFileSync("test/fixtures/core-ui/projects.json", "utf8")) as { id: string }[])[0].id;

/** The Pi enclosure (geometry scenario) as the build's assembly, with the base lead running. */
function enclosure(): Record<string, unknown> {
  const sc = JSON.parse(readFileSync("test/fake-core/scenarios/geometry.json", "utf8")) as Record<string, unknown>;
  const ids = ["pi_case", "pi_lid", "pi_board", "pi_battery", "pi_fan"];
  const I = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];
  const stored = { instances: ids.map((id, i) => ({ id: `i_${id}`, part_id: id, name: id.replace("pi_", ""), fixed: i === 0, transform: I })), joints: [] };
  return {
    ...sc, script: [],
    assembly: { by_project: { [PROJECT]: stored, b0default000: stored }, interference: "none" },
    runs: [{ ...BASE.runs[0], status: "running" }], steps: BASE.steps, approvals: [],
  };
}

const box = async (page: Page, sel: string) => (await page.locator(sel).first().boundingBox())!;

/** The x centre of the model's drawn pixels (opaque, on the transparent canvas), in client px. */
async function modelCentreX(page: Page): Promise<number> {
  return page.evaluate(() => {
    const src = document.querySelector<HTMLCanvasElement>("[data-testid=assembly-canvas]") as HTMLCanvasElement;
    const c = document.createElement("canvas");
    c.width = src.width; c.height = src.height;
    const ctx = c.getContext("2d") as CanvasRenderingContext2D;
    ctx.drawImage(src, 0, 0);
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    let lo = Infinity; let hi = -Infinity;
    for (let y = 0; y < c.height; y += 2) for (let x = 0; x < c.width; x += 2) {
      if ((d[(y * c.width + x) * 4 + 3] ?? 0) > 200) { lo = Math.min(lo, x); hi = Math.max(hi, x); }
    }
    const r = src.getBoundingClientRect();
    return r.left + ((lo + hi) / 2) * (r.width / c.width);
  });
}

for (const size of [{ width: 1440, height: 900 }, { width: 1280, height: 800 }]) {
  test(`the_build_view_is_full_bleed_with_floating_surfaces_at_${size.width}`, async ({ page, fake }) => {
    await fake.reset(enclosure());
    await page.setViewportSize(size);
    await page.goto("/");
    await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-instances", "5");
    await expect.poll(() => modelCentreX(page), { timeout: 15_000 }).toBeGreaterThan(0);
    // the viewer spans the window under the 44px top bar
    const vp = await box(page, "[data-region=viewer]");
    expect([vp.x, vp.y, Math.round(vp.width)]).toEqual([0, 44, size.width]);
    expect(Math.round(vp.height)).toBe(size.height - 44);
    // the two surfaces float over it, 12px in, rounded, with the soft shadow
    const left = await box(page, "[data-region=left]");
    const dock = await box(page, "[data-region=console]");
    expect(left.x).toBe(12);
    expect(left.y).toBe(56);
    expect(Math.round(dock.x + dock.width)).toBe(size.width - 12);
    expect(Math.round(dock.y + dock.height)).toBe(size.height - 12);
    for (const s of ["[data-region=left]", "[data-region=console]"]) {
      const st = await page.locator(s).evaluate((el) => ({ r: getComputedStyle(el).borderRadius, sh: getComputedStyle(el).boxShadow }));
      expect(st.r).toBe("10px");
      expect(st.sh).not.toBe("none");
    }
    // the Parts surface is as tall as its content, not a full-height column
    expect(left.height).toBeLessThan(vp.height - 100);
    // the model sits in the middle of what the surfaces leave, and so does the viewer pill
    const mid = (left.x + left.width + dock.x) / 2;
    await expect.poll(() => modelCentreX(page)).toBeGreaterThan(mid - 30);
    expect(await modelCentreX(page)).toBeLessThan(mid + 30);
    const pill = await box(page, ".asm-pill");
    expect(Math.abs(pill.x + pill.width / 2 - mid)).toBeLessThan(2);
    // ⌘[ / ⌘] fold both to slim tabs: the model re-centres in the whole window
    await page.keyboard.press("Meta+BracketLeft");
    await page.keyboard.press("Meta+BracketRight");
    await expect(page.locator("[data-region=app]")).toHaveAttribute("data-left", "rail");
    await expect(page.locator("[data-region=app]")).toHaveAttribute("data-console", "rail");
    expect((await box(page, "[data-region=left] .surface-tab")).width).toBeLessThan(40);
    expect((await box(page, "[data-region=console] .surface-tab")).width).toBeLessThan(40);
    await expect.poll(async () => Math.abs((await modelCentreX(page)) - size.width / 2)).toBeLessThan(40);
    // and back with their tabs
    await page.getByRole("button", { name: "Show the parts" }).click();
    await page.getByRole("button", { name: /^Expand the agent console/ }).click();
    await expect(page.getByTestId("left-pane")).toBeVisible();
    await expect(page.locator("[data-region=console] [data-dock]")).toBeVisible();
    // the top bar keeps quiet: no red Stop all while a run works (⇧⌘. and the composer's Stop stay)
    await expect(page.locator("[data-region=topbar]").getByRole("button", { name: /stop all/i })).toHaveCount(0);
  });
}

test("studies_hides_the_dock_and_fits_1280x800", async ({ page, fake }) => {
  const SIMULATION = { name: "simulation", description: "The Studies page's agent.", model: "inherit" };
  await fake.reset({
    ...BASE, agents: [...BASE.agents, SIMULATION], parts: read("study_parts.json"),
    glb: { beam: resolve(FX, "study_part_beam.glb"), cube: resolve(FX, "study_part_cube.glb") },
    runs: [], steps: [], approvals: [], sims: { studies: true, step_ms: 60 }, studies_agent: "agent",
  } as never);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  await page.getByRole("tab", { name: "Studies" }).click();
  const pane = page.getByTestId("studies-agent");
  await expect(pane.getByTestId("study-proposal")).toHaveCount(2);
  // the studies agent is the agent here: the dock is only its slim tab, the page takes the width
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-console", "rail");
  await expect(page.locator("[data-region=console] [data-dock]")).toHaveCount(0);
  expect((await box(page, "[data-region=page]")).width).toBe(1280);
  // every proposal and "Set up manually" whole on screen, nothing clipped
  for (const p of await pane.getByTestId("study-proposal").all()) await expect(p).toBeInViewport({ ratio: 1 });
  await expect(page.getByTestId("manual-setup-toggle")).toBeInViewport({ ratio: 1 });
  // the dock is a click away and the page makes room for it
  await page.getByRole("button", { name: /^Expand the agent console/ }).click();
  const dock = await box(page, "[data-region=console]");
  const main = await box(page, ".st-main");
  expect(main.x + main.width).toBeLessThanOrEqual(dock.x);
});

test("print_and_bom_start_with_the_dock_folded_and_remember_it_opened", async ({ page, fake }) => {
  await fake.reset("base");
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-console", "open");
  for (const tab of ["Print", "BOM"]) {
    await page.getByRole("tab", { name: tab }).click();
    await expect(page.locator("[data-region=app]")).toHaveAttribute("data-console", "rail");
  }
  await page.getByRole("button", { name: /^Expand the agent console/ }).click();
  await page.getByRole("tab", { name: "Print" }).click();
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-console", "rail");
  await page.getByRole("tab", { name: "BOM" }).click();
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-console", "open");
  await page.getByRole("tab", { name: "Design" }).click();
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-console", "open");
});

test("home_fills_the_width_with_3_to_5_columns_of_280_to_420px", async ({ page, fake }) => {
  const sc = JSON.parse(readFileSync("test/fake-core/scenarios/builds.json", "utf8")) as { builds: { builds: { id: string; name: string }[] } };
  sc.builds.builds = ["q0quadframe0", "b0benchrig00", "x0gimbal0000", "x1kettle0000", "x2encl000000", "x3arm0000000"]
    .map((id, i) => ({ id, name: `Build ${i + 1}` }));
  await fake.reset(sc as unknown as Record<string, unknown>);
  await openHome(page);
  for (const [width, cols] of [[1440, 4], [1280, 4], [1920, 5]] as const) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    const tiles = page.locator("[data-testid=home] .home-cell");
    await expect(tiles).toHaveCount(6);
    const boxes = await Promise.all((await tiles.all()).map((t) => t.boundingBox()));
    const tops = boxes.map((b) => Math.round(b!.y));
    expect(tops.filter((t) => t === tops[0]).length, `${width}`).toBe(cols);
    for (const b of boxes) {
      expect(b!.width).toBeGreaterThanOrEqual(280);
      expect(b!.width).toBeLessThanOrEqual(420);
    }
    // the row reaches across the window (24px margins)
    const last = boxes[cols - 1]!;
    expect(last.x + last.width).toBeGreaterThan(width - 30);
  }
});

test("home_with_two_builds_centres_large_covers", async ({ page, fake }) => {
  const sc = JSON.parse(readFileSync("test/fake-core/scenarios/builds.json", "utf8")) as { builds: { builds: { id: string; name: string }[] } };
  sc.builds.builds = [{ id: "q0quadframe0", name: "Robotic dog" }, { id: "b0benchrig00", name: "Bench rig" }];
  await fake.reset(sc as unknown as Record<string, unknown>);
  await openHome(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  const tiles = page.locator("[data-testid=home] .home-cell");
  await expect(tiles).toHaveCount(2);
  const a = (await tiles.nth(0).boundingBox())!;
  const b = (await tiles.nth(1).boundingBox())!;
  expect(a.width).toBeGreaterThan(400);
  expect(Math.abs((a.x + b.x + b.width) / 2 - 720)).toBeLessThan(2);
  await expect(page.locator(".home-hint")).toBeVisible();
});
