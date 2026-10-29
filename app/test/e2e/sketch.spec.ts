// Sketch page e2e against the fake core: draw with the mouse (and a synthetic pen), snap, label,
// undo/redo, upload a photo, then "Build part" → POST /sketches + POST /runs, and the agent's
// clarifying questions arrive in the console.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Locator, Page } from "@playwright/test";
import { expect, test, type FakeCall } from "./fixtures";

const SCENARIO_DIR = resolve(import.meta.dirname, "../fake-core/scenarios");
const base = JSON.parse(readFileSync(resolve(SCENARIO_DIR, "base.json"), "utf8")) as Record<string, unknown>;
const PNG_1PX = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=", "base64");

function question(n: number, text: string) {
  return { at_ms: 50 * n, event: { type: "question_asked", run_id: "$run", data: { question_id: `q${n}$run`, question: text, options: ["5 mm", "8 mm"] } } };
}

/** base + three clarifying questions after any run starts (what a sketch run's agent asks). */
const WITH_QUESTIONS = {
  ...base,
  on: { create_run: [question(1, "How thick is the plate?"), question(2, "Is the circle a through hole?"), question(3, "Which material?")] },
};

async function openSketch(page: Page): Promise<Locator> {
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await page.getByRole("tab", { name: "Sketch" }).click();
  const svg = page.locator("[data-sketch-canvas]");
  await expect(svg).toBeVisible();
  return svg;
}

/** Screen position of a sheet point (mm, origin bottom-left) — the inverse of view.screenToMm, for
 *  the view the canvas reports in data-view ("x y w h" in mm). */
async function screenOf(svg: Locator, x: number, y: number): Promise<[number, number]> {
  const b = await svg.boundingBox();
  if (!b) throw new Error("no canvas");
  const [vx, vy, vw, vh] = ((await svg.getAttribute("data-view")) ?? "0 0 200 150").split(" ").map(Number);
  const scale = Math.min(b.width / vw, b.height / vh);
  const x0 = b.x + (b.width - vw * scale) / 2;
  const y0 = b.y;                                   // xMidYMin: top-aligned
  return [x0 + (x - vx) * scale, y0 + (vy + vh - y) * scale];
}

async function useTool(page: Page, name: "Pen" | "Line" | "Rectangle" | "Circle" | "Select") {
  await page.getByRole("button", { name: new RegExp(`^${name}`) }).click();
}

async function drag(page: Page, svg: Locator, from: [number, number], to: [number, number], steps = 8) {
  const a = await screenOf(svg, ...from);
  const b = await screenOf(svg, ...to);
  await page.mouse.move(...a);
  await page.mouse.down();
  await page.mouse.move(...b, { steps });
  await page.mouse.up();
}

async function sketchPost(calls: () => Promise<FakeCall[]>) {
  const c = (await calls()).filter((x) => x.method === "POST" && x.path === "/sketches");
  return c.at(-1)?.body as { sketch: { strokes: { kind: string; points: number[][]; closed: boolean; radius_mm?: number; center?: number[] }[]; dimensions: unknown[]; notes: string }; photo: { data_b64: string; name: string } | null; render_png_b64: string | null };
}

test.beforeEach(async ({ fake, page }) => {
  await fake.reset("base");
  await page.setViewportSize({ width: 1440, height: 900 });
});

test("draw_line_rect_circle_with_mouse_snaps_to_mm_grid", async ({ page, fake }) => {
  const svg = await openSketch(page);
  await page.locator(".sk-hint").click();          // focus the page, not a text field: tool keys work
  await page.keyboard.press("l");
  // ~0.8 mm off the 5 mm grid: the line snaps to (10,10) → (50,10)
  await drag(page, svg, [10.8, 9.3], [50.9, 10.7]);
  await expect(svg.locator("[data-kind=line]")).toHaveCount(1);
  await page.keyboard.press("r");
  await drag(page, svg, [60.6, 20.4], [99.2, 49.4]);
  await page.keyboard.press("c");
  await drag(page, svg, [130.4, 70.3], [140.6, 70.2]);
  await expect(svg.locator("[data-stroke-id]")).toHaveCount(3);
  // the last stroke (the circle) is selected (blue) and described in mm
  await expect(svg.locator("[data-kind=circle]")).toHaveClass(/selected/);
  await expect(page.locator("[data-selected-stroke]")).toContainText("Circle · Ø20.0 mm drawn");

  await page.getByRole("button", { name: "Build part" }).click();
  await expect(page.getByRole("status")).toContainText("Run started");
  const body = await sketchPost(() => fake.calls());
  expect(body.sketch.strokes.map((s) => s.kind)).toEqual(["line", "rect", "circle"]);
  expect(body.sketch.strokes[0].points).toEqual([[10, 10], [50, 10]]);
  expect(body.sketch.strokes[1].points).toEqual([[60, 20], [100, 20], [100, 50], [60, 50]]);
  expect(body.sketch.strokes[1].closed).toBe(true);
  expect(body.sketch.strokes[2]).toMatchObject({ center: [130, 70], radius_mm: 10, closed: true });
});

test("line_angle_snap_and_endpoint_snap", async ({ page, fake }) => {
  const svg = await openSketch(page);
  await page.getByRole("checkbox", { name: "Grid" }).uncheck();
  await page.keyboard.press("l");                  // a checkbox has focus: tool keys still work
  await drag(page, svg, [20, 20], [60, 21.5]);            // 2° off horizontal → horizontal
  await drag(page, svg, [60.6, 20.4], [80, 41]);           // starts on the first line's end; ~47° → 45°
  await page.getByRole("button", { name: "Build part" }).click();
  await expect(page.getByRole("status")).toContainText("Run started");
  const [a, b] = (await sketchPost(() => fake.calls())).sketch.strokes;
  expect(a.points[0][1]).toBeCloseTo(a.points[1][1], 2);
  expect(b.points[0]).toEqual(a.points[1]);
  const [p, q] = b.points;
  expect(q[0] - p[0]).toBeCloseTo(q[1] - p[1], 1);
});

test("freehand_with_mouse_and_pen_closes_loops", async ({ page, fake }) => {
  const svg = await openSketch(page);
  await expect(page.getByRole("button", { name: /^Pen/ })).toHaveAttribute("aria-pressed", "true");
  // mouse freehand: an open wiggle
  const pts: [number, number][] = Array.from({ length: 30 }, (_, i) => [20 + i * 2, 100 + 6 * Math.sin(i / 3)]);
  const first = await screenOf(svg, ...pts[0]);
  await page.mouse.move(...first);
  await page.mouse.down();
  for (const p of pts.slice(1)) await page.mouse.move(...(await screenOf(svg, ...p)));
  await page.mouse.up();
  // pen (synthetic pointer events, pointerType "pen"): a loop that ends near its start
  const loop: [number, number][] = Array.from({ length: 40 }, (_, i) => [120 + 15 * Math.cos((i / 40) * 2 * Math.PI), 40 + 15 * Math.sin((i / 40) * 2 * Math.PI)]);
  const screen = await Promise.all(loop.map((p) => screenOf(svg, ...p)));
  await svg.evaluate((el, s) => {
    const fire = (type: string, [x, y]: number[]) => el.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerId: 7, pointerType: "pen", clientX: x, clientY: y, buttons: 1, pressure: 0.5 }));
    fire("pointerdown", s[0]);
    for (const p of s.slice(1)) fire("pointermove", p);
    fire("pointerup", s[s.length - 1]);
  }, screen);
  await expect(svg.locator("[data-kind=freehand]")).toHaveCount(2);
  await page.getByRole("button", { name: "Build part" }).click();
  await expect(page.getByRole("status")).toContainText("Run started");
  const strokes = (await sketchPost(() => fake.calls())).sketch.strokes;
  expect(strokes[0].closed).toBe(false);
  expect(strokes[0].points.length).toBeGreaterThan(4);
  expect(strokes[1].closed).toBe(true);
  for (const [x, y] of strokes[1].points) expect(Math.hypot(x - 120, y - 40)).toBeCloseTo(15, 0);
});

test("dimension_labels_undo_redo_clear", async ({ page, fake }) => {
  const svg = await openSketch(page);
  await useTool(page, "Line");
  await drag(page, svg, [10, 10], [50, 10]);
  const input = page.getByLabel("Dimension label");
  await input.fill("40");
  await input.press("Enter");
  await expect(svg.locator("[data-label-for]")).toHaveText("40");
  await expect(page.locator("[data-dim-parsed]")).toHaveText("length · 40 mm");
  // typed label text renders as text, never as markup
  await input.fill("<b>clamps</b> here");
  await input.press("Enter");
  await expect(svg.locator("[data-label-for]")).toHaveText("<b>clamps</b> here");
  await expect(svg.locator("b")).toHaveCount(0);

  await svg.click({ position: { x: 5, y: 5 } }); // focus out of the input (pen tool not active: line)
  await page.keyboard.press("ControlOrMeta+z");
  await expect(svg.locator("[data-label-for]")).toHaveText("40");
  await page.keyboard.press("ControlOrMeta+z");
  await expect(svg.locator("[data-label-for]")).toHaveCount(0);
  await page.keyboard.press("ControlOrMeta+Shift+z");
  await expect(svg.locator("[data-label-for]")).toHaveText("40");

  await page.getByRole("button", { name: "Clear" }).click();
  await expect(svg.locator("[data-stroke-id]")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Build part" })).toBeDisabled();
  await page.getByRole("button", { name: "Undo" }).click();
  await expect(svg.locator("[data-stroke-id]")).toHaveCount(1);

  await page.getByRole("button", { name: "Build part" }).click();
  await expect(page.getByRole("status")).toContainText("Run started");
  expect((await sketchPost(() => fake.calls())).sketch.dimensions).toEqual([{ stroke_id: "s1", text: "40", value_mm: 40, kind: "length" }]);
});

test("photo_upload_checks_type_and_size", async ({ page }) => {
  await openSketch(page);
  const chooser = page.getByLabel("Choose photo");
  await chooser.setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("not a picture") });
  await expect(page.getByRole("alert")).toContainText("PNG, JPEG or HEIC");
  const big = Buffer.concat([PNG_1PX, Buffer.alloc(10 * 1024 * 1024)]);
  await chooser.setInputFiles({ name: "huge.png", mimeType: "image/png", buffer: big });
  await expect(page.getByRole("alert")).toContainText("10 MB at most");
  await chooser.setInputFiles({ name: "napkin.png", mimeType: "image/png", buffer: PNG_1PX });
  await expect(page.locator("[data-photo-name]")).toContainText("napkin.png · PNG");
  await expect(page.getByRole("img", { name: "Uploaded sketch photo" })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  // a photo alone is enough to build
  await expect(page.getByRole("button", { name: "Build part" })).toBeEnabled();
});

test("build_part_sends_sketch_photo_render_then_questions_arrive", async ({ page, fake }) => {
  await fake.reset(WITH_QUESTIONS);
  const svg = await openSketch(page);
  await useTool(page, "Rectangle");
  await drag(page, svg, [20, 20], [100, 60]);
  await page.getByLabel("Dimension label").fill("80");
  await page.getByLabel("Dimension label").press("Enter");
  await page.getByLabel("Notes for the agent").fill("wall bracket, PETG");
  await page.getByLabel("Choose photo").setInputFiles({ name: "napkin.png", mimeType: "image/png", buffer: PNG_1PX });
  await expect(page.locator("[data-build-target]")).toHaveText("designer · anthropic claude-sonnet-5-5");
  await expect(page.locator("[data-vision-note]")).toHaveCount(0);

  await page.getByRole("button", { name: "Build part" }).click();
  await expect(page.getByRole("status")).toContainText("Run started");
  const calls = await fake.calls();
  const body = await sketchPost(async () => calls);
  expect(body.sketch).toMatchObject({ notes: "wall bracket, PETG", dimensions: [{ text: "80", value_mm: 80, kind: "length" }] });
  expect(body.photo).toEqual({ data_b64: PNG_1PX.toString("base64"), name: "napkin.png" });
  // the drawing itself, rasterised by the webview
  expect(Buffer.from(body.render_png_b64 ?? "", "base64").subarray(0, 4)).toEqual(Buffer.from([0x89, 0x50, 0x4e, 0x47]));
  const run = calls.filter((c) => c.method === "POST" && c.path === "/runs").at(-1);
  const sketchCall = calls.findIndex((c) => c.path === "/sketches");
  expect(calls.indexOf(run as FakeCall)).toBeGreaterThan(sketchCall);
  expect(run?.body).toMatchObject({ agent: "designer", prompt: "Build the part shown in my sketch.", sketch_id: expect.stringMatching(/^sk_[0-9a-f]{12}$/) });
  // A6: the run carries the model shown in the build target, not a re-resolved default
  expect((run?.body as Record<string, unknown>).model_ref).toBe("anthropic/claude-sonnet-5-5");

  // the new run is selected in the console and its (at most 3) questions show as cards there
  const consoleRegion = page.locator("[data-region=console]");
  await expect(consoleRegion.locator("[data-question]")).toHaveCount(3);
  await expect(consoleRegion).toContainText("How thick is the plate?");
});

test("non_vision_model_gets_no_image_note", async ({ page, fake }) => {
  await fake.reset({ ...base, setup: { profile: { default_model: "ollama/qwen3", setup_completed_at: 1 } } });
  await openSketch(page);
  await expect(page.locator("[data-build-target]")).toHaveText("designer · ollama qwen3");
  await page.getByLabel("Choose photo").setInputFiles({ name: "napkin.png", mimeType: "image/png", buffer: PNG_1PX });
  await expect(page.locator("[data-vision-note]")).toContainText("can't read images");
});

test("core_error_shows_hint_as_text", async ({ page, fake }) => {
  const models = base.models as { vendors: { vendor: string; key: string }[] };
  await fake.reset({ ...base, models: { ...models, vendors: models.vendors.map((v) => (v.vendor === "anthropic" ? { ...v, key: "none" } : v)) } });
  const svg = await openSketch(page);
  await useTool(page, "Line");
  await drag(page, svg, [10, 10], [50, 10]);
  await page.getByRole("button", { name: "Build part" }).click();
  await expect(page.getByRole("alert")).toContainText("no API key for anthropic");
});

// ---- Sketch v2: typed dimensions, guides, constraints, templates, extrude preview

type V2Body = { sketch: { strokes: { id: string; kind: string; points: number[][]; center?: number[]; radius_mm?: number }[]; constraints?: { kind: string; refs: { stroke_id: string; edge?: number }[]; value_mm?: number }[]; extrude_mm?: number } };

test("typed_40x20_rectangle_then_d4_hole_then_build", async ({ page, fake }) => {
  const svg = await openSketch(page);
  await page.locator(".sk-hint").click();
  await page.keyboard.press("r");
  await expect(page.locator("[data-hint]")).toHaveText("Click the first corner, or type x,y");
  await page.mouse.click(...(await screenOf(svg, 20, 20)));
  await expect(page.locator("[data-hint]")).toHaveText("Click the opposite corner, or type 40,20");
  await page.mouse.move(...(await screenOf(svg, 45, 35)));        // the way the rectangle goes: up and right
  await page.keyboard.type("40,20");
  await expect(page.locator("[data-typed]")).toContainText("40,20");
  await page.keyboard.press("Enter");
  await expect(svg.locator("[data-kind=rect]")).toHaveCount(1);
  // the typed sizes became driving dimensions, shown on the geometry
  await expect(svg.locator("[data-ckind=distance]")).toHaveCount(2);
  await expect(svg.locator("[data-ckind=distance] text")).toHaveText(["40", "20"]);
  await expect(page.locator("[data-profiles]")).toHaveText("1 closed profile");
  await expect(svg.locator("[data-profile]")).toHaveCount(1);

  await page.keyboard.press("c");
  await page.mouse.click(...(await screenOf(svg, 30, 30)));
  await page.keyboard.type("4");
  await page.keyboard.press("Enter");
  await expect(svg.locator("[data-kind=circle]")).toHaveCount(1);
  await expect(svg.locator("[data-ckind=diameter] text")).toHaveText("Ø4");
  await expect(page.locator("[data-profiles]")).toHaveText("1 closed profile · 1 hole");

  await page.getByLabel("Extrude depth (mm)").fill("6");
  await page.getByLabel("Extrude depth (mm)").press("Enter");
  await page.getByRole("button", { name: "Build part" }).click();
  await expect(page.getByRole("status")).toContainText("Run started");
  const body = (await sketchPost(() => fake.calls())) as unknown as V2Body;
  const [rect, hole] = body.sketch.strokes;
  expect(rect).toMatchObject({ kind: "rect", points: [[20, 20], [60, 20], [60, 40], [20, 40]] });
  expect(hole).toMatchObject({ kind: "circle", center: [30, 30], radius_mm: 2 });
  expect(body.sketch.constraints).toEqual([
    { id: expect.any(String), kind: "distance", refs: [{ stroke_id: rect.id, edge: 0 }], value_mm: 40 },
    { id: expect.any(String), kind: "distance", refs: [{ stroke_id: rect.id, edge: 1 }], value_mm: 20 },
    { id: expect.any(String), kind: "diameter", refs: [{ stroke_id: hole.id }], value_mm: 4 },
  ]);
  expect(body.sketch.extrude_mm).toBe(6);
});

test("inference_guides_show_while_drawing_and_lines_pick_up_constraints", async ({ page, fake }) => {
  const svg = await openSketch(page);
  await page.getByRole("checkbox", { name: "Grid" }).uncheck();
  await page.locator(".sk-hint").click();
  await page.keyboard.press("l");
  await page.mouse.click(...(await screenOf(svg, 20, 100)));
  await page.mouse.move(...(await screenOf(svg, 70, 100.6)), { steps: 4 });
  await expect(svg.locator("[data-guide=horizontal]")).toHaveCount(1);
  await expect(svg.locator(".sk-guide-label").first()).toHaveText("Horizontal");
  await page.mouse.click(...(await screenOf(svg, 70, 100.6)));
  // the second line starts on the first one's end (Endpoint) and goes straight up
  await page.mouse.move(...(await screenOf(svg, 70.4, 100.3)));
  await expect(svg.locator("[data-snap=endpoint]")).toHaveCount(1);
  await page.mouse.click(...(await screenOf(svg, 70.4, 100.3)));
  await page.mouse.move(...(await screenOf(svg, 70.8, 130)), { steps: 4 });
  await expect(svg.locator("[data-guide=vertical]")).toHaveCount(1);
  await page.mouse.click(...(await screenOf(svg, 70.8, 130)));
  await expect(svg.locator("[data-kind=line]")).toHaveCount(2);
  await expect(svg.locator("[data-ckind=horizontal]")).toHaveCount(1);
  await expect(svg.locator("[data-ckind=vertical]")).toHaveCount(1);
  await expect(svg.locator("[data-ckind=coincident]")).toHaveCount(1);
  await page.getByRole("button", { name: "Build part" }).click();
  await expect(page.getByRole("status")).toContainText("Run started");
  const [a, b] = ((await sketchPost(() => fake.calls())) as unknown as V2Body).sketch.strokes;
  expect(a.points[1][1]).toBeCloseTo(a.points[0][1], 6);
  expect(b.points[0]).toEqual(a.points[1]);
  expect(b.points[1][0]).toBeCloseTo(b.points[0][0], 6);
});

test("keyboard_only_line_tool_enter_on_a_button_then_draw_and_dimension", async ({ page, fake }) => {
  const svg = await openSketch(page);
  await page.locator(".sk-hint").click();
  await page.keyboard.press("l");
  // Enter on a focused template button presses it, even with the Line tool active
  await page.getByRole("button", { name: /^Plate with holes/ }).focus();
  await page.keyboard.press("Enter");
  await expect(svg.locator("[data-kind=rect]")).toHaveCount(1);
  // draw a line by keyboard alone: first point, then a length
  await page.locator(".sk-hint").click();
  await page.keyboard.type("0,0");
  await page.keyboard.press("Enter");
  await page.keyboard.type("30");
  await page.keyboard.press("Enter");
  await expect(svg.locator("[data-kind=line]")).toHaveCount(1);
  // pick it in the Shapes list and give it a 45 mm length with the keyboard
  const list = page.getByRole("listbox", { name: "Shapes" });
  await list.focus();
  await page.keyboard.press("End");
  await expect(list.getByRole("option", { selected: true })).toHaveText(/^Line/);
  await page.getByLabel("Dimension label").fill("45");
  await page.getByLabel("Dimension label").press("Enter");
  await page.getByRole("button", { name: "Build part" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("status")).toContainText("Run started");
  const body = (await sketchPost(() => fake.calls())) as unknown as V2Body;
  const line = body.sketch.strokes.find((s) => s.kind === "line");
  expect(line?.points).toEqual([[0, 0], [45, 0]]);
});

test("template_constraint_edit_and_vertex_drag_keep_the_rectangle", async ({ page }) => {
  const svg = await openSketch(page);
  await page.getByRole("button", { name: /^Plate with holes/ }).click();
  await expect(svg.locator("[data-kind=rect]")).toHaveCount(1);
  await expect(svg.locator("[data-kind=circle]")).toHaveCount(4);
  await expect(page.locator("[data-profiles]")).toHaveText("1 closed profile · 4 holes");
  await expect(page.locator("[data-extrude-preview], .sk-preview-none")).toHaveCount(1);
  // pick the 80 mm dimension and change it to 100: the plate follows
  await svg.locator("[data-ckind=distance] text").first().click();
  await expect(page.locator("[data-selected-constraint]")).toContainText("Distance · 80 mm");
  await page.getByLabel("Constraint value (mm)").fill("100");
  await page.getByLabel("Constraint value (mm)").press("Enter");
  await expect(svg.locator("[data-ckind=distance] text").first()).toHaveText("100");
  await page.getByRole("button", { name: "Fit" }).click();           // the wider plate back in view
  // select the plate by its bottom edge and drag a corner: it stays a rectangle with its sizes
  await page.getByRole("button", { name: /^Select/ }).click();
  const pts = async () => JSON.parse(await svg.locator("[data-kind=rect]").evaluate((el) => JSON.stringify((el.getAttribute("points") ?? "").split(" ").map((p) => p.split(",").map(Number))))) as number[][];
  const before = await pts();
  const edge = await svg.locator("[data-kind=rect]").evaluate((el) => {
    const r = (el as SVGGraphicsElement).getBoundingClientRect();
    return [r.x + r.width / 2, r.y + r.height] as [number, number];
  });
  await page.mouse.click(edge[0], edge[1] - 1);
  await expect(svg.locator("[data-handle]")).toHaveCount(4);
  const corner = await svg.locator("[data-handle]").nth(2).boundingBox();
  if (!corner) throw new Error("no handle");
  await page.mouse.move(corner.x + corner.width / 2, corner.y + corner.height / 2);
  await page.mouse.down();
  await page.mouse.move(corner.x + 60, corner.y - 40, { steps: 6 });
  await page.mouse.up();
  const after = await pts();
  const w = (p: number[][]) => Math.abs(p[1][0] - p[0][0]);
  const h = (p: number[][]) => Math.abs(p[2][1] - p[1][1]);
  expect(w(after)).toBeCloseTo(w(before), 1);                      // the 100 mm width is held
  expect(h(after)).toBeCloseTo(h(before), 1);                      // and the 50 mm height
  expect(after[0][1]).toBeCloseTo(after[1][1], 6);                 // still axis-aligned
  expect(after).not.toEqual(before);                               // but it moved
  // undo puts it back in one step
  await page.keyboard.press("ControlOrMeta+z");
  expect(await pts()).toEqual(before);
});
