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
  const parts = (JSON.parse(readFileSync("test/fake-core/scenarios/robot/parts.json", "utf8")) as Record<string, unknown>[])
    .map((p): Record<string, unknown> => ({ ...p, project_id: PROJECT }));
  const stored = JSON.parse(readFileSync("test/fake-core/scenarios/robot/assembly.json", "utf8")) as Record<string, unknown>;
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

test("Play plays the build's newest saved animation after a reload; a menu lists them all (E5)", async ({ page, fake }) => {
  // saved like the real route answers (core/tests/fixtures/ui/motion/motion_animations.json)
  const anim = (id: string, name: string, joint: string, to: number) => ({ id, name, steps: [{ joint_id: joint, from: 0, to, duration_s: 1.0 }, { joint_id: joint, from: to, to: 0, duration_s: 1.0 }], loops: 1, duration_s: 2.0 });
  const sc = robot();
  await fake.reset({ ...sc, assembly: { ...(sc["assembly"] as object), animations: { [PROJECT]: [anim("a1", "Wave the arm", "j_elbow", 60), anim("a2", "Open the gripper", "j_finger_l", 9)] } } } as never);
  await openRobot(page);
  await expect(page.getByTestId("pill-animations")).toBeVisible();
  await page.getByTestId("pill-play").click();
  const imm = page.getByTestId("assembly-immersive");
  await expect(imm).toHaveAttribute("data-mode", "animation");
  await expect(imm).toContainText("Open the gripper");                 // the newest
  await page.keyboard.press("Escape");
  await expect(imm).toHaveCount(0);
  await page.getByTestId("pill-animations").click();
  const menu = page.getByTestId("animation-menu");
  await expect(menu.getByRole("menuitem")).toHaveText(["Open the gripper · newest", "Wave the arm"]);
  // the same button closes it again (E7)
  await page.getByTestId("pill-animations").click();
  await expect(menu).toHaveCount(0);
  await page.getByTestId("pill-animations").click();
  await menu.getByRole("menuitem", { name: "Wave the arm" }).click();
  await expect(page.getByTestId("assembly-immersive")).toContainText("Wave the arm");
});

async function at(page: Page, p: [number, number, number]): Promise<{ x: number; y: number }> {
  return page.evaluate((q) => (window as unknown as { __misesAssembly: { project(p: number[]): { x: number; y: number } } }).__misesAssembly.project(q), p);
}

test("laid out: every part flat in a grid with its name, and back (E36)", async ({ page, fake }) => {
  await fake.reset(robot());
  await openRobot(page);
  await page.getByTestId("pill-layout").click();
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-layout", "on");
  await expect(page.getByTestId("layout-label")).toHaveCount(13);
  await expect(page.getByTestId("layout-label").filter({ hasText: /^chassis$/ })).toHaveCount(1);
  await page.waitForTimeout(300);
  await shots(page, "robot-laid-out");
  // Explode instead: the layout goes, the explode comes
  await page.getByTestId("pill-explode").click();
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-layout", "off");
  await expect(page.getByTestId("layout-label")).toHaveCount(0);
  await page.getByTestId("pill-explode").click();
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-explode", "off");
});

test("the origin and R: the selected part's offset shows; R puts it on the origin; ⌘Z puts it back (E19, E11)", async ({ page, fake }) => {
  await fake.reset(robot());
  await openRobot(page);
  // the battery sits loose 18.5 mm up in the chassis pocket
  const b = await at(page, [-15, 0, 26]);
  await page.mouse.click(b.x, b.y);
  await expect(page.getByTestId("assembly-selection")).toContainText("battery");
  await expect(page.getByTestId("origin-delta")).toHaveText("Δ 0, 0, 18.5 mm");
  await shots(page, "robot-origin");
  await page.keyboard.press("r");
  await expect(page.getByTestId("assembly-note")).toContainText("battery is at the origin");
  await expect(page.getByTestId("origin-delta")).toHaveCount(0);
  await page.keyboard.press("Meta+z");
  await expect(page.getByTestId("undo-status")).toHaveText("Undid: Reset battery to the origin");
  await expect(page.getByTestId("origin-delta")).toHaveText("Δ 0, 0, 18.5 mm");
  // a part a joint places can't be reset on its own: the note says why
  const t = await at(page, [30, -20, 45]);
  await page.mouse.click(t.x, t.y);
  await expect(page.getByTestId("assembly-selection")).toContainText("turret");
  await page.keyboard.press("r");
  await expect(page.getByTestId("assembly-note")).toContainText("placed by its joint to chassis");
});

test("measure in the assembly: hover shows the spot, two clicks measure between them (E16)", async ({ page, fake }) => {
  await fake.reset(robot());
  await openRobot(page);
  await page.getByTestId("pill-measure").click();
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-tool", "measure");
  let a = await at(page, [-12, -35, 30]);
  await expect.poll(async () => {
    a = await at(page, [-12, -35, 30]);
    await page.mouse.move(a.x + 1, a.y);
    await page.mouse.move(a.x, a.y);
    return page.getByTestId("assembly-hover").count();
  }).toBe(1);
  await shots(page, "robot-measure-hover");
  await page.mouse.click(a.x, a.y);
  const c = await at(page, [12, -35, 30]);
  await page.mouse.move(c.x, c.y);
  await page.mouse.click(c.x, c.y);
  await expect(page.getByTestId("assembly-pick")).toHaveCount(2);
  const panel = page.getByTestId("assembly-measure");
  await expect(panel.locator('[data-value="distance"] dd')).toHaveText(/^2[34](\.\d+)? mm$/);
  await expect(panel.locator('[data-value="angle"] dd')).toHaveText("0°");
  await shots(page, "robot-measure-two");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("assembly-pick")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("assembly-canvas")).toHaveAttribute("data-tool", "select");
});

test("comment pins in the assembly name the instance and follow it through the explode (E35)", async ({ page, fake }) => {
  await fake.reset(robot());
  await openRobot(page);
  await page.getByTestId("pill-comment").click();
  const t = await at(page, [30, -20, 45]);
  await page.mouse.click(t.x, t.y);
  const box = page.getByTestId("comment-box");
  await expect(box).toContainText("turret");
  await box.getByRole("textbox").fill("make the turret taller");
  await box.getByRole("button", { name: "Send to agent" }).click();
  const pin = page.getByTestId("pin");
  await expect(pin).toHaveCount(1);
  const sent = (await fake.calls()).filter((c) => c.method === "POST" && /\/feedback$/.test(c.path)).at(-1);
  const body = (sent?.body ?? {}) as Record<string, unknown>;
  expect(body["instance_id"]).toBe("r_turret");
  expect(Array.isArray(body["world_point"])).toBe(true);
  const before = await pin.boundingBox();
  await page.getByTestId("pill-explode").click();
  await page.waitForTimeout(900);
  const after = await pin.boundingBox();
  expect(Math.hypot((after?.x ?? 0) - (before?.x ?? 0), (after?.y ?? 0) - (before?.y ?? 0))).toBeGreaterThan(5);
  await shots(page, "robot-pin-exploded");
});
