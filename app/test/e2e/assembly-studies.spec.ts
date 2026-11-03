// Plan 13 H4: the Studies page's "Whole assembly" target against the fake core (the real core's
// assembly study fixtures, on the motion dump's hinged build): the studies agent's pick, a drop
// study run with live progress, its result (pass / fail checks, charts), Watch opening the build's
// full window with the whole assembly falling (Section / X-ray on its bar), and the study's card in
// the Simulations stream. Screenshots land in test-results/h4-*.png.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "./fixtures";

test.use({ launchOptions: { args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] } });

const FX = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui/motion");
const BASE = JSON.parse(readFileSync(resolve(import.meta.dirname, "../fake-core/scenarios/base.json"), "utf8")) as { runs: Record<string, unknown>[] } & Record<string, unknown>;
const PROJECT = (JSON.parse(readFileSync(resolve(import.meta.dirname, "../fixtures/core-ui/projects.json"), "utf8")) as { id: string }[])[0].id;

function scenario(): Record<string, unknown> {
  const { dof_remaining: _d, overconstrained: _o, ...stored } = JSON.parse(readFileSync(resolve(FX, "motion_assembly.json"), "utf8")) as Record<string, unknown>;
  return { ...BASE, script: [], runs: [{ ...BASE.runs[0], role: "lead", project_id: PROJECT, status: "running" }], assembly: { by_project: { [PROJECT]: stored } } };
}

test.beforeEach(async ({ fake, page }) => {
  await fake.reset(scenario() as never);
  await page.setViewportSize({ width: 1440, height: 900 });
});

test("a_whole_assembly_drop_study_runs_shows_its_checks_and_plays_the_assembly_falling", async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto("/");
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-instances", "2");
  await page.getByRole("tab", { name: "Studies" }).click();
  await page.getByTestId("target-assembly").click();
  const side = page.getByTestId("assembly-studies");
  await expect(side.getByTestId("assembly-recommended")).toContainText("walk study");
  await side.getByTestId("asy-kind-drop").click();
  await side.getByTestId("asy-field-drop_m").fill("1.5");
  await side.getByTestId("asy-run").click();
  await expect(side.getByTestId("asy-progress")).toBeVisible();
  const result = page.getByTestId("assembly-result");
  await expect(result).toHaveAttribute("data-verdict", "fail", { timeout: 10_000 });
  await expect(result.getByTestId("asy-check").first()).toContainText("peak deceleration");
  await expect(result.locator("svg").first()).toBeVisible();          // a chart
  // the study is in the dock's Simulations stream, and its card opens it in Studies again
  await page.getByRole("tab", { name: "Design" }).click();
  await page.getByRole("tab", { name: /^Simulations/ }).click();
  const card = page.locator("[data-dock] [data-stream=sim] [data-record=assembly] [data-testid=assembly-study-card]");
  await expect(card).toHaveCount(1);
  await expect(card).toHaveAttribute("data-status", "done");
  await expect(card).toContainText("Whole assembly · Drop");
  await card.getByTestId("assembly-study-open").click();
  await expect(result).toHaveAttribute("data-verdict", "fail");
  for (const t of ["dark", "light"] as const) {
    await page.evaluate((th) => document.documentElement.setAttribute("data-theme", th), t);
    await page.waitForTimeout(200);
    await page.screenshot({ path: `test-results/h4-result-${t}.png` });
  }
  await result.getByTestId("asy-watch").click();
  const imm = page.getByTestId("assembly-immersive");
  await expect(imm).toHaveAttribute("data-mode", "animation");
  await expect(imm).toContainText("Drop study");
  await expect(page.getByTestId("motion-xray")).toBeVisible();
  await expect.poll(async () => Number(await page.getByTestId("motion-time").textContent()), { timeout: 4000 }).toBeGreaterThan(0.3);
  await page.screenshot({ path: "test-results/h4-watch.png" });
});
