// Assembly mode e2e (Plan 5 A4): Chromium with SwiftShader WebGL, against the fake core seeded
// with the real core's hinge fixture (two mount plates on a revolute joint, limits 0–180°).
import { readFileSync } from "node:fs";
import type { Page } from "@playwright/test";
import { expect, test, type FakeControl } from "./fixtures";

test.use({ launchOptions: { args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] } });

const HINGE = JSON.parse(readFileSync("test/fixtures/core-ui/assembly.json", "utf8")) as {
  instances: { id: string; name: string }[]; joints: { id: string }[];
};
const PROJECT = (JSON.parse(readFileSync("test/fixtures/core-ui/projects.json", "utf8")) as { id: string }[])[0].id;
const JOINT = HINGE.joints[0].id;
const [BASE, LID] = HINGE.instances.map((i) => i.id);

function scenario(): Record<string, unknown> {
  const base = JSON.parse(readFileSync("test/fake-core/scenarios/base.json", "utf8")) as Record<string, unknown>;
  const { dof_remaining: _d, overconstrained: _o, ...stored } = HINGE as unknown as Record<string, unknown>;
  return { ...base, script: [], assembly: { by_project: { [PROJECT]: stored } } };
}

async function openAssembly(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByTestId("mode-switch").getByRole("radio", { name: "assembly" }).click();
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-instances", "2");
}

async function drives(fake: FakeControl): Promise<unknown[]> {
  return (await fake.calls()).filter((c) => c.method === "POST" && c.path.endsWith("/assembly/drive")).map((c) => c.body);
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

/** Chooses the option whose label starts with `prefix` (face labels end with area and id). */
async function selectStarting(select: import("@playwright/test").Locator, prefix: string): Promise<void> {
  const value = await select.evaluate((el, p) => [...(el as HTMLSelectElement).options].find((o) => o.text.startsWith(p))?.value ?? "", prefix);
  expect(value, `an option starting "${prefix}"`).not.toBe("");
  await select.selectOption(value);
}

test.beforeEach(async ({ fake, page }) => {
  await fake.reset(scenario());
  await page.addInitScript(() => { try { localStorage.removeItem("mises.design.mode"); } catch { /* none */ } });
  await page.setViewportSize({ width: 1440, height: 900 });
});

test("assembly_toggle_shows_the_hinge_and_option_cmd_A_flips_back", async ({ page }) => {
  await openAssembly(page);
  const panel = page.getByTestId("assembly-panel");
  await expect(panel.locator(`[data-instance-id="${BASE}"]`)).toContainText("grounded");
  await expect(panel.locator(`[data-instance-id="${LID}"]`)).toContainText("hinge");
  await expect(page.getByTestId("joint-value")).toHaveText("90°");
  await expect.poll(async () => (await pixels(page)).total, { timeout: 10_000 }).toBeGreaterThan(500);
  await page.keyboard.press("Meta+Alt+KeyA");
  await expect(page.getByTestId("viewer-canvas")).toBeVisible();
  await page.keyboard.press("Meta+Alt+KeyA");
  await expect(page.getByTestId("assembly-canvas")).toBeVisible();
});

test("joint_slider_drives_the_core", async ({ page, fake }) => {
  await openAssembly(page);
  const slider = page.locator(`[data-joint-id="${JOINT}"] input[type=range]`);
  await slider.focus();
  await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByTestId("joint-value")).toHaveText("88°");
  await expect.poll(async () => (await drives(fake)).at(-1)).toEqual({ joint_id: JOINT, value: 88 });
  await page.keyboard.press("Home");
  await expect(page.getByTestId("joint-value")).toHaveText("0°");
  await expect.poll(async () => (await drives(fake)).at(-1)).toEqual({ joint_id: JOINT, value: 0 });
});

test("dragging_the_slider_sends_few_drives_and_lands_on_the_last_value", async ({ page, fake }) => {
  await openAssembly(page);
  const box = await page.locator(`[data-joint-id="${JOINT}"] input[type=range]`).boundingBox();
  if (!box) throw new Error("no slider");
  const y = box.y + box.height / 2;
  await page.mouse.move(box.x + box.width / 2, y);
  await page.mouse.down();
  for (let i = 1; i <= 30; i++) await page.mouse.move(box.x + box.width / 2 - (box.width / 2) * (i / 30), y);
  await page.mouse.up();
  await expect(page.getByTestId("joint-value")).toHaveText("0°");
  await expect.poll(async () => (await drives(fake)).at(-1)).toEqual({ joint_id: JOINT, value: 0 });
  const n = (await drives(fake)).length;
  expect(n).toBeGreaterThan(0);
  expect(n).toBeLessThanOrEqual(31);
});

test("animate_sweeps_and_puts_the_joint_back_without_writing", async ({ page, fake }) => {
  await openAssembly(page);
  const card = page.locator(`[data-joint-id="${JOINT}"]`);
  const before = (await drives(fake)).length;
  await card.getByRole("button", { name: "animate" }).click();
  await expect(card).toHaveAttribute("data-animating", "on");
  await expect.poll(async () => page.getByTestId("joint-value").textContent()).not.toBe("90°");
  await expect(card).not.toHaveAttribute("data-animating", "on", { timeout: 6_000 });
  await expect(page.getByTestId("joint-value")).toHaveText("90°");
  expect((await drives(fake)).length).toBe(before);
});

test("interference_shows_in_red_with_its_volume_and_clears_when_flat", async ({ page }) => {
  await openAssembly(page);
  const row = page.getByTestId("interference-row");
  await expect(row).toContainText(/base and lid overlap by \d+ mm³/, { timeout: 8_000 });
  await expect(page.getByTestId("interference-tag")).toContainText("mm³");
  await expect.poll(async () => (await pixels(page)).red, { timeout: 8_000 }).toBeGreaterThan(50);
  await page.locator(`[data-joint-id="${JOINT}"] input[type=range]`).focus();
  await page.keyboard.press("Home");
  await expect(page.getByTestId("assembly-check")).toContainText("No parts interfere.", { timeout: 8_000 });
  await expect(page.getByTestId("interference-tag")).toHaveCount(0);
});

test("new_joint_by_keyboard_picks_faces_kind_and_limits", async ({ page, fake }) => {
  await openAssembly(page);
  await page.locator(`[data-joint-id="${JOINT}"]`).getByRole("button", { name: /Delete the hinge/ }).click();
  await expect(page.locator("[data-joint-id]")).toHaveCount(0);
  await page.getByRole("button", { name: "New joint" }).click();
  const form = page.getByTestId("joint-form");
  await form.getByRole("radio", { name: "slide", exact: true }).click();
  await form.getByLabel("First face: part").selectOption({ label: "base" });
  await selectStarting(form.getByLabel("First face: face"), "+Z face");
  await form.getByLabel("Second face: part").selectOption({ label: "lid" });
  await selectStarting(form.getByLabel("Second face: face"), "−Z face");
  await form.getByLabel("Upper limit (mm)").fill("15");
  await form.getByLabel("Upper limit (mm)").blur();
  await form.getByRole("button", { name: "Create joint" }).click();
  await expect(form).toHaveCount(0);
  const call = (await fake.calls()).find((c) => c.method === "POST" && c.path.endsWith("/assembly/joints"));
  expect(call?.body).toMatchObject({
    kind: "slider", limits: [0, 15],
    a: { instance_id: BASE, frame: { axis: [0, 0, 1] } },
    b: { instance_id: LID, frame: { axis: [0, 0, 1] } },
  });
  await expect(page.locator("[data-joint-id]")).toHaveCount(1);
  await expect(page.locator("[data-joint-id]")).toContainText("Slide");
});

test("an_agent's_drive_arrives_and_glides_in", async ({ page, fake }) => {
  await openAssembly(page);
  await expect(page.getByTestId("joint-value")).toHaveText("90°");
  // an agent drives the hinge through the core (the fake emits assembly_changed)
  const r = await fetch(`${fake.url}/projects/${PROJECT}/assembly/drive`, {
    method: "POST", headers: { Authorization: `Bearer ${fake.token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ joint_id: JOINT, value: 30 }),
  });
  expect(r.ok).toBe(true);
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-arriving", "on");
  await expect(page.getByTestId("joint-value")).toHaveText("30°");
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-arriving", "off");
});

test("add_part_needs_its_own_name", async ({ page, fake }) => {
  await openAssembly(page);
  const add = page.getByRole("form", { name: "Add a part" });
  await add.getByLabel("Name in assembly").fill("lid");
  await add.getByRole("button", { name: "Add part" }).click();
  await expect(add.getByRole("alert")).toContainText("already in this assembly");
  await add.getByLabel("Name in assembly").fill("cover");
  await add.getByRole("button", { name: "Add part" }).click();
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-instances", "3");
  const call = (await fake.calls()).find((c) => c.method === "POST" && c.path.endsWith("/assembly/instances"));
  expect(call?.body).toMatchObject({ part_id: "mount_plate", name: "cover", fixed: false });
});

test("blue_only_on_selected_or_picking_in_assembly_mode", async ({ page }) => {
  await openAssembly(page);
  await page.locator(`[data-instance-id="${LID}"]`).click();
  await page.getByRole("button", { name: "New joint" }).click();
  const offenders = await page.evaluate(() => {
    const BLUE = new Set(["rgb(61, 139, 255)", "rgb(141, 186, 255)"]);
    const ALLOWED = "[data-selected], [data-changing], [data-card], .active-tab, .cmd-prompt, .needs-you";
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

interface Probe { project(p: number[]): { x: number; y: number }; gizmoAxis(): string | null }

/** Hovers around `center` (mm) until the gizmo handle `axis` is under the pointer; returns that point. */
async function findHandle(page: Page, center: number[], axis: string): Promise<{ x: number; y: number }> {
  const c = await page.evaluate((p) => (window as unknown as { __misesAssembly: Probe }).__misesAssembly.project(p), center);
  for (let r = 10; r <= 220; r += 6) {
    for (let a = 0; a < 360; a += 10) {
      const x = c.x + r * Math.cos((a * Math.PI) / 180); const y = c.y + r * Math.sin((a * Math.PI) / 180);
      await page.mouse.move(x, y);
      if (await page.evaluate(() => (window as unknown as { __misesAssembly: Probe }).__misesAssembly.gizmoAxis()) === axis) return { x, y };
    }
  }
  throw new Error(`no ${axis} handle near the part`);
}

test("dragging_the_hinge_ring_turns_the_lid_in_15_degree_steps", async ({ page, fake }) => {
  await openAssembly(page);
  await page.locator(`[data-instance-id="${LID}"]`).click();
  await expect(page.getByTestId("assembly-hint")).toContainText("along its hinge");
  const at = await findHandle(page, [20, 0, 0], "Z");
  await page.mouse.down();
  for (let i = 1; i <= 20; i++) await page.mouse.move(at.x + i * 4, at.y + i * 5);
  await page.mouse.up();
  const shown = Number((await page.getByTestId("joint-value").textContent())?.replace("°", ""));
  expect(shown).not.toBe(90);
  expect(shown % 15).toBe(0);
  await expect.poll(async () => (await drives(fake)).at(-1)).toEqual({ joint_id: JOINT, value: shown });
});

test("switching_mode_while_the_gizmo_is_held_leaves_nothing_stuck", async ({ page, fake }) => {
  await openAssembly(page);
  await page.locator(`[data-instance-id="${LID}"]`).click();
  const at = await findHandle(page, [20, 0, 0], "Z");
  await page.mouse.down();
  for (let i = 1; i <= 8; i++) await page.mouse.move(at.x + i * 4, at.y + i * 5);
  await page.keyboard.press("Meta+Alt+KeyA");        // to Part mode, gizmo still held
  await expect(page.getByTestId("viewer-canvas")).toBeVisible();
  await page.mouse.up();
  await page.keyboard.press("Meta+Alt+KeyA");        // back
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-instances", "2");
  // the core's value is shown, and an agent's change still glides in (the controller isn't busy)
  const core = (await (await fetch(`${fake.url}/projects/${PROJECT}/assembly`, { headers: { Authorization: `Bearer ${fake.token}` } })).json()) as { joints: { value: number }[] };
  await expect(page.getByTestId("joint-value")).toHaveText(`${core.joints[0].value}°`);
  await fetch(`${fake.url}/projects/${PROJECT}/assembly/drive`, {
    method: "POST", headers: { Authorization: `Bearer ${fake.token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ joint_id: JOINT, value: 135 }),
  });
  await expect(page.getByTestId("joint-value")).toHaveText("135°");
});

test("escape_stops_animate_and_restores_the_joint", async ({ page, fake }) => {
  await openAssembly(page);
  const card = page.locator(`[data-joint-id="${JOINT}"]`);
  await card.getByRole("button", { name: "animate" }).click();
  await expect.poll(async () => page.getByTestId("joint-value").textContent()).not.toBe("90°");
  await page.locator("body").click({ position: { x: 5, y: 890 } }); // focus away from the button
  await page.keyboard.press("Escape");
  await expect(card).not.toHaveAttribute("data-animating", "on");
  await expect(page.getByTestId("joint-value")).toHaveText("90°");
  expect(await drives(fake)).toEqual([]);
});

test("dragging_a_free_part_moves_it_in_whole_mm_and_saves_once", async ({ page, fake }) => {
  await openAssembly(page);
  await page.locator(`[data-instance-id="${BASE}"]`).click();
  await page.getByRole("checkbox", { name: /Grounded/ }).uncheck();
  await expect(page.locator(`[data-instance-id="${BASE}"]`)).toContainText("free");
  const at = await findHandle(page, [0, 0, 0], "X");
  await page.mouse.down();
  for (let i = 1; i <= 15; i++) await page.mouse.move(at.x + i * 5, at.y + i * 2);
  await page.mouse.up();
  const patches = async () => (await fake.calls()).filter((c) => c.method === "PATCH" && c.path.endsWith(`/instances/${BASE}`) && "transform" in (c.body as object));
  await expect.poll(async () => (await patches()).length).toBe(1);
  const t = ((await patches())[0].body as { transform: number[][] }).transform;
  expect(t[0][3]).not.toBe(0);
  expect(Number.isInteger(t[0][3])).toBe(true);
  expect(t[1][3]).toBe(0);
  expect(t[2][3]).toBe(0);
});
