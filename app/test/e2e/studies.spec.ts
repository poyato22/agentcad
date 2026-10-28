// Studies page e2e against the fake core, whose /sims routes serve the real core's study
// fixtures (core/tests/fixtures/ui: a CalculiX run on mount_plate) and play its event sequence.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const BASE = JSON.parse(readFileSync(resolve(import.meta.dirname, "../fake-core/scenarios/base.json"), "utf8")) as Record<string, unknown>;
const withSims = (sims: Record<string, unknown>) => ({ ...BASE, sims });

const THEMES = {
  dark: { green: "rgb(79, 209, 139)", amber: "rgb(242, 177, 52)", red: "rgb(255, 90, 74)", blue: "rgb(61, 139, 255)", bg: "rgb(10, 10, 10)" },
  light: { green: "rgb(31, 138, 76)", amber: "rgb(183, 121, 15)", red: "rgb(200, 50, 30)", blue: "rgb(10, 91, 216)", bg: "rgb(244, 244, 242)" },
} as const;

async function open(page: Page, theme: "dark" | "light" = "dark") {
  await page.goto("/");
  await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await page.getByRole("tab", { name: "Studies" }).click();
  await expect(page.getByTestId("studies")).toBeVisible();
}

/** Pixels of a WebGL canvas (preserveDrawingBuffer): saturated (heatmap) vs grey. */
async function pixels(page: Page, testId: string): Promise<{ saturated: number; dark: number; light: number; total: number }> {
  return page.evaluate((id) => {
    const src = document.querySelector<HTMLCanvasElement>(`[data-testid=${id}]`);
    if (!src) throw new Error("no canvas");
    const c = document.createElement("canvas");
    c.width = src.width; c.height = src.height;
    const ctx = c.getContext("2d");
    if (!ctx) throw new Error("no 2d");
    ctx.drawImage(src, 0, 0);
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    let saturated = 0; let dark = 0; let light = 0; let total = 0;
    for (let i = 0; i < d.length; i += 4) {
      const r = d[i] ?? 0; const g = d[i + 1] ?? 0; const b = d[i + 2] ?? 0; const a = d[i + 3] ?? 0;
      if (a < 200) continue;
      total++;
      if (Math.max(r, g, b) - Math.min(r, g, b) > 90) saturated++;
      else if (Math.max(r, g, b) < 0x60) dark++;
      else if (Math.min(r, g, b) > 0xa0) light++;
    }
    return { saturated, dark, light, total };
  }, testId);
}

for (const theme of ["dark", "light"] as const) {
  test(`shows_the_newest_study_with_every_guide_label_${theme}`, async ({ page, fake }) => {
    await fake.reset(withSims({ records: "fixtures" }) as never);
    await open(page, theme);
    const sf = page.getByTestId("sf");
    await expect(sf).toContainText("3.17");
    await expect(sf).toHaveAttribute("data-verdict", "pass");
    await expect(sf.locator(".st-sf-value")).toHaveCSS("color", THEMES[theme].green);
    // the fixture-including safety factor and the exclusion note sit right next to the headline
    await expect(page.getByTestId("sf-incl-fixture")).toContainText("incl. fixed face 3.17");
    await expect(page.getByTestId("sf-incl-fixture")).toContainText("6.23 MPa at the fixed face");
    await expect(page.getByTestId("peak-excludes")).toHaveText("peak excludes the fixed face (nodes on the fixed face)");
    const head = await page.locator(".st-head").boundingBox();
    const side = await page.getByTestId("sf-incl-fixture").boundingBox();
    expect(head && side && side.y < head.y + head.height).toBe(true);
    await expect(page.getByTestId("max-vm")).toContainText("10.4 MPa");
    await expect(page.getByTestId("max-disp")).toContainText("0.832 mm");
    await expect(page.getByTestId("error-band")).toContainText("±5.1%");
    await expect(page.getByTestId("guide-label")).toContainText("GUIDE ONLY");
    await expect(page.getByTestId("guide-label")).toContainText("guide only, not a guarantee");
    await expect(page.getByTestId("solver-label")).toContainText("CalculiX 2.23 · C3D10 (quadratic tetrahedra) · gmsh 4.15.2 mesh 1.5 mm (coarse 3 mm, 16,752 nodes) · PETG isotropic linear elastic, strength ×0.7 FDM knock-down");
    await expect(page.getByTestId("legend")).toContainText("MPa");
    await expect(page.getByTestId("legend")).toContainText("10.4");
    // design | stress split: both sheets render; the stress one is the heatmap
    await expect(page.getByTestId("design-canvas")).toHaveAttribute("data-state", "loaded");
    await expect(page.getByTestId("stress-canvas")).toHaveAttribute("data-state", "loaded");
    await expect.poll(async () => (await pixels(page, "stress-canvas")).saturated, { timeout: 10_000 }).toBeGreaterThan(1000);
    const design = await pixels(page, "design-canvas");
    expect(design.saturated).toBe(0); // the design half stays drafting grey
    // theme-aware edges: light edges on dark, dark edges on light
    if (theme === "dark") expect(design.light).toBeGreaterThan(100);
    else expect(design.dark).toBeGreaterThan(100);
    // history: newest first, the shown one selected (blue), names without counters
    const rows = page.getByTestId("study-row");
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(1)).toHaveAttribute("aria-current", "true");
    await expect(rows.nth(0)).toContainText(/Study · \d\d:\d\d(:\d\d)? · PETG · 20 N −z on x-max · fixed x-min/);
    expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe(THEMES[theme].bg);
  });
}

test("the_failed_study_shows_its_error_and_install_hint", async ({ page, fake }) => {
  await fake.reset(withSims({ records: "fixtures" }) as never);
  await open(page);
  await page.getByTestId("study-row").first().click();
  const band = page.getByTestId("study-failed");
  await expect(band).toContainText("Study failed");
  await expect(band).toContainText("CalculiX (ccx) is not installed");
  await expect(band).toContainText("conda install -c conda-forge calculix");
  await expect(band.locator(".led")).toHaveCSS("background-color", THEMES.dark.red);
});

test("run_progress_result_and_history", async ({ page, fake }) => {
  await fake.reset(withSims({ step_ms: 400 }) as never);
  await open(page);
  await expect(page.getByText("No studies of this part yet.")).toBeVisible();
  await page.getByLabel("Preset").selectOption("Desk");
  await page.getByLabel("Fixed face").selectOption("x-min");
  await page.getByLabel("Load face").selectOption("x-max");
  await page.getByLabel("Material").selectOption("PETG");
  await page.getByRole("button", { name: "Run study" }).click();
  const progress = page.getByTestId("study-progress");
  await expect(progress).toBeVisible();
  await expect(progress).toContainText(/meshing|solving/);
  await expect(page.getByRole("button", { name: "Cancel study" })).toBeVisible();
  await expect(page.getByTestId("sf")).toContainText("3.17", { timeout: 10_000 });
  await expect(progress).toHaveCount(0);
  await expect(page.getByTestId("study-row")).toHaveCount(1);
  await expect(page.getByTestId("stress-canvas")).toHaveAttribute("data-state", "loaded");
  const post = (await fake.calls()).find((c) => c.method === "POST" && c.path === "/sims");
  expect(post?.body).toEqual({ part_id: "mount_plate", fix: "x-min", load: { face: "x-max", newtons: 20, direction: "-z" }, material: "PETG", environment: "Desk" });
  // the studies run is in the console like any other run
  await expect(page.locator("[data-region=console]")).toContainText("studies");
});

test("cancel_stops_a_long_study", async ({ page, fake }) => {
  await fake.reset(withSims({ outcome: "hang", step_ms: 100 }) as never);
  await open(page);
  await page.getByRole("button", { name: "Run study" }).click();
  await expect(page.getByTestId("study-progress")).toHaveAttribute("data-stage", "solve_fine", { timeout: 10_000 });
  await page.getByRole("button", { name: "Cancel study" }).click();
  await expect(page.getByTestId("study-progress")).toHaveCount(0, { timeout: 10_000 });
  await expect(page.getByTestId("study-row").first()).toContainText("cancelled");
  await expect(page.getByTestId("study-failed")).toContainText("Study cancelled");
  const calls = await fake.calls();
  expect(calls.some((c) => c.method === "POST" && /^\/runs\/[^/]+\/cancel$/.test(c.path))).toBe(true);
  await expect(page.getByRole("button", { name: "Run study" })).toBeEnabled();
});

test("missing_calculix_shows_the_install_card_and_blocks_run", async ({ page, fake }) => {
  await fake.reset(withSims({ ccx: false }) as never);
  await open(page);
  const card = page.getByTestId("install-card");
  await expect(card).toContainText("CalculiX is not installed");
  await expect(card.locator("code")).toHaveText("conda install -c conda-forge calculix");
  await expect(page.getByRole("button", { name: "Run study" })).toBeDisabled();
  const before = (await fake.calls()).filter((c) => c.path === "/sims/solvers").length;
  await card.getByRole("button", { name: "Check again" }).click();
  await expect.poll(async () => (await fake.calls()).filter((c) => c.path === "/sims/solvers").length).toBe(before + 1);
});

test("a_solver_failure_is_a_clear_error", async ({ page, fake }) => {
  await fake.reset(withSims({ outcome: "solve_failed", step_ms: 50 }) as never);
  await open(page);
  await page.getByRole("button", { name: "Run study" }).click();
  await expect(page.getByTestId("study-failed")).toContainText("CalculiX failed", { timeout: 10_000 });
  await expect(page.getByTestId("study-failed")).toContainText("check the setup");
});

test("a_setup_the_core_rejects_in_the_tool_shows_the_reason", async ({ page, fake }) => {
  await fake.reset(withSims({ step_ms: 50 }) as never);
  await open(page);
  // same face for fix and load is caught before the round trip
  await page.getByLabel("Load face").selectOption("bottom");
  await expect(page.getByTestId("setup-problems")).toContainText("the same");
  await expect(page.getByRole("button", { name: "Run study" })).toBeDisabled();
});

test("ask_agent_to_fix_starts_a_designer_run_with_the_result", async ({ page, fake }) => {
  await fake.reset(withSims({ records: "fixtures" }) as never);
  await open(page);
  await page.getByRole("button", { name: "Collapse the agent console" }).click();
  await page.getByRole("button", { name: "Ask agent to fix" }).click();
  await expect(page.getByTestId("asked")).toContainText("Sent to designer");
  const post = (await fake.calls()).find((c) => c.method === "POST" && c.path === "/runs");
  const body = post?.body as { agent: string; prompt: string; edit_scope: string[] };
  expect(body.agent).toBe("designer");
  expect(body.edit_scope).toEqual(["mount_plate"]);
  expect(body.prompt).toContain("guide only, not a guarantee");
  expect(body.prompt).toContain("including the fixed face 3.17");
  // the console opens on the new run
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-console", "open");
});

test("visual_rules_square_corners_and_blue_only_on_the_selected_row", async ({ page, fake }) => {
  await fake.reset(withSims({ records: "fixtures" }) as never);
  await open(page);
  await expect(page.getByTestId("sf")).toContainText("3.17");
  const offenders = await page.evaluate((blues) => {
    const bad: string[] = [];
    for (const el of Array.from(document.querySelectorAll("[data-testid=studies] *"))) {
      const cs = getComputedStyle(el);
      if (cs.borderTopLeftRadius !== "0px") bad.push(`radius ${el.tagName}.${el.className}`);
      const blue = [cs.color, cs.backgroundColor, cs.borderTopColor].some((c) => blues.includes(c));
      if (blue && !el.closest("[aria-current=true]") && !(el instanceof HTMLSelectElement && el.matches(":focus"))) bad.push(`blue ${el.tagName}.${el.className}`);
    }
    return bad;
  }, [THEMES.dark.blue, "rgb(141, 186, 255)"]);
  expect(offenders).toEqual([]);
});
