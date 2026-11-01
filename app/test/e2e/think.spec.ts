// Plan 11 F8 against the fake core: the composer's Think control (Auto · Normal · Hard · Max).
// A pick rides on POST /projects/{id}/chat as `effort` for one message, the sent message shows the
// effort its turn ran at, and "Every message in this build" saves the build's default (PUT
// /projects/{id}/effort). MISES_SHOTS=<dir> saves screenshots.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const SHOTS = process.env.MISES_SHOTS;
const FX = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui");
const BASE = JSON.parse(readFileSync(resolve(import.meta.dirname, "../fake-core/scenarios/base.json"), "utf8")) as { runs: Record<string, unknown>[] } & Record<string, unknown>;
const BUILD = (JSON.parse(readFileSync(resolve(FX, "projects.json"), "utf8")) as { id: string }[])[0].id;
const scenario = () => ({ ...BASE, runs: [{ ...BASE.runs[0], role: "lead", project_id: BUILD, status: "done" }] });
const con = (page: Page) => page.locator("[data-region=console]");
const box = (page: Page) => page.getByLabel("Command line");
const think = (page: Page) => con(page).getByTestId("think-control");

async function open(page: Page) {
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await expect(con(page).getByRole("log", { name: "Conversation" })).toContainText("thicken the mount plate to 6 mm");
}

test("think_control_sends_one_pick_and_the_message_shows_its_effort", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page);
  await expect(think(page)).toHaveAttribute("data-think", "auto");
  await think(page).getByRole("button").click();
  await page.getByRole("menuitemcheckbox", { name: /^Max/ }).click();
  await expect(think(page)).toHaveAttribute("data-think", "max");
  if (SHOTS) await con(page).screenshot({ path: `${SHOTS}/think-max.png` });
  await box(page).fill("for the egg drop challenge make something really interesting");
  await box(page).press("Enter");
  await expect.poll(async () => (await fake.calls()).filter((c) => c.method === "POST" && c.path === `/projects/${BUILD}/chat`).map((c) => c.body))
    .toEqual([{ text: "for the egg drop challenge make something really interesting", effort: "max" }]);
  await expect(think(page)).toHaveAttribute("data-think", "auto");          // one message only
  const chip = con(page).getByTestId("effort-chip").last();
  await expect(chip).toHaveText("Thinking: Max · your pick");
  await expect(chip).toHaveAttribute("data-effort", "max");
  if (SHOTS) await con(page).screenshot({ path: `${SHOTS}/think-chip.png` });
});

test("think_control_every_message_in_this_build_saves_the_default", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page);
  await think(page).getByRole("button").click();
  await page.getByRole("menuitemcheckbox", { name: /^Hard/ }).click();
  await think(page).getByRole("button").click();
  await page.getByRole("menuitemcheckbox", { name: "Every message in this build" }).click();
  await expect(think(page)).toHaveAttribute("data-think", "hard");
  await expect(think(page)).toHaveAttribute("data-think-sticky", "");
  await expect.poll(async () => (await fake.calls()).filter((c) => c.method === "PUT" && c.path === `/projects/${BUILD}/effort`).map((c) => c.body))
    .toEqual([{ default: "hard" }]);
  // the default is the core's: Auto messages carry no effort of their own
  await box(page).fill("add a lid");
  await box(page).press("Enter");
  await expect.poll(async () => (await fake.calls()).filter((c) => c.method === "POST" && c.path === `/projects/${BUILD}/chat`).map((c) => c.body))
    .toEqual([{ text: "add a lid" }]);
  await page.reload();
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await expect(think(page)).toHaveAttribute("data-think", "hard");
});
