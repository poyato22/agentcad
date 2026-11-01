// Plan 10 E8: the immersive study view draws the study's loads (arrows at the load face, labelled
// with size and direction) and fixtures (hatched) over the result, with a legend and a
// "Show loads" toggle. Real-core study fixtures (core/scripts/dump_study_fixtures.py).
// MISES_SHOTS=<dir> also saves it in both themes.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "./fixtures";

test.use({ launchOptions: { args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] } });

const SHOTS = process.env.MISES_SHOTS;
const FX = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui");
const read = (n: string): unknown => JSON.parse(readFileSync(resolve(FX, n), "utf8"));
const BASE = JSON.parse(readFileSync(resolve(import.meta.dirname, "../fake-core/scenarios/base.json"), "utf8")) as Record<string, unknown>;
const DEFAULT_BUILD = (read("projects.json") as { id: string }[])[0].id;
const RUN = { ...(read("study_agent_run.json") as Record<string, unknown>), project_id: DEFAULT_BUILD };

test("the immersive study shows its loads, fixtures and a legend over the result (E8)", async ({ page, fake }) => {
  await fake.reset({
    ...BASE, parts: read("study_parts.json"), glb: { beam: resolve(FX, "study_part_beam.glb"), cube: resolve(FX, "study_part_cube.glb") },
    runs: [RUN], steps: read("study_agent_steps.json"), approvals: [], sims: { studies: true, step_ms: 60 },
  } as never);
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  const stat = page.locator("[data-testid=study-card][data-type=static]").first();
  await expect(stat).toHaveCount(1);
  await stat.getByTestId("open-immersive").evaluate((el) => (el as HTMLElement).click());
  const imm = page.getByTestId("immersive");
  await expect(imm).toHaveAttribute("data-status", "done");
  const canvas = imm.getByTestId("study-canvas");
  await expect(canvas).not.toHaveAttribute("data-markers", "0");
  const legend = imm.getByTestId("marker-legend");
  await expect(legend.locator("[data-role=load]")).toContainText("Load");
  await expect(legend.locator("[data-role=support]")).toContainText("Fixed face");
  await expect(imm.getByTestId("load-label").first()).toHaveText(/^\S+ N · /);
  await imm.getByTestId("legend").waitFor();
  await page.waitForTimeout(400);
  if (SHOTS) {
    for (const t of ["dark", "light"] as const) {
      await page.evaluate((th) => document.documentElement.setAttribute("data-theme", th), t);
      await page.waitForTimeout(300);
      await page.screenshot({ path: `${SHOTS}/immersive-loads-${t}.png` });
    }
  }
  // Show loads off: the result alone
  await imm.getByTestId("toggle-loads").click();
  await expect(canvas).toHaveAttribute("data-markers", "0");
  await expect(imm.getByTestId("marker-legend")).toHaveCount(0);
  await expect(imm.getByTestId("load-label")).toHaveCount(0);
  await imm.getByTestId("toggle-loads").click();
  await expect(imm.getByTestId("marker-legend")).toBeVisible();
});
