// The flows the final review's QA run did not exercise (final-678-app.md): a PDF and a PNG
// drawing imported from Home (with m2's full-width result row and "Open part"), the two-point
// measure and parts-list multi-select with Shift and ⌘ (Plan 9 D6 removed the assembly drag).
// Runs in Chromium and WebKit (the app's engine).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";
import { expect, openHome, test } from "./fixtures";

const DRAWINGS = resolve(import.meta.dirname, "../../../core/tests/fixtures/drawings");

test.describe("drawing import from Home", () => {
  test.beforeEach(async ({ fake, page }) => {
    await fake.reset("base");
    await openHome(page);
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  for (const file of [{ name: "plate_vector.pdf", kind: "PDF", text: /PDF text/ }, { name: "plate_drawing.png", kind: "PNG", text: /only the model's reading was checked/ }]) {
    test(`a ${file.kind} from the Home tile makes a part, shown full width, and Open part goes to it`, async ({ page, fake }) => {
      await page.goto("/");
      await expect(page.locator('[data-testid="home"]')).toBeVisible();
      await page.locator("[data-drawing-drop=tile] [data-drawing-input]").setInputFiles({
        name: file.name, mimeType: file.kind === "PDF" ? "application/pdf" : "image/png", buffer: readFileSync(resolve(DRAWINGS, file.name)),
      });
      const card = page.locator(".home-imports .imp-card").first();
      await expect(card).toHaveAttribute("data-import-state", "done", { timeout: 15_000 });
      // m2: the result is a full-width row, not a 200 px grid cell
      const w = (await card.boundingBox())!.width;
      expect(w).toBeGreaterThan(600);
      await expect(card.locator("[data-result]")).toHaveText(/dimensions match the model's reading/);
      await expect(card.locator("[data-check=text]")).toHaveText(file.text);
      await expect(card.locator("[data-check=second-look]")).toContainText("Second opinion");
      await expect(card.locator("[data-import-build]")).toHaveText("Default");       // where it landed
      const post = (await fake.calls()).find((c) => c.method === "POST" && c.path === "/imports/drawing");
      expect(post).toBeTruthy();
      await card.getByRole("button", { name: "Open part" }).click();
      await expect(page.locator('[data-testid="home"]')).toHaveCount(0);
      await expect(page.locator("[data-region=viewer]")).toBeVisible();
      await expect(page.locator("[data-import-cards]")).toHaveCount(0);              // not floating over the viewer
    });
  }
});

test.describe("measure and the parts list", () => {
  test.beforeEach(async ({ fake, page }) => {
    await fake.reset("builds");
    await page.setViewportSize({ width: 1440, height: 900 });
  });
  test.afterEach(async ({ fake }) => { await fake.reset("base"); });

  const tree = (page: Page) => page.getByTestId("part-tree");

  async function openPlate(page: Page): Promise<void> {
    await page.goto("/");
    await expect(page.getByRole("tab", { name: "Quad frame" })).toHaveAttribute("aria-selected", "true");
    await tree(page).getByRole("button", { name: "top plate", exact: true }).click();
    await expect(page.getByTestId("viewer-canvas")).toHaveAttribute("data-bbox-size", "40.00,40.00,3.00", { timeout: 15_000 });
  }

  /** A model corner's client point, nudged a few px toward `toward` so the click lands on the part. */
  async function corner(page: Page, p: number[], toward: number[]): Promise<{ x: number; y: number }> {
    return page.evaluate(({ p, toward }) => {
      const v = (window as unknown as { __misesViewer: { project(q: number[]): { x: number; y: number } } }).__misesViewer;
      const a = v.project(p); const c = v.project(toward);
      const d = Math.hypot(c.x - a.x, c.y - a.y) || 1;
      return { x: a.x + ((c.x - a.x) / d) * 3, y: a.y + ((c.y - a.y) / d) * 3 };
    }, { p, toward });
  }

  test("two-point measure: two corners give the core's distance and its components", async ({ page, fake }) => {
    await openPlate(page);
    await page.getByTestId("viewer-canvas").focus();
    await page.keyboard.press("m");
    const panel = page.getByTestId("measure-panel");
    await expect(panel).toBeVisible();
    await page.waitForTimeout(300);                                   // the frame that fits the part
    const a = await corner(page, [-20, -20, 1.5], [0, 0, 1.5]);
    const b = await corner(page, [20, -20, 1.5], [0, 0, 1.5]);
    await page.mouse.click(a.x, a.y);
    await page.keyboard.down("Shift");
    await page.mouse.click(b.x, b.y);
    await page.keyboard.up("Shift");
    await expect(panel.locator('[data-value="distance"] dd')).toHaveText(/^40(\.0+)? mm$/);
    await expect(panel.locator('[data-value="dx"] dd')).toHaveText(/^40(\.0+)? mm$/);
    const post = (await fake.calls()).filter((c) => c.method === "POST" && c.path === "/parts/top_plate/measure").at(-1)?.body as { refs: { point: number[] }[] };
    expect(post.refs).toHaveLength(2);
    expect(post.refs.map((r) => r.point)).toEqual([[-20, -20, 1.5], [20, -20, 1.5]]);
  });

  test("multi-select: Shift selects a range, ⌘ toggles one, and the menu acts on the selection", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("tab", { name: "Quad frame" })).toHaveAttribute("aria-selected", "true");
    await tree(page).getByRole("button", { name: "Quad arm", exact: true }).click();
    await tree(page).getByRole("button", { name: "top plate", exact: true }).click({ modifiers: ["Shift"] });
    await expect(tree(page).locator(".tree-row.sel")).toHaveCount(2);
    await tree(page).getByRole("button", { name: "top plate", exact: true }).click({ modifiers: ["Meta"] });
    await expect(tree(page).locator(".tree-row.sel")).toHaveCount(1);
    await tree(page).getByRole("button", { name: "top plate", exact: true }).click({ modifiers: ["Meta"] });
    await expect(tree(page).locator(".tree-row.sel")).toHaveCount(2);
    await tree(page).locator('[data-part="top_plate"] .tree-row').click({ button: "right" });
    await page.getByRole("menuitem", { name: "Hide 2 parts" }).click();
    await expect(tree(page).locator(".tree-row.is-hidden")).toHaveCount(2);
  });
});

