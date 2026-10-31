// Plan 9 G-D (D11 / D18, and D12's Play): an agent presents its assembly's motion and a section
// sweep as View-stream cards; a card opens the build view full window on the assembly's own poses.
// Chromium with SwiftShader WebGL against the fake core seeded with the real core's motion dump
// (core/scripts/dump_motion_fixtures.py: two mount plates hinged on a revolute joint, the lid
// opened 120° and shut, and a sweep across X) — the cards are the real core's announcements.
// Screenshots of both, dark and light, land in test-results/gd-*.png.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Locator, Page } from "@playwright/test";
import { expect, test, type FakeControl } from "./fixtures";

test.use({ launchOptions: { args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] } });

const FX = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui/motion");
const read = (n: string): unknown => JSON.parse(readFileSync(resolve(FX, n), "utf8"));
const BASE = JSON.parse(readFileSync(resolve(import.meta.dirname, "../fake-core/scenarios/base.json"), "utf8")) as { runs: Record<string, unknown>[] } & Record<string, unknown>;
const PROJECT = (JSON.parse(readFileSync(resolve(import.meta.dirname, "../fixtures/core-ui/projects.json"), "utf8")) as { id: string }[])[0].id;
const LEAD = String(BASE.runs[0].id);
const DUMPED = (read("motion_animate_tool.json") as { animation: { project_id: string } }).animation.project_id;
// the dump's build is this fake build: its cards point here (their ids, joints, thumbnails stay real)
const here = <T,>(v: T): T => JSON.parse(JSON.stringify(v).split(DUMPED).join(PROJECT)) as T;
const ANNOUNCE = (read("motion_steps.json") as { kind: string; payload: { agent_announce?: { kind: string; ref: { view_spec: { kind: string } } } } }[])
  .flatMap((s) => (s.payload.agent_announce?.kind === "view_presented" ? [here(s.payload.agent_announce)] : []));
const card = (kind: string) => ANNOUNCE.find((a) => a.ref.view_spec.kind === kind) as unknown as Record<string, unknown>;

function scenario(): Record<string, unknown> {
  const { dof_remaining: _d, overconstrained: _o, ...stored } = read("motion_assembly.json") as Record<string, unknown>;
  return {
    ...BASE, script: [],
    runs: [{ ...BASE.runs[0], role: "lead", project_id: PROJECT, status: "running" }],
    assembly: { by_project: { [PROJECT]: stored } },
  };
}

const view = (page: Page): Locator => page.locator("[data-region=console] [data-dock] [data-stream=view]");

async function open(page: Page, fake: FakeControl, kind: "animation" | "section_sweep"): Promise<void> {
  await page.goto("/");
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-instances", "2");
  await expect.poll(async () => (await pixels(page)).total, { timeout: 10_000 }).toBeGreaterThan(500);
  await fake.emit({ type: "agent_announce", run_id: LEAD, data: card(kind) });
  await expect(view(page).getByTestId("motion-card")).toHaveAttribute("data-kind", kind);
}

/** Opaque pixels on the WebGL canvas, and a coarse signature of where they are (what moved). */
async function pixels(page: Page): Promise<{ total: number; sig: string }> {
  return page.evaluate(() => {
    const src = document.querySelector<HTMLCanvasElement>("[data-testid=assembly-canvas]");
    if (!src) throw new Error("no canvas");
    const c = document.createElement("canvas");
    c.width = 64; c.height = 40;
    const ctx = c.getContext("2d");
    if (!ctx) throw new Error("no 2d");
    ctx.drawImage(src, 0, 0, 64, 40);
    const d = ctx.getImageData(0, 0, 64, 40).data;
    let total = 0; let sig = "";
    for (let i = 0; i < d.length; i += 4) { const on = (d[i + 3] ?? 0) > 120; if (on) total++; sig += on ? "1" : "0"; }
    return { total: total * ((src.width * src.height) / (64 * 40)), sig };
  });
}
/** How much of the canvas the model fills on its limiting side (its opaque pixels' box). */
async function fill(page: Page): Promise<number> {
  return page.evaluate(() => {
    const src = document.querySelector<HTMLCanvasElement>("[data-testid=assembly-canvas]");
    if (!src) throw new Error("no canvas");
    const W = 288; const H = Math.round((288 * src.height) / src.width);
    const c = document.createElement("canvas");
    c.width = W; c.height = H;
    const ctx = c.getContext("2d");
    if (!ctx) throw new Error("no 2d");
    ctx.drawImage(src, 0, 0, W, H);
    const d = ctx.getImageData(0, 0, W, H).data;
    let x0 = W; let x1 = -1; let y0 = H; let y1 = -1;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      if ((d[(y * W + x) * 4 + 3] ?? 0) < 120) continue;
      x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
    }
    return x1 < 0 ? 0 : Math.max((x1 - x0 + 1) / W, (y1 - y0 + 1) / H);
  });
}
const differs = (a: string, b: string) => [...a].filter((ch, i) => ch !== b[i]).length;

async function shots(page: Page, name: string): Promise<void> {
  for (const t of ["dark", "light"] as const) {
    await page.evaluate((th) => document.documentElement.setAttribute("data-theme", th), t);
    await page.waitForTimeout(250);
    await page.screenshot({ path: `test-results/gd-${name}-${t}.png` });
  }
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
}

test.beforeEach(async ({ fake, page }) => {
  await fake.reset(scenario() as never);
  await page.setViewportSize({ width: 1440, height: 900 });
});

test("an_animation_card_opens_the_assembly_full_window_and_plays_with_scrub_loop_and_speed", async ({ page, fake }) => {
  await open(page, fake, "animation");
  const c = view(page).getByTestId("motion-card");
  await expect(c).toContainText("The lid swings 120° on its hinge and closes again");
  await expect(c.getByTestId("motion-card-meta")).toHaveText("Open and shut the lid · 2 moves · 3.5 s · plays 2×");
  await expect(c.getByTestId("motion-card-still")).toBeVisible();
  const card = (await c.boundingBox())!;
  expect(card.width).toBeGreaterThanOrEqual(400);                        // D13: the dock's full width
  await shots(page, "animation-card");
  const rest = await pixels(page);

  await c.getByTestId("motion-card-open").click();
  const imm = page.getByTestId("assembly-immersive");
  await expect(imm).toHaveAttribute("data-mode", "animation");
  await expect(imm).toContainText("Open and shut the lid");
  const box = await page.getByTestId("assembly-view").boundingBox();
  expect([box?.width, box?.height]).toEqual([1440, 900]);              // full window
  // the model is the hero: framed to what the motion sweeps, ~70% of the limiting side
  await expect.poll(() => fill(page), { timeout: 5000 }).toBeGreaterThan(0.55);
  await page.setViewportSize({ width: 1100, height: 760 });
  await expect.poll(() => fill(page), { timeout: 5000 }).toBeGreaterThan(0.55);
  expect(await fill(page)).toBeLessThan(0.95);                          // and never cropped
  await page.setViewportSize({ width: 1440, height: 900 });
  const bar = page.getByTestId("motion-bar");
  await expect(bar).toHaveAttribute("data-playing", "true");
  await expect(page.getByTestId("motion-loop")).toHaveAttribute("aria-pressed", "true");   // it plays 2x: looping
  // it plays: the clock runs and the lid moves
  await expect.poll(async () => Number(await page.getByTestId("motion-time").textContent()), { timeout: 4000 }).toBeGreaterThan(0.6);
  // scrub: half-way through the opening (60°), paused there
  const scrub = page.getByTestId("motion-scrub");
  const seek = async (t: number) => {
    await scrub.evaluate((el, v) => { const i = el as HTMLInputElement; i.value = String(v); i.dispatchEvent(new Event("input", { bubbles: true })); }, t);
    await page.waitForTimeout(200);
  };
  await seek(1);
  await expect(bar).toHaveAttribute("data-playing", "false");
  await expect(page.getByTestId("motion-time")).toHaveText("1.0");
  // ←/→ scrub from anywhere (no focus needed), ⇧ for bigger steps
  await page.locator("body").focus();
  await page.keyboard.press("ArrowRight");                              // + 1/20 of 3.5 s
  await expect(page.getByTestId("motion-time")).toHaveText("1.2");
  await page.keyboard.press("Shift+ArrowLeft");                         // − 1/4
  await expect(page.getByTestId("motion-time")).toHaveText("0.3");
  await seek(1);
  const half = await pixels(page);
  await seek(2);                                                        // fully open (120°)
  const open120 = await pixels(page);
  await seek(0);                                                        // shut, as at rest
  const shut = await pixels(page);
  expect(differs(half.sig, shut.sig)).toBeGreaterThan(20);              // the lid stood up
  expect(differs(open120.sig, half.sig)).toBeGreaterThan(5);
  await seek(1.6);                                                      // the lid well up, for the shot
  await shots(page, "animation-immersive");
  // speed and loop
  await page.getByTestId("motion-speed-2").click();
  await expect(page.getByTestId("motion-speed-2")).toHaveAttribute("aria-pressed", "true");
  await page.getByTestId("motion-loop").click();
  await expect(page.getByTestId("motion-loop")).toHaveAttribute("aria-pressed", "false");
  await page.getByTestId("motion-play").click();
  await expect(bar).toHaveAttribute("data-playing", "true");
  // 3.5 s at 2x without loop: done in under 2 s, held at the end
  await expect(bar).toHaveAttribute("data-playing", "false", { timeout: 4000 });
  await expect(page.getByTestId("motion-time")).toHaveText("3.5");
  // Space plays again (from the start), Esc closes: the joint is back, the pill returns
  await page.locator("body").press("Space");
  await expect(bar).toHaveAttribute("data-playing", "true");
  await page.keyboard.press("Escape");
  await expect(imm).toHaveCount(0);
  await expect(page.getByTestId("viewer-pill")).toBeVisible();
  await expect.poll(async () => differs((await pixels(page)).sig, rest.sig)).toBeLessThan(15);
});

test("a_section_sweep_card_sweeps_the_cut_once_then_the_user_drags_it", async ({ page, fake }) => {
  await open(page, fake, "section_sweep");
  const c = view(page).getByTestId("motion-card");
  await expect(c.getByTestId("motion-card-meta")).toHaveText("Cuts across X from -20 to 60 mm");
  await shots(page, "sweep-card");

  await c.getByTestId("motion-card-open").click();
  const imm = page.getByTestId("assembly-immersive");
  await expect(imm).toHaveAttribute("data-mode", "sweep");
  const bar = page.getByTestId("sweep-bar");
  await expect(bar).toHaveAttribute("data-sweep", "auto");
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-section", "on");
  const mm = async () => Number((await page.getByTestId("sweep-value").textContent())?.replace(" mm", ""));
  // it sweeps by itself across X, from -20 toward 60
  await expect.poll(mm, { timeout: 3000 }).toBeGreaterThan(0);
  await page.waitForTimeout(300);
  await shots(page, "sweep-immersive");
  await expect(bar).toHaveAttribute("data-sweep", "manual", { timeout: 6000 });
  expect(await mm()).toBeCloseTo(60, 0);
  expect(await fill(page)).toBeGreaterThan(0.55);                       // the posed parts, framed
  const whole = (await pixels(page)).total;                             // cut at the far end: all of it
  // ←/→ nudge the cut from anywhere: 1/50 of the 80 mm range, 1/10 with ⇧
  await page.locator("body").focus();
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByTestId("sweep-value")).toHaveText("58.4 mm");
  await page.keyboard.press("Shift+ArrowLeft");
  await expect(page.getByTestId("sweep-value")).toHaveText("50.4 mm");
  // the slider is the user's now
  const slider = page.getByTestId("sweep-slider");
  const to = async (v: number) => {
    await slider.evaluate((el, x) => { const i = el as HTMLInputElement; i.value = String(x); i.dispatchEvent(new Event("input", { bubbles: true })); }, v);
    await page.waitForTimeout(200);
    return (await pixels(page)).total;
  };
  const little = await to(-10);
  const most = await to(50);
  expect(most).toBeGreaterThan(little * 1.3);                           // the cut moved: more is kept
  expect(little).toBeLessThan(whole);
  await expect(page.getByTestId("sweep-value")).toHaveText("50.0 mm");
  // sweep again: auto once more; Close puts the view back
  await page.getByTestId("sweep-again").click();
  await expect(bar).toHaveAttribute("data-sweep", "auto");
  await page.getByTestId("assembly-immersive-close").click();
  await expect(imm).toHaveCount(0);
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-section", "off");
});

test("play_in_the_pill_opens_the_agent's_latest_animation_with_the_same_controls", async ({ page, fake }) => {
  await page.goto("/");
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-instances", "2");
  const ev = here((readFileSync(resolve(FX, "motion_events.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l)) as { type: string; data: Record<string, unknown> }[])
    .find((e) => e.type === "assembly_animate")!);
  await fake.emit({ type: "assembly_animate", run_id: null, data: ev.data });
  await page.getByTestId("pill-play").click();
  await expect(page.getByTestId("assembly-immersive")).toContainText("The agent's animation");
  await expect(page.getByTestId("motion-bar")).toHaveAttribute("data-playing", "true");
  await expect(page.getByTestId("motion-scrub")).toHaveAttribute("max", "3.5");
  await page.getByTestId("assembly-immersive-close").click();
  await expect(page.getByTestId("viewer-pill")).toBeVisible();
});

test("after_a_reload_play_finds_the_builds_saved_animation", async ({ page, fake }) => {
  // no live assembly_animate this session: the core's saved one (GET …/assembly/animations)
  const card = read("motion_animate_tool.json") as { animation_id: string; view_spec: { animation: Record<string, unknown> } };
  const saved = { id: card.animation_id, ...card.view_spec.animation };
  await fake.reset({ ...scenario(), assembly: { ...(scenario().assembly as object), animations: { [PROJECT]: [saved] } } } as never);
  await page.goto("/");
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-instances", "2");
  await page.getByTestId("pill-play").click();
  const imm = page.getByTestId("assembly-immersive");
  await expect(imm).toHaveAttribute("data-mode", "animation");
  await expect(imm).toContainText("Open and shut the lid");
  await expect(page.getByTestId("motion-scrub")).toHaveAttribute("max", "3.5");
});

test("under_reduced_motion_nothing_moves_by_itself", async ({ page, fake }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await open(page, fake, "animation");
  await view(page).getByTestId("motion-card-open").click();
  await expect(page.getByTestId("motion-bar")).toHaveAttribute("data-playing", "false");
  await page.waitForTimeout(400);
  await expect(page.getByTestId("motion-time")).toHaveText("0.0");
  await page.getByTestId("motion-play").click();                         // the user starts it
  await expect(page.getByTestId("motion-bar")).toHaveAttribute("data-playing", "true");
  await page.keyboard.press("Escape");
  await fake.emit({ type: "agent_announce", run_id: LEAD, data: card("section_sweep") });
  await expect(view(page).getByTestId("motion-card")).toHaveAttribute("data-kind", "section_sweep");
  await view(page).getByTestId("motion-card-open").click();
  // no auto sweep: the cut lands 45% along (-20 → 60 mm) and the slider is the user's
  await expect(page.getByTestId("sweep-bar")).toHaveAttribute("data-sweep", "manual");
  await expect(page.getByTestId("sweep-value")).toHaveText("16.0 mm");
});
