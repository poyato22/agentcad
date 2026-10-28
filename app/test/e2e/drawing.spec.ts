// Drawing page against the fake core (base scenario: one built part, mount_plate). The fake
// serves the real core's drawing SVG fixture. Both themes.
import type { Page } from "@playwright/test";
import type { PartSummary } from "../../src/api/types";
import { loadScenario } from "../fake-core/server";
import { expect, test, type FakeControl } from "./fixtures";

const RUN = "b7c79a57cbb4";
const sheet = (page: Page) => page.locator("[data-region=page] img.drawing-sheet");
const svgCalls = async (fake: FakeControl) => (await fake.calls()).filter((c) => c.path === "/parts/mount_plate/drawing.svg");

async function openDrawing(page: Page, theme: "dark" | "light") {
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
  await page.locator("[data-region=topbar]").getByRole("tab", { name: "Drawing" }).click();
  await expect(sheet(page)).toBeVisible();
  // the SVG really decoded as a picture
  await expect.poll(() => sheet(page).evaluate((i) => (i as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
}

test.beforeEach(async ({ fake, page }) => {
  await fake.reset("base");
  await page.setViewportSize({ width: 1440, height: 900 });
});

for (const theme of ["dark", "light"] as const) {
  test(`renders_the_core_svg_as_an_image_with_facts_${theme}`, async ({ page }) => {
    await openDrawing(page, theme);
    expect(await sheet(page).getAttribute("src")).toMatch(/^blob:/);
    await expect(sheet(page)).toHaveAttribute("alt", "Drawing · Part · mount plate");
    // the SVG's markup never enters the document
    expect(await page.locator("#view-front, #sheet, svg #dims").count()).toBe(0);
    const facts = page.getByRole("list", { name: "Drawing facts" });
    await expect(facts).toContainText("scale 2:1");
    await expect(facts).toContainText("2x Ø4.2 THRU");
    // black-on-transparent lines: inverted on the dark palette, as-is on light
    const filter = await sheet(page).evaluate((i) => getComputedStyle(i).filter);
    if (theme === "dark") expect(filter).toContain("invert");
    else expect(filter).toBe("none");
    // square corners
    expect(await page.getByRole("button", { name: "Export SVG" }).evaluate((b) => getComputedStyle(b).borderRadius)).toBe("0px");
  });

  test(`pan_and_zoom_${theme}`, async ({ page }) => {
    await openDrawing(page, theme);
    const zoom = page.getByTestId("zoom");
    await expect(zoom).toHaveText("100%");
    const scale = async () => Number(await sheet(page).getAttribute("data-scale"));
    const s0 = await scale();
    const vp = page.locator(".drawing-viewport");
    const box = await vp.boundingBox();
    if (!box) throw new Error("no viewport");
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.wheel(0, -400);
    await expect.poll(scale).toBeGreaterThan(s0);
    await expect(zoom).not.toHaveText("100%");
    const before = await sheet(page).evaluate((i) => (i as HTMLElement).style.transform);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 120, box.y + box.height / 2 + 60, { steps: 4 });
    await page.mouse.up();
    expect(await sheet(page).evaluate((i) => (i as HTMLElement).style.transform)).not.toBe(before);
    await page.getByRole("button", { name: "Fit" }).click();
    await expect(zoom).toHaveText("100%");
    await page.getByRole("button", { name: "Zoom in" }).click();
    await expect(zoom).toHaveText("125%");
    await vp.focus();
    await page.keyboard.press("0");
    await expect(zoom).toHaveText("100%");
  });
}

test("export_svg_and_dxf_download_and_pdf_prints_only_the_sheet", async ({ page }) => {
  await openDrawing(page, "dark");
  const [svg] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Export SVG" }).click()]);
  expect(svg.suggestedFilename()).toBe("mount_plate.svg");
  const [dxf] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Export DXF" }).click()]);
  expect(dxf.suggestedFilename()).toBe("mount_plate.dxf");

  await page.evaluate(() => { (window as unknown as { __prints: number }).__prints = 0; window.print = () => { (window as unknown as { __prints: number }).__prints++; }; });
  await page.getByRole("button", { name: "Export PDF" }).click();
  expect(await page.evaluate(() => (window as unknown as { __prints: number }).__prints)).toBe(1);
  await page.emulateMedia({ media: "print" });
  const printCopy = page.locator(".drawing-print img");
  await expect(printCopy).toBeVisible();
  expect(await printCopy.evaluate((i) => getComputedStyle(i).filter)).toBe("none");
  expect(await page.locator("[data-region=topbar]").evaluate((e) => getComputedStyle(e).visibility)).toBe("hidden");
  await page.emulateMedia({ media: "screen" });
});

test("regenerates_when_the_part_is_rebuilt_and_on_material_change", async ({ page, fake }) => {
  await openDrawing(page, "dark");
  expect(await svgCalls(fake)).toHaveLength(1);
  const r = await fetch(`${fake.url}/parts`, { headers: { Authorization: `Bearer ${fake.token}` } });
  const parts = (await r.json()) as PartSummary[];
  await fake.emit({ type: "tool_result", run_id: RUN, data: { call_id: "rb1", name: "mirror_part_set_vars", ok: true, summary: "ok" } },
    { parts: parts.map((p) => ({ ...p, mtime: p.mtime + 5 })) });
  await expect.poll(async () => (await svgCalls(fake)).length).toBe(2);
  await page.getByRole("combobox", { name: "Material" }).selectOption("PETG");
  await expect.poll(async () => (await svgCalls(fake)).at(-1)?.query.material).toBe("PETG");
  await expect(page.getByRole("list", { name: "Drawing facts" })).toContainText("PETG");
});

test("an_unbuilt_part_says_what_to_do", async ({ page, fake }) => {
  await fake.reset({ ...loadScenario("base"), print: { unbuilt: ["mount_plate"] } } as unknown as Record<string, unknown>);
  await page.goto("/");
  await page.locator("[data-region=topbar]").getByRole("tab", { name: "Drawing" }).click();
  await expect(page.getByRole("alert")).toContainText("Part · mount plate has no built model yet.");
  await expect(page.getByRole("alert")).toContainText("build it with mirror_part_create");
});
