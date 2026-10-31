// Plan 9 B3 fix round 2 against the fake core: an agent's study keeps running in the background
// after its turn has ended. Its card in the dock's Simulations stream has its own Stop (POST
// /jobs/{id}/cancel), and the composer's send button is Stop while the build has a live job, even
// with the lead idle. Steps and the job are the real core's (core/tests/fixtures/ui/p9 from
// scripts/dump_streams_fixtures.py: a lead's sim_run that returned at once, GET /jobs meanwhile).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";
import type { BackgroundJob } from "../../src/api/jobs";
import type { Step } from "../../src/api/types";
import { expect, test } from "./fixtures";

const FX = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui");
const read = (n: string): unknown => JSON.parse(readFileSync(resolve(FX, n), "utf8"));
const BASE = JSON.parse(readFileSync(resolve(import.meta.dirname, "../fake-core/scenarios/base.json"), "utf8")) as { runs: Record<string, unknown>[]; steps: Step[] } & Record<string, unknown>;
const BUILD = (read("projects.json") as { id: string }[])[0].id;
const LEAD = String(BASE.runs[0].id);
const P9 = (read("p9/streams_steps.json") as Step[]);
const [JOB] = read("p9/streams_jobs.json") as BackgroundJob[];
const firstId = Math.max(...BASE.steps.map((s) => s.id)) + 1;
// the real lead's steps, on the fake build's lead: its words, the sim_run that returned running,
// the render (stops before the note: the study has not finished yet)
const noteAt = P9.findIndex((s) => s.payload.system_note);
const STEPS = P9.slice(0, noteAt < 0 ? P9.length : noteAt).map((s, i) => ({ ...s, id: firstId + i, run_id: LEAD }));
const SIM_ID = String(((STEPS.find((s) => s.kind === "tool_result" && s.payload.name === "sim_run")?.payload.output as { data: { sim_id: string } }).data.sim_id));
const LIVE: BackgroundJob = { ...JOB, job_id: SIM_ID, cancel_url: `/jobs/${SIM_ID}/cancel`, run_id: LEAD, build_id: BUILD };

const scenario = () => ({ ...BASE, steps: [...BASE.steps, ...STEPS], jobs: [LIVE],
  runs: [{ ...BASE.runs[0], role: "lead", project_id: BUILD, status: "done" }] });
const con = (page: Page) => page.locator("[data-region=console]");
const sim = (page: Page) => con(page).locator("[data-dock] [data-stream=sim]");

async function open(page: Page) {
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await expect(sim(page).locator(`[data-job-stop="${SIM_ID}"]`)).toBeVisible();
}

test("a_running_background_study_has_its_own_stop_in_the_simulations_stream", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page);
  const stop = sim(page).locator(`[data-job-stop="${SIM_ID}"]`);
  await expect(stop).toHaveText("Stop");
  await stop.click();
  await expect.poll(async () => (await fake.calls()).some((c) => c.method === "POST" && c.path === `/jobs/${SIM_ID}/cancel`)).toBe(true);
  // the job ends (sim_finished): its Stop goes; the lead itself was never cancelled
  await expect(sim(page).locator("[data-job-stop]")).toHaveCount(0);
  expect((await fake.calls()).some((c) => c.path === `/runs/${LEAD}/cancel`)).toBe(false);
  await expect(con(page).getByTestId("composer-send")).toBeVisible();
});

test("an_idle_lead_with_a_live_job_makes_the_composer_stop_and_esc_stops_the_job", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page);
  await expect(con(page).getByTestId("composer-stop")).toBeVisible();
  await con(page).getByRole("textbox", { name: "Command line" }).press("Escape");
  await expect.poll(async () => (await fake.calls()).some((c) => c.method === "POST" && c.path === `/jobs/${SIM_ID}/cancel`)).toBe(true);
  await expect(con(page).getByTestId("composer-send")).toBeVisible();
  await expect(sim(page).locator("[data-job-stop]")).toHaveCount(0);
});

test("stop_all_from_the_top_bar_stops_an_idle_leads_background_study", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page);
  await page.keyboard.press("Meta+Shift+Period");
  await expect.poll(async () => (await fake.calls()).some((c) => c.method === "POST" && c.path === "/runs/cancel_all")).toBe(true);
  await expect(sim(page).locator("[data-job-stop]")).toHaveCount(0);
});
