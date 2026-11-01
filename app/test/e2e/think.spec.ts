// G8 (Plan 11 F8) against the fake core: the composer's Think control, always visible as
// Auto · Low · Medium · High · Max. A pick is saved as the build's (PUT /projects/{id}/effort,
// it survives a reload) and rides on every POST /projects/{id}/chat as `effort`; the sent message
// shows the level its turn ran at; Max shows a usage note. Screenshots dark + light land in
// test-results/think-*.png.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

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

async function shots(page: Page, name: string) {
  for (const scheme of ["dark", "light"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.waitForTimeout(150);
    await con(page).screenshot({ path: `test-results/think-${name}-${scheme}.png` });
  }
  await page.emulateMedia({ colorScheme: "dark" });
}

test("think_control_shows_every_level_and_a_pick_persists_for_the_build", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page);
  await expect(think(page)).toHaveAttribute("data-think", "auto");
  await expect(think(page).getByRole("radio")).toHaveText(["Auto", "Low", "Medium", "High", "Max"]);
  await think(page).getByTestId("think-max").click();
  await expect(think(page)).toHaveAttribute("data-think", "max");
  await expect(think(page).getByTestId("think-max")).toHaveAttribute("aria-checked", "true");
  await expect(think(page).getByTestId("think-max-note")).toContainText("much more of your plan's usage");
  await expect.poll(async () => (await fake.calls()).filter((c) => c.method === "PUT" && c.path === `/projects/${BUILD}/effort`).map((c) => c.body))
    .toEqual([{ default: "max" }]);
  await box(page).fill("for the egg drop challenge make something really interesting");
  await box(page).press("Enter");
  await expect.poll(async () => (await fake.calls()).filter((c) => c.method === "POST" && c.path === `/projects/${BUILD}/chat`).map((c) => c.body))
    .toEqual([{ text: "for the egg drop challenge make something really interesting", effort: "max" }]);
  await expect(think(page)).toHaveAttribute("data-think", "max");           // it stays
  const chip = con(page).getByTestId("effort-chip").last();
  await expect(chip).toHaveText("Thinking: Max · your pick");                // the level the turn ran at
  await shots(page, "max");
  await page.reload();
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await expect(think(page)).toHaveAttribute("data-think", "max");           // persisted per build
  await think(page).getByTestId("think-medium").click();
  await expect(think(page)).toHaveAttribute("data-think", "medium");
  await expect(think(page).getByTestId("think-max-note")).toHaveCount(0);
});
