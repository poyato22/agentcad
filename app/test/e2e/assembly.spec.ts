// The build view e2e (Plan 9 D5 / D6 / D7 / D12 / D17 / D29): Chromium with SwiftShader WebGL,
// against the fake core seeded with the real core's hinge fixture (two mount plates on a revolute
// joint, limits 0–180°). The Design page IS the assembly: no side panel, no Move/Turn toolbar, no
// Part toggle; a tree click opens a part alone; the floating pill has Play, Section, X-ray, Explode.
// MISES_SHOTS=<dir> also saves the pill with the section on, in both themes.
import { readFileSync } from "node:fs";
import type { Page } from "@playwright/test";
import { expect, test, type FakeControl } from "./fixtures";

test.use({ launchOptions: { args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] } });

const SHOTS = process.env.MISES_SHOTS;
const HINGE = JSON.parse(readFileSync("test/fixtures/core-ui/assembly.json", "utf8")) as {
  instances: { id: string; name: string }[]; joints: { id: string }[];
};
const PROJECT = (JSON.parse(readFileSync("test/fixtures/core-ui/projects.json", "utf8")) as { id: string }[])[0].id;
const JOINT = HINGE.joints[0].id;

function scenario(): Record<string, unknown> {
  const base = JSON.parse(readFileSync("test/fake-core/scenarios/base.json", "utf8")) as Record<string, unknown>;
  const { dof_remaining: _d, overconstrained: _o, total_mass_g: _m, mass_unknown: _u, ...stored } = HINGE as unknown as Record<string, unknown>;
  return { ...base, script: [], assembly: { by_project: { [PROJECT]: stored } } };
}

async function openAssembly(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-instances", "2");
  await expect.poll(async () => (await pixels(page)).total, { timeout: 10_000 }).toBeGreaterThan(500);
}

/** Counts opaque pixels on the WebGL canvas (preserveDrawingBuffer), and red ones. */
async function pixels(page: Page): Promise<{ total: number; red: number }> {
  return page.evaluate(() => {
    const src = document.querySelector<HTMLCanvasElement>("[data-testid=assembly-canvas]");
    if (!src) throw new Error("no canvas");
    const c = document.createElement("canvas");
    c.width = src.width; c.height = src.height;
    const ctx = c.getContext("2d");
    if (!ctx) throw new Error("no 2d");
    ctx.drawImage(src, 0, 0);
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    let total = 0; let red = 0;
    for (let i = 0; i < d.length; i += 4) {
      if ((d[i + 3] ?? 0) < 200) continue;
      total++;
      if ((d[i] ?? 0) > 150 && (d[i + 1] ?? 0) < 120 && (d[i + 2] ?? 0) < 120) red++;
    }
    return { total, red };
  });
}

async function shots(page: Page, name: string): Promise<void> {
  if (!SHOTS) return;
  for (const t of ["dark", "light"] as const) {
    await page.evaluate((th) => document.documentElement.setAttribute("data-theme", th), t);
    await page.waitForTimeout(250);
    await page.screenshot({ path: `${SHOTS}/${name}-${t}.png` });
  }
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
}

const treeName = (page: Page, id: string) => page.locator(`[data-part="${id}"] .tree-name`);

test.beforeEach(async ({ fake, page }) => {
  await fake.reset(scenario());
  await page.setViewportSize({ width: 1440, height: 900 });
});

test("the_design_page_is_the_assembly_with_no_panel_no_toolbar_and_no_part_toggle", async ({ page }) => {
  await openAssembly(page);
  await expect(page.getByTestId("assembly-panel")).toHaveCount(0);
  await expect(page.getByTestId("mode-switch")).toHaveCount(0);
  await expect(page.getByRole("toolbar", { name: "Assembly tools" })).toHaveCount(0);
  for (const name of ["move", "turn", "snap", "fit", "Add part", "New joint"]) {
    await expect(page.getByRole("button", { name, exact: true })).toHaveCount(0);
  }
  await expect(page.getByTestId("viewer-pill")).toBeVisible();
  // the left surface: parts with their mass and the total, then the BOM, no History
  await expect(page.locator('[data-part="mount_plate"] [data-testid=row-mass]')).toHaveText(/\d g$/);
  await expect(page.getByTestId("parts-total")).toContainText(/Total\s*[\d.]+ (g|kg)/);
  await expect(page.getByTestId("bom-summary")).toBeVisible();
  await expect(page.locator("[data-region=left]")).not.toContainText("History");
  await expect(page.getByRole("button", { name: "Parts list" })).toHaveCount(0);
  // ⌥⌘A is gone: nothing flips
  await page.keyboard.press("Meta+Alt+KeyA");
  await expect(page.getByTestId("assembly-canvas")).toBeVisible();
});

test("the_assembly_shows_its_total_mass (Plan 9 B2)", async ({ page }) => {
  await openAssembly(page);
  // two mount plates: instances x the part's tree mass (volume x PLA, the build default)
  await expect(page.getByTestId("assembly-mass")).toHaveText(/^Mass \d+(\.\d+)? g$/);
  await expect(page.getByTestId("assembly-mass")).toHaveAttribute("title", /every instance/);
});

test("a_tree_click_opens_the_part_alone_and_back_or_esc_returns", async ({ page }) => {
  await openAssembly(page);
  await treeName(page, "mount_plate").click();
  await expect(page.getByTestId("viewer-canvas")).toBeVisible();
  await expect(page.getByTestId("assembly-canvas")).toHaveCount(0);
  await page.getByTestId("back-to-assembly").click();
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-instances", "2");
  await treeName(page, "mount_plate").click();
  await expect(page.getByTestId("viewer-canvas")).toBeVisible();
  await page.locator("body").click({ position: { x: 700, y: 40 } });          // focus off the row
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("assembly-canvas")).toBeVisible();
});

test("a_click_on_a_part_in_3D_selects_it_and_its_tree_row", async ({ page }) => {
  await openAssembly(page);
  const at = await page.evaluate(() => (window as unknown as { __misesAssembly: { project(p: number[]): { x: number; y: number } } }).__misesAssembly.project([0, 0, 0]));
  await page.mouse.click(at.x, at.y);
  await expect(page.getByTestId("assembly-selection")).toContainText("base");
  await expect(page.locator('[data-part="mount_plate"]')).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("assembly-canvas")).toBeVisible();              // selecting never opens the part
});

test("section_one_click_cuts_the_assembly_and_the_slider_sweeps_the_plane", async ({ page }) => {
  await openAssembly(page);
  const full = (await pixels(page)).total;
  await page.getByTestId("pill-section").click();
  await expect(page.getByTestId("pill-section")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-section", "on");
  const slider = page.getByTestId("section-slider");
  await expect(slider).toBeVisible();
  const [lo, hi] = await slider.evaluate((el) => [Number((el as HTMLInputElement).min), Number((el as HTMLInputElement).max)]);
  expect(hi).toBeGreaterThan(lo);
  const sweep = async (f: number) => {
    await slider.evaluate((el, v) => {
      const i = el as HTMLInputElement;
      i.value = String(v);
      i.dispatchEvent(new Event("input", { bubbles: true }));
    }, lo + (hi - lo) * f);
    await page.waitForTimeout(200);
    return (await pixels(page)).total;
  };
  const near = await sweep(0.15);
  const far = await sweep(0.95);
  expect(far).toBeGreaterThan(near * 1.3);                                       // the cut moved: more is kept
  expect(near).toBeLessThan(full);
  await expect(page.getByTestId("section-value")).toHaveText(/mm$/);
  // X / Y / Z and flip
  await page.getByRole("group", { name: "Section plane normal" }).getByRole("button", { name: "Y", exact: true }).click();
  await expect(page.getByRole("group", { name: "Section plane normal" }).getByRole("button", { name: "Y", exact: true })).toHaveAttribute("aria-pressed", "true");
  await sweep(0.5);
  await page.getByTestId("pill-section").click();
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-section", "off");
  await expect.poll(async () => (await pixels(page)).total).toBeGreaterThan(far * 0.9);
});

/** The Pi enclosure (geometry scenario) as an assembly whose tree matches it: case, lid and an
 *  Electronics group (board, battery, fan) — every row with its mass, the group its sum. */
function enclosure(): Record<string, unknown> {
  const sc = JSON.parse(readFileSync("test/fake-core/scenarios/geometry.json", "utf8")) as { parts: Record<string, unknown>[] } & Record<string, unknown>;
  const ids = ["pi_case", "pi_lid", "pi_board", "pi_battery", "pi_fan"];
  const I = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];
  const stored = { instances: ids.map((id, i) => ({ id: `i_${id}`, part_id: id, name: id.replace("pi_", ""), fixed: i === 0, transform: I })), joints: [] };
  return {
    ...sc, script: [],
    parts: [...sc.parts, { part_id: "electronics", vars: {}, metrics: null, mtime: 0 }],
    builds: { tree: {
      pi_case: { label: "Case", order: 0 }, pi_lid: { label: "Lid", order: 1 }, electronics: { label: "Electronics", kind: "group", order: 2 },
      pi_board: { label: "Board", parent_id: "electronics", order: 0 }, pi_battery: { label: "Battery", parent_id: "electronics", order: 1 },
      pi_fan: { label: "Fan", parent_id: "electronics", order: 2 },
    } },
    assembly: { by_project: { [PROJECT]: stored, b0default000: stored }, interference: "none" },
  };
}

/** Pixels on the assembly canvas lit well above the dark faces (luma > 150). */
async function bright(page: Page): Promise<number> {
  return page.evaluate(() => {
    const src = document.querySelector<HTMLCanvasElement>("[data-testid=assembly-canvas]") as HTMLCanvasElement;
    const c = document.createElement("canvas");
    c.width = src.width; c.height = src.height;
    const ctx = c.getContext("2d") as CanvasRenderingContext2D;
    ctx.drawImage(src, 0, 0);
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    let n = 0;
    for (let i = 0; i < d.length; i += 4) {
      if ((d[i + 3] ?? 0) < 200) continue;
      if (0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2] > 150) n++;
    }
    return n;
  });
}

test("the_section_is_unmistakable_from_the_default_camera_and_the_tree_matches_the_assembly", async ({ page, fake }) => {
  await fake.reset(enclosure());
  await page.goto("/");
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-instances", "5");
  await expect.poll(async () => (await pixels(page)).total, { timeout: 15_000 }).toBeGreaterThan(2000);
  // D9: every row its mass, the group the sum of its children, the total under the tree
  for (const id of ["pi_case", "pi_lid", "pi_board", "pi_battery", "pi_fan"]) {
    await expect(page.locator(`[data-part="${id}"] [data-testid=row-mass]`).first()).toHaveText(/\d g$/);
  }
  await expect(page.locator('[data-part="electronics"] > .tree-row [data-testid=row-mass]')).toHaveText("Σ 23.9 g");   // 5.555 + 15.475 + 2.867 (Σ: a sum, B2)
  await expect(page.getByTestId("parts-total")).toContainText("73.6 g");
  // one click from the default camera: the cut faces the camera, filled far from the faces
  await page.getByTestId("pill-section").click();
  const handle = page.getByTestId("section-handle");
  await expect(handle).toBeVisible();
  await page.waitForTimeout(300);
  // the cap: a large fill far brighter than the dark faces (dark theme), hatched; off, only the
  // thin edge lines are that bright
  const on = await bright(page);
  await page.getByTestId("pill-section").click();
  await page.waitForTimeout(300);
  const off = await bright(page);
  expect(on - off, `bright pixels with the cut ${on} vs without ${off}`).toBeGreaterThan(3000);
  await page.getByTestId("pill-section").click();
  await shots(page, "uc-assembly-section");
});

test("x_ray_and_explode_work_on_the_assembly", async ({ page }) => {
  await openAssembly(page);
  await page.getByTestId("pill-xray").click();
  await expect(page.getByTestId("pill-xray")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("pill-explode")).toBeEnabled();
  await page.getByTestId("pill-explode").click();
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-explode", "on");
  await shots(page, "uc-assembly-xray-explode");
  await page.getByRole("button", { name: "Clear the view modes" }).click();
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-explode", "off");
  await expect(page.getByTestId("pill-xray")).toHaveAttribute("aria-pressed", "false");
});

test("play_opens_the_assembly_full_window_and_esc_closes_it", async ({ page, fake }) => {
  await openAssembly(page);
  // no agent animation yet: an exploded tour
  await page.getByTestId("pill-play").click();
  const imm = page.getByTestId("assembly-immersive");
  await expect(imm).toContainText("Exploded tour");
  const box = await page.getByTestId("assembly-view").boundingBox();
  expect(box?.width).toBe(1440);
  expect(box?.height).toBe(900);
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-explode", "on");
  await page.keyboard.press("Escape");
  await expect(imm).toHaveCount(0);
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-explode", "off");
  // an agent animated the hinge: Play plays that
  await fake.emit({ type: "assembly_animate", run_id: null, data: { project_id: PROJECT, joint_id: JOINT, from: 0, to: 180, duration_s: 3 } });
  await page.getByTestId("pill-play").click();
  await expect(imm).toContainText("The agent's animation");
  await page.getByTestId("assembly-immersive-close").click();
  await expect(imm).toHaveCount(0);
  await expect(page.getByTestId("viewer-pill")).toBeVisible();
});

test("interference_shows_in_red_with_a_check_chip_in_the_pill", async ({ page }) => {
  await openAssembly(page);
  await expect(page.getByTestId("assembly-check-chip")).toContainText("1 clash", { timeout: 8_000 });
  await expect(page.getByTestId("assembly-check-chip")).toHaveAttribute("data-status", "fail");
  await expect(page.getByTestId("interference-tag")).toContainText("mm³");
  await expect.poll(async () => (await pixels(page)).red, { timeout: 8_000 }).toBeGreaterThan(50);
});

test("an_agent's_drive_arrives_and_glides_in", async ({ page, fake }) => {
  await openAssembly(page);
  const r = await fetch(`${fake.url}/projects/${PROJECT}/assembly/drive`, {
    method: "POST", headers: { Authorization: `Bearer ${fake.token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ joint_id: JOINT, value: 0 }),
  });
  expect(r.ok).toBe(true);
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-arriving", "on");
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-arriving", "off");
  // flat: the clash is gone
  await expect(page.getByTestId("assembly-check-chip")).toHaveCount(0, { timeout: 8_000 });
});

async function deletes(fake: FakeControl): Promise<{ method: string; path: string; query: Record<string, string> }[]> {
  return (await fake.calls()).filter((c) => (c.method === "DELETE" && c.path.startsWith("/parts/")) || c.path.endsWith("/restore"));
}

test("delete_a_part_with_its_instances_then_undo", async ({ page, fake }) => {
  await openAssembly(page);
  await page.locator('[data-part="mount_plate"] .tree-row').click({ button: "right" });
  await page.getByRole("menuitem", { name: /^Delete…/ }).click();
  const ask = page.getByTestId("confirm-delete");
  await expect(ask).toContainText("Also removes 2 instances from the assembly.");
  await ask.getByRole("button", { name: "Delete" }).click();
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-instances", "0");
  await expect(page.locator('[data-part="mount_plate"]')).toHaveCount(0);
  await expect(page.getByTestId("undo-toast")).toContainText("Deleted mount plate and 2 instances");
  expect((await deletes(fake))[0]).toMatchObject({ method: "DELETE", path: "/parts/mount_plate", query: { cascade: "true" } });
  await page.getByTestId("undo-toast").getByRole("button", { name: "Undo" }).click();
  await expect(page.locator('[data-part="mount_plate"]')).toHaveCount(1);
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-instances", "2");
  expect((await deletes(fake)).map((c) => c.method)).toEqual(["DELETE", "POST"]);
});

test("backspace_on_a_tree_row_asks_first_and_esc_keeps_the_part", async ({ page }) => {
  await openAssembly(page);
  await treeName(page, "mount_plate").focus();
  await page.keyboard.press("Backspace");
  await expect(page.getByTestId("confirm-delete")).toContainText("Delete mount plate?");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("confirm-delete")).toHaveCount(0);
  await expect(page.locator('[data-part="mount_plate"]')).toHaveCount(1);
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-instances", "2");
});

test("blue_only_on_selected_or_pressed_in_the_build_view", async ({ page }) => {
  await openAssembly(page);
  const offenders = await page.evaluate(() => {
    const BLUE = new Set(["rgb(75, 147, 255)", "rgb(141, 186, 255)"]);
    const ALLOWED = "[data-selected], [data-changing], [data-card], .active-tab, .cmd-prompt, .needs-you, [aria-pressed=true]";
    const out: string[] = [];
    for (const el of document.querySelectorAll<HTMLElement>("[data-region=viewer] *")) {
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue;
      const blue = [cs.color, cs.borderTopColor, cs.borderRightColor, cs.borderBottomColor, cs.borderLeftColor, cs.backgroundColor]
        .filter((c, i) => BLUE.has(c) && (i === 0 ? (el.textContent ?? "").trim() !== "" : i === 5 || parseFloat([cs.borderTopWidth, cs.borderRightWidth, cs.borderBottomWidth, cs.borderLeftWidth][i - 1]) > 0));
      if (blue.length && !el.closest(ALLOWED)) out.push(`${el.tagName.toLowerCase()}.${[...el.classList].join(".")} "${(el.textContent ?? "").trim().slice(0, 40)}"`);
    }
    return out;
  });
  expect(offenders).toEqual([]);
});
