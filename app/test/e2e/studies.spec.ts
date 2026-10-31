// Studies page, inline study cards and the immersive view (Task A8) against the fake core, whose
// /sims routes serve the real core's study fixtures (core/scripts/dump_study_fixtures.py: every
// study type on a beam and a cube, an agent's sim_run calls and its design_review).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";
import { expect, openManualStudySetup, test } from "./fixtures";

const FX = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui");
const read = (n: string): unknown => JSON.parse(readFileSync(resolve(FX, n), "utf8"));
const BASE = JSON.parse(readFileSync(resolve(import.meta.dirname, "../fake-core/scenarios/base.json"), "utf8")) as Record<string, unknown>;
const PARTS = read("study_parts.json");
const GLB = { beam: resolve(FX, "study_part_beam.glb"), cube: resolve(FX, "study_part_cube.glb") };
// the real run belongs to the dump's build; here it is the fake core's active build (A3's tabs
// show each build's own runs)
const DEFAULT_BUILD = (read("projects.json") as { id: string }[])[0].id;   // the fake core's Default build
const RUN = { ...(read("study_agent_run.json") as Record<string, unknown>), project_id: DEFAULT_BUILD };
const STEPS = read("study_agent_steps.json");
const rec = (t: string) => read(`study_${t}.json`) as { sim_id: string; part_id: string };

const scenario = (sims: Record<string, unknown> = {}, extra: Record<string, unknown> = {}) => ({
  ...BASE, parts: PARTS, glb: GLB, runs: [], steps: [], approvals: [], sims: { studies: true, step_ms: 60, ...sims }, ...extra,
});

async function open(page: Page, theme: "dark" | "light" = "dark") {
  await openManualStudySetup(page);
  await page.goto("/");
  await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await page.getByRole("tab", { name: "Studies" }).click();
  await expect(page.getByTestId("studies")).toBeVisible();
}

async function selectPart(page: Page, id: string) {
  await page.locator("#st-part").selectOption(id);
}

/** Saturated (colormap) pixels of the study canvas (preserveDrawingBuffer). */
async function saturated(page: Page, sel = "[data-testid=study-canvas]"): Promise<number> {
  return page.evaluate((s) => {
    const src = document.querySelector<HTMLCanvasElement>(s);
    if (!src) return -1;
    const c = document.createElement("canvas");
    c.width = src.width; c.height = src.height;
    const ctx = c.getContext("2d")!;
    ctx.drawImage(src, 0, 0);
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    let n = 0;
    for (let i = 0; i < d.length; i += 4) if ((d[i + 3] ?? 0) > 200 && Math.max(d[i]!, d[i + 1]!, d[i + 2]!) - Math.min(d[i]!, d[i + 1]!, d[i + 2]!) > 90) n++;
    return n;
  }, sel);
}

for (const theme of ["dark", "light"] as const) {
  test(`wind_results_render_from_the_real_fixture_${theme}`, async ({ page, fake }) => {
    await fake.reset(scenario() as never);
    await open(page, theme);
    await selectPart(page, "cube");
    const r = page.getByTestId("study-result");
    await expect(r).toHaveAttribute("data-type", "wind");
    await expect(r.getByTestId("fidelity-note")).toHaveText("low fidelity (preview) · ±40% (validated)");
    await expect(r.getByTestId("guide-label")).toContainText("GUIDE ONLY");
    await expect(r.getByTestId("result-note")).toContainText("skin-friction estimate");
    await expect(r.locator("[data-testid=metric][data-key=cd]")).toContainText("1.15");
    await expect(r.locator("[data-testid=metric][data-key=cl]")).toBeVisible();
    await expect(r.getByTestId("convergence-band")).toContainText("not the accuracy");
    // the yaw sweep: Cd and Cl over the three angles, with a legend and a table view
    const yaw = r.locator("[data-chart=cd_vs_yaw]");
    await expect(yaw.getByTestId("chart-legend")).toContainText("Cd");
    await yaw.getByRole("button", { name: "Table" }).click();
    await expect(yaw.getByTestId("chart-table")).toContainText("-15");
    // the viewport: Cp on the surface, particles moving along the velocity slice
    const canvas = page.getByTestId("study-canvas");
    await expect(page.getByTestId("study-view")).toHaveAttribute("data-channel", "cp");
    await expect(canvas).toHaveAttribute("data-particles", "900");
    await expect(canvas).toHaveAttribute("data-animating", "true");
    await expect.poll(() => saturated(page)).toBeGreaterThan(50);
    await expect(page.getByTestId("legend")).toContainText("Cp");
    await page.screenshot({ path: `test-results/a8-wind-${theme}.png` });
  });
}

test("the_settings_form_comes_from_the_schema_and_bad_ranges_are_said_before_running", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await open(page);
  for (const t of ["static", "modal", "buckling", "thermal", "drop", "wind"]) {
    await page.getByTestId(`type-${t}`).click();
    await expect(page.getByTestId(`type-${t}`)).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("schema-form")).toBeVisible();
  }
  await page.getByTestId("type-modal").click();
  await page.locator("#sf-n_modes").fill("500");
  await page.getByTestId("run-study").click();
  await expect(page.locator('[data-field="n_modes"] .sf-err')).toHaveText("at most 30");
  const calls = await fake.calls();
  expect(calls.some((c) => c.method === "POST" && c.path === "/sims")).toBe(false);
});

test("pick_a_face_in_the_viewer_assigns_it_to_the_load_and_draws_the_arrow", async ({ page, fake }) => {
  await fake.reset(scenario({}, { sims: { studies: false } }) as never);
  await open(page);
  await selectPart(page, "beam");
  await expect(page.getByTestId("study-view")).toHaveAttribute("data-mode", "setup");
  const canvas = page.getByTestId("study-canvas");
  await expect(canvas).not.toHaveAttribute("data-markers", "0");
  const face = page.locator("#sf-load_cases-0-loads-0-face");
  await expect(face).toHaveValue("top");
  await page.locator('[data-field="load_cases.0.loads.0.face"]').getByTestId("pick-face").click();
  await expect(page.getByTestId("study-view")).toHaveAttribute("data-picking", "true");
  const box = (await canvas.boundingBox())!;
  await canvas.click({ position: { x: box.width / 2, y: box.height / 2 } });
  await expect(face).toHaveValue(/^f\d+$/);
  await expect(page.getByTestId("study-view")).not.toHaveAttribute("data-picking", "true");
  // Escape disarms a pick without changing anything
  await page.locator('[data-field="load_cases.0.supports.0.face"]').getByTestId("pick-face").click();
  await page.keyboard.press("Escape");
  await expect(page.locator("#sf-load_cases-0-supports-0-face")).toHaveValue("bottom");
});

test("static_result_channel_switch_deform_section_and_probe", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await open(page);
  await selectPart(page, "beam");
  await page.locator("[data-testid=study-row][data-type=static]").first().click();
  const view = page.getByTestId("study-view");
  await expect(view).toHaveAttribute("data-mode", "result");
  await expect(view).toHaveAttribute("data-channel", "von_mises");
  await page.getByTestId("channel").selectOption("s1");
  await expect(view).toHaveAttribute("data-channel", "s1");
  await expect(page.getByTestId("legend")).toContainText("max principal");
  await page.getByTestId("range-hi").fill("2");
  await page.getByTestId("range-hi").press("Enter");
  await expect(page.getByTestId("legend")).toContainText("2");
  await page.getByTestId("deform").fill("8");
  await page.getByTestId("section-axis").selectOption("y");
  await expect(page.getByTestId("study-canvas")).toHaveAttribute("data-section", "true");
  await page.getByTestId("probe-toggle").click();
  const box = (await page.getByTestId("study-canvas").boundingBox())!;
  await page.getByTestId("study-canvas").click({ position: { x: box.width / 2, y: box.height / 2 } });
  await expect(page.getByTestId("probe")).toContainText("MPa");
});

test("modal_result_plays_mode_shapes_and_static_frames_with_reduced_motion", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await open(page);
  await selectPart(page, "beam");
  await page.locator("[data-testid=study-row][data-type=modal]").first().click();
  await page.getByTestId("animate-modes").click();
  await expect(page.getByTestId("study-canvas")).toHaveAttribute("data-animating", "true");
  await page.getByTestId("animate-modes").click();
  await expect(page.getByTestId("study-canvas")).toHaveAttribute("data-animating", "false");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await selectPart(page, "cube");
  await expect(page.getByTestId("study-canvas")).not.toHaveAttribute("data-streamlines", "0");
  await expect(page.getByTestId("study-canvas")).toHaveAttribute("data-particles", "0");
  await expect(page.getByTestId("study-canvas")).toHaveAttribute("data-animating", "false");
});

test("run_progress_by_phase_and_cancel", async ({ page, fake }) => {
  await fake.reset(scenario({ outcome: "hang" }) as never);
  await open(page);
  await selectPart(page, "cube");
  await page.getByTestId("type-wind").click();
  await page.getByTestId("run-study").click();
  const prog = page.getByTestId("study-progress");
  await expect(prog).toHaveAttribute("data-phase", "solve");
  await expect(prog.getByTestId("phase-bar")).toHaveAttribute("aria-valuetext", /solving · lattice step/);
  await page.getByTestId("cancel-study").click();
  await expect(prog).toBeHidden();
  const calls = await fake.calls();
  const start = calls.find((c) => c.method === "POST" && c.path === "/sims")!;
  expect(start.body).toMatchObject({ type: "wind", part_id: "cube", settings: { speed_m_s: 10, refinement: 2 } });
  expect(calls.some((c) => c.method === "POST" && /^\/sims\/sim_[0-9a-f]{12}\/cancel$/.test(c.path))).toBe(true);
  await expect(page.getByTestId("study-result")).toHaveAttribute("data-status", "cancelled");
});

test("compare_two_studies_side_by_side", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await open(page);
  await selectPart(page, "beam");
  const checks = page.getByTestId("compare-check");
  await checks.nth(0).check();
  await checks.nth(1).check();
  await page.getByTestId("compare").click();
  const v = page.getByTestId("compare-view");
  await expect(v.getByTestId("compare-row").first()).toBeVisible();
  await expect(v).toContainText("GUIDE ONLY");
});

test("templates_save_and_load", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await open(page);
  await page.getByTestId("template-pick").selectOption({ label: "Breeze · Wind" });
  await page.getByTestId("template-load").click();
  await expect(page.getByTestId("type-wind")).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#sf-speed_m_s")).toHaveValue("5");
  await page.getByTestId("template-name").fill("Gusty");
  await page.getByTestId("template-save").click();
  await expect(page.getByTestId("template-pick")).toContainText("Gusty · Wind");
});

/** The immersive view listens for its keys (data-ready) and shows this study, loaded. */
async function immersiveShows(page: Page, simId: string | null = null) {
  const imm = page.getByTestId("immersive");
  await expect(imm).toHaveAttribute("data-ready", "true");
  if (simId) await expect(imm).toHaveAttribute("data-sim", simId);
  await expect(imm).toHaveAttribute("data-status", "done");
  await expect(imm.getByTestId("imm-metrics")).toBeVisible();
  await expect(imm.getByTestId("imm-error")).toHaveCount(0);
  return imm;
}

for (const theme of ["dark", "light"] as const) {
  test(`the_agent_s_studies_show_as_cards_and_open_the_immersive_view_${theme}`, async ({ page, fake }) => {
    await fake.reset(scenario({}, { runs: [RUN], steps: STEPS }) as never);
    await page.goto("/");
    await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
    await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
    const cards = page.getByTestId("study-card");
    await expect(cards).toHaveCount(8);
    const wind = page.locator("[data-testid=study-card][data-type=wind]");
    await expect(wind.getByTestId("fidelity-note")).toHaveText("low fidelity (preview) · ±40% (validated)");
    const drop = page.locator("[data-testid=study-card][data-type=drop]");
    await expect(drop.getByTestId("error-band")).toHaveText("error band ±19.1%");
    // Plan 9 D4: the studies are the dock's Simulations stream (one compact card each, not the
    // conversation); the chart opens on the card's expand
    await expect(page.locator("[data-dock] [data-stream=sim]").getByTestId("study-card")).toHaveCount(8);
    await expect(page.getByRole("log", { name: "Conversation" }).getByTestId("study-card")).toHaveCount(0);
    await expect(wind.getByTestId("card-chart")).toHaveCount(0);
    await expect(wind.getByTestId("check")).toHaveCount(0);
    await wind.scrollIntoViewIfNeeded();
    await wind.getByTestId("card-expand").click();
    await expect(wind).toHaveAttribute("data-expanded", "");
    await wind.getByTestId("card-chart").scrollIntoViewIfNeeded();   // the Simulations stream scrolls on its own
    await expect(wind.getByTestId("card-chart")).toHaveAttribute("data-chart", "cd_vs_yaw");
    await expect(wind.getByTestId("study-thumb")).toHaveAttribute("data-state", "loaded");
    await page.locator("[data-region=console]").screenshot({ path: `test-results/a8-cards-${theme}.png` });
    await wind.getByTestId("open-immersive").click();
    const imm = await immersiveShows(page, rec("wind").sim_id);
    // the traffic lights keep their corner: the top strip starts after the inset
    const pad = await imm.locator(".imm-top").evaluate((el) => getComputedStyle(el).paddingLeft);
    expect(pad).toBe("78px");
    await expect(imm.getByTestId("fidelity-note")).toHaveText("low fidelity (preview) · ±40% (validated)");
    await expect(imm.getByTestId("study-canvas")).toHaveAttribute("data-particles", "900");
    await page.screenshot({ path: `test-results/a8-immersive-wind-${theme}.png` });
    await page.keyboard.press("Escape");
    await expect(imm).toBeHidden();
    // the review card: each row opens its own study, which loads; ⌘[ / ⌘] step through the review
    const review = page.getByTestId("review-card");
    await review.scrollIntoViewIfNeeded();
    const rows = review.locator("[data-testid=review-row][data-sim]:not([data-sim=''])");
    const linked = await rows.evaluateAll((els) => [...new Set(els.map((e) => (e as HTMLElement).dataset.sim as string))]);
    expect(linked).toHaveLength(2);
    for (const sim of linked) {
      await review.locator(`[data-testid=review-row][data-sim="${sim}"]`).first().getByRole("button", { name: /^Immersive view of the study for / }).click();
      await immersiveShows(page, sim);
      await page.keyboard.press("Escape");
      await expect(page.getByTestId("immersive")).toBeHidden();
    }
    await review.getByTestId("open-immersive").click();
    await immersiveShows(page, linked[0]);
    await page.keyboard.press("Meta+]");
    await immersiveShows(page, linked[1]);
    await page.keyboard.press("Meta+[");
    await immersiveShows(page, linked[0]);
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("immersive")).toBeHidden();
    // a double-click on a card's chart opens it too
    const stat = page.locator("[data-testid=study-card][data-type=static]").first();
    await stat.scrollIntoViewIfNeeded();
    await stat.getByTestId("card-expand").click();
    await stat.getByTestId("card-chart").scrollIntoViewIfNeeded();
    await stat.getByTestId("study-chart").dblclick();
    await immersiveShows(page);
  });
}

test("opening_the_immersive_view_30_times_never_exhausts_webgl_contexts", async ({ page, fake }) => {
  await fake.reset(scenario({}, { runs: [RUN], steps: STEPS }) as never);
  const warnings: string[] = [];
  page.on("console", (m) => { if (/WebGL context|Too many active WebGL/i.test(m.text())) warnings.push(m.text()); });
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  const wind = page.locator("[data-testid=study-card][data-type=wind]");
  await wind.scrollIntoViewIfNeeded();
  await expect(wind.getByTestId("study-thumb")).toHaveAttribute("data-state", "loaded");
  for (let i = 0; i < 30; i++) {
    await wind.getByTestId("open-immersive").click();
    await expect(page.getByTestId("immersive")).toHaveAttribute("data-ready", "true");
    await expect(page.getByTestId("study-canvas").last()).toHaveAttribute("data-particles", "900");
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("immersive")).toBeHidden();
  }
  expect(warnings.filter((w) => /Too many active WebGL contexts/i.test(w))).toEqual([]);
  // the thumbnail renderer still works afterwards (its context was never evicted)
  const stat = page.locator("[data-testid=study-card][data-type=static]").first();
  await stat.scrollIntoViewIfNeeded();
  await expect(stat.getByTestId("study-thumb")).toHaveAttribute("data-state", "loaded");
});

test("the_immersive_view_follows_a_running_study_from_the_page", async ({ page, fake }) => {
  await fake.reset(scenario({ step_ms: 250 }) as never);
  await open(page);
  await selectPart(page, "cube");
  await page.getByTestId("type-wind").click();
  await page.getByTestId("run-study").click();
  await page.getByTestId("watch-immersive").click();
  const imm = page.getByTestId("immersive");
  await expect(imm).toHaveAttribute("data-ready", "true");
  // the honesty labels while it runs
  await expect(imm.getByTestId("fidelity-note")).toHaveText("low fidelity");
  await expect(imm.getByTestId("error-band")).toHaveText("±30% (validated)");
  await expect(imm.getByTestId("imm-live")).toBeVisible();
  await expect(imm.getByTestId("live-caption")).toContainText("Wind ·");
  await expect(imm).toHaveAttribute("data-status", "done", { timeout: 15_000 });
  await expect(imm.getByTestId("imm-metrics")).toContainText("Cd");
  await page.keyboard.press("Escape");
  await expect(imm).toBeHidden();
});
