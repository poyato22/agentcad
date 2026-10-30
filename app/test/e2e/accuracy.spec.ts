// Plan 6 (S4) on the Studies page against the fake core's assumed S1-S3 shapes
// (test/fake-core/accuracy.ts): nonlinear stress with a contact pair picked in the viewer, the
// print direction, fatigue inputs and results, the OpenFOAM solver with its user-click install,
// and material calibration from the user's own tests with the scatter plot and the badge.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const FX = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui");
const read = (n: string): unknown => JSON.parse(readFileSync(resolve(FX, n), "utf8"));
const BASE = JSON.parse(readFileSync(resolve(import.meta.dirname, "../fake-core/scenarios/base.json"), "utf8")) as Record<string, unknown>;
const GLB = { beam: resolve(FX, "study_part_beam.glb"), cube: resolve(FX, "study_part_cube.glb") };
const scenario = (sims: Record<string, unknown> = {}) => ({
  ...BASE, parts: read("study_parts.json"), glb: GLB, runs: [], steps: [], approvals: [], sims: { studies: true, step_ms: 40, ...sims },
});

async function open(page: Page, theme: "dark" | "light" = "dark") {
  await page.goto("/");
  await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await page.getByRole("tab", { name: "Studies" }).click();
  await expect(page.getByTestId("studies")).toBeVisible();
}

test("nonlinear_contact_pair_picked_in_the_viewer_print_direction_and_fatigue_run_and_report", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await open(page);
  await page.locator("#st-part").selectOption("beam");
  await page.getByTestId("type-static").click();
  await expect(page.getByTestId("study-view")).toHaveAttribute("data-mode", "setup");
  const canvas = page.getByTestId("study-canvas");
  await expect(canvas).not.toHaveAttribute("data-markers", "0");
  const before = Number(await canvas.getAttribute("data-markers"));

  // nonlinear: the generic switch and fields, plasticity on
  await page.locator('[data-field="nonlinear"] > legend input[type=checkbox]').check();
  await page.locator("#sf-nonlinear-plasticity").check();
  // a contact pair, both faces picked in the viewer one after the other
  await page.getByTestId("add-contact-pair").click();
  await page.getByTestId("pick-pair").click();
  await expect(page.getByTestId("pick-hint")).toHaveText("Click face A of the contact pair · Esc to stop");
  const box = (await canvas.boundingBox())!;
  await canvas.click({ position: { x: box.width / 2, y: box.height / 2 } });
  await expect(page.getByTestId("pick-hint")).toHaveText("Now click face B · Esc to stop");
  await expect(page.getByTestId("contact-pair")).toHaveAttribute("data-armed", "b");
  await canvas.click({ position: { x: box.width / 2 + 4, y: box.height / 2 + 4 } });
  await expect(page.getByTestId("study-view")).not.toHaveAttribute("data-picking", "true");
  await expect(page.getByLabel("Contact 1 face A", { exact: true })).toHaveValue(/^f\d+$/);
  await expect(page.getByLabel("Contact 1 face B", { exact: true })).toHaveValue(/^f\d+$/);

  // the print direction: layers along +x, drawn across the part
  await page.getByTestId("print-direction-on").check();
  await page.getByRole("radio", { name: "layers stack along +x" }).click();
  await expect(page.getByTestId("print-direction-text")).toHaveText("layers stack along +x (printed on its side)");
  // the two contact faces + the pair's link + the layer stack are drawn
  await expect.poll(async () => Number(await canvas.getAttribute("data-markers"))).toBeGreaterThan(before + 2);
  await page.getByTestId("print-direction").scrollIntoViewIfNeeded();
  await page.screenshot({ path: "test-results/p6-print-dark.png" });
  await page.getByTestId("study-view").screenshot({ path: "test-results/p6-viewer-markers-dark.png" });

  // fatigue: zero to max, 10 million cycles
  await page.getByTestId("fatigue-on").check();
  await page.getByRole("radio", { name: "Zero to max" }).click();
  await page.getByRole("button", { name: /10⁷/ }).click();
  await expect(page.getByTestId("fatigue-cycles-text")).toHaveText("10 million cycles");
  await page.screenshot({ path: "test-results/p6-setup-dark.png", fullPage: true });

  await page.getByTestId("run-study").click();
  const r = page.getByTestId("study-result");
  await expect(r.getByTestId("nonlinear-result")).toContainText("converged at the full load");
  await expect(r.getByTestId("fidelity-note")).toHaveText("nonlinear FEM (large deflection, plasticity, contact) · ±8% (validated)");
  await expect(r.getByTestId("fatigue-label")).toHaveText("fatigue estimate");
  await expect(r.getByTestId("sn-chart")).toBeVisible();
  await expect(r.getByTestId("solver-label")).toContainText("orthotropic FDM");
  const start = (await fake.calls()).find((c) => c.method === "POST" && c.path === "/sims")!;
  expect(start.body).toMatchObject({
    type: "static", part_id: "beam", settings: {
      nonlinear: { large_deflection: true, plasticity: true, steps: 10, contact: [{ kind: "faces", master: expect.stringMatching(/^f\d+$/), slave: expect.stringMatching(/^f\d+$/), friction: 0.2 }] },
      print_direction: "+x", fatigue: { cycles: 1e7, cycle: "zero_to_max", min_ratio: 0, mean_correction: "goodman" },
    },
  });
  // the new channels reach the probe
  await page.getByTestId("probe-toggle").click();
  await canvas.click({ position: { x: box.width / 2, y: box.height / 2 } });
  await expect(page.getByTestId("probe")).toContainText("plastic strain (PEEQ)");
  await expect(page.getByTestId("probe")).toContainText("contact pressure");
  await r.getByTestId("fatigue-result").scrollIntoViewIfNeeded();
  await page.screenshot({ path: "test-results/p6-result-dark.png", fullPage: true });
});

test("openfoam_is_a_wind_solver_installed_by_a_click_and_labelled_on_the_result", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await open(page);
  await page.locator("#st-part").selectOption("cube");
  await page.getByTestId("type-wind").click();
  await expect(page.getByTestId("openfoam-card")).toHaveAttribute("data-status", "missing");
  await page.locator("#sf-solver").selectOption("openfoam");
  const card = page.getByTestId("openfoam-card");
  await expect(card).toContainText("runs on the built-in solver");
  // nothing is pulled until the click
  expect((await fake.calls()).some((c) => c.path.startsWith("/system/install"))).toBe(false);
  await card.getByTestId("openfoam-install").click();
  await expect(card).toHaveAttribute("data-status", "ok");
  await expect(card).toContainText("OpenFOAM is ready");
  expect((await fake.calls()).filter((c) => c.method === "POST" && c.path === "/system/install/openfoam")).toHaveLength(1);
  await page.getByTestId("run-study").click();
  const r = page.getByTestId("study-result");
  await expect(r.getByTestId("fidelity-note")).toHaveText("OpenFOAM k-ω SST · ±10% (validated)");
  await expect(r.getByTestId("solver-label")).toContainText("OpenFOAM v2406");
});

for (const theme of ["dark", "light"] as const) {
  test(`calibrate_pla_from_coupons_fit_scatter_apply_badge_${theme}`, async ({ page, fake }) => {
    await fake.reset(scenario() as never);
    await open(page, theme);
    await page.locator("#st-part").selectOption("beam");
    await page.getByTestId("type-static").click();
    await page.locator("#sf-material-name").selectOption("PLA");
    await expect(page.locator("#sf-material-name option:checked")).toHaveText("PLA · datasheet / estimate");
    await page.getByTestId("open-calibration").click();
    const cal = page.getByTestId("calibration");
    await expect(cal.getByTestId("cal-material")).toHaveValue("PLA");
    await cal.getByTestId("cal-printer").fill("MK4");
    const tests: [string, Record<string, string>][] = [
      ["tensile", { "break force": "1300" }], ["tensile", { "break force": "1180" }], ["tensile", { "break force": "1240" }],
      ["bend", { load: "40", deflection: "2" }], ["bend", { load: "38", deflection: "2" }],
    ];
    for (const [i, [kind, vals]] of tests.entries()) {
      await cal.getByTestId(`cal-add-${kind}`).click();
      for (const [label, v] of Object.entries(vals)) await cal.getByLabel(new RegExp(`^Test ${i + 1} ${label} \\(`)).fill(v);
    }
    await expect(cal.getByTestId("cal-implied").first()).toContainText("32.5 MPa strength");
    await cal.getByTestId("cal-fit").click();
    const res = cal.getByTestId("cal-result");
    await expect(res.getByTestId("cal-strength")).toHaveText("31 MPa");
    await expect(res.getByTestId("cal-confidence")).toHaveText("high confidence");
    const charts = res.getByTestId("cal-scatter");
    await expect(charts).toHaveCount(2);
    await expect(charts.first().locator("circle.acc-cal-dot")).toHaveCount(2);
    await expect(charts.nth(1).locator("circle.acc-cal-dot")).toHaveCount(3);
    await expect(charts.first().getByTestId("cal-datasheet")).toBeVisible();
    // hover a dot: its value and its distance from the fit
    const svg = charts.nth(1).locator("svg");
    const sb = (await svg.boundingBox())!;
    await svg.hover({ position: { x: 44 + (sb.width - 44 - 64) / 6, y: sb.height / 2 } });
    await expect(charts.nth(1).locator(".sc-tip")).toContainText("test 1 · tensile bar");
    await cal.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `test-results/p6-calibration-${theme}.png`, fullPage: true });
    await res.getByTestId("cal-apply").click();
    await expect(cal.getByTestId("cal-note")).toHaveText("PLA is now calibrated (5 tests): new studies use the fitted values.");
    await expect(cal.getByTestId("cal-badge")).toHaveText("calibrated (5 tests)");
    await expect(page.locator("#sf-material-name option:checked")).toHaveText("PLA · calibrated (5 tests)");
    const body = (await fake.calls()).find((c) => c.method === "POST" && c.path === "/sims/calibrations")!.body as { tests: unknown[]; printer_profile: string };
    expect(body.printer_profile).toBe("MK4");
    expect(body.tests).toHaveLength(5);
  });
}
