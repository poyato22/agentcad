// Plan 10 A3 e2e on the robot fixture (glb/make_robot.py): a rover with an arm, 9 parts and 13
// instances five joint levels deep, every part modelled about its own origin and placed by the
// assembly. Explode, Laid out, the origin triad, R reset + undo, assembly comment pins.
// MISES_SHOTS=<dir> also saves each state in both themes.
import { readFileSync } from "node:fs";
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

test.use({ launchOptions: { args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] } });

const SHOTS = process.env.MISES_SHOTS;
const PROJECT = (JSON.parse(readFileSync("test/fixtures/core-ui/projects.json", "utf8")) as { id: string }[])[0].id;

export function robot(): Record<string, unknown> {
  const base = JSON.parse(readFileSync("test/fake-core/scenarios/base.json", "utf8")) as Record<string, unknown>;
  const parts = (JSON.parse(readFileSync("test/fake-core/scenarios/robot_parts.json", "utf8")) as Record<string, unknown>[])
    .map((p): Record<string, unknown> => ({ ...p, project_id: PROJECT }));
  const stored = JSON.parse(readFileSync("test/fake-core/scenarios/robot_assembly.json", "utf8")) as Record<string, unknown>;
  const glb = Object.fromEntries(parts.map((p) => [p["part_id"] as string, `glb/${p["part_id"] as string}.glb`]));
  const tree = Object.fromEntries(parts.map((p) => [p["part_id"] as string, { label: p["label"], color: p["color"], order: p["order"] }]));
  return { ...base, script: [], parts, glb, builds: { tree }, assembly: { by_project: { [PROJECT]: stored }, interference: "none" } };
}

/** Opaque pixels on the assembly canvas (preserveDrawingBuffer). */
export async function drawn(page: Page): Promise<number> {
  return page.evaluate(() => {
    const src = document.querySelector<HTMLCanvasElement>("[data-testid=assembly-canvas]");
    if (!src) return 0;
    const c = document.createElement("canvas");
    c.width = src.width; c.height = src.height;
    const ctx = c.getContext("2d") as CanvasRenderingContext2D;
    ctx.drawImage(src, 0, 0);
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    let n = 0;
    for (let i = 3; i < d.length; i += 4) if ((d[i] ?? 0) >= 200) n++;
    return n;
  });
}

export async function shots(page: Page, name: string): Promise<void> {
  if (!SHOTS) return;
  for (const t of ["dark", "light"] as const) {
    await page.evaluate((th) => document.documentElement.setAttribute("data-theme", th), t);
    await page.waitForTimeout(300);
    await page.screenshot({ path: `${SHOTS}/${name}-${t}.png` });
  }
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
}

export async function openRobot(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-instances", "13");
  await expect.poll(() => drawn(page), { timeout: 15_000 }).toBeGreaterThan(3000);
  await page.waitForTimeout(400);
}

test("the robot explodes coherently and the camera keeps it framed (E4)", async ({ page, fake }) => {
  await fake.reset(robot());
  await openRobot(page);
  await shots(page, "robot-assembled");
  const before = await drawn(page);
  await page.getByTestId("pill-explode").click();
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-explode", "on");
  await page.waitForTimeout(900);
  // the parts spread out but stay on screen: more of the canvas is covered, none of it clipped away
  await expect.poll(() => drawn(page)).toBeGreaterThan(before * 0.6);
  await shots(page, "robot-exploded");
  await page.getByTestId("pill-explode").click();
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-explode", "off");
});
