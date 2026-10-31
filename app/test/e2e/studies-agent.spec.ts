// Plan 9 U-D (D19): the Studies page opens on the studies agent. Against the fake core, whose
// GET /studies/context serves the real core's context (core/scripts/dump_studies_context_fixtures.py,
// the proposals moved onto this scenario's parts) and whose POST /sims plays the real study
// events: proposals with why and Run; Run starts the study and its card appears; the manual setup
// still works behind "Set up manually"; the composer starts the build's `simulation` agent.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const FX = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui");
const read = (n: string): unknown => JSON.parse(readFileSync(resolve(FX, n), "utf8"));
const BASE = JSON.parse(readFileSync(resolve(import.meta.dirname, "../fake-core/scenarios/base.json"), "utf8")) as Record<string, unknown> & { agents: unknown[] };
const GLB = { beam: resolve(FX, "study_part_beam.glb"), cube: resolve(FX, "study_part_cube.glb") };
const SIMULATION = (read("agents.json") as { name: string }[]).some((a) => a.name === "simulation")
  ? (read("agents.json") as { name: string }[]).find((a) => a.name === "simulation")
  : { name: "simulation", description: "The Studies page's agent.", model: "inherit" };
const REPLY = "The beam carries the load; run the stress study first. Guide only.";

const scenario = (extra: Record<string, unknown> = {}) => ({
  ...BASE, agents: [...BASE.agents, SIMULATION], parts: read("study_parts.json"), glb: GLB, runs: [], steps: [], approvals: [],
  sims: { studies: true, step_ms: 60 }, studies_agent: "agent",
  on: { create_run: [{ at_ms: 150, event: { type: "run_finished", run_id: "$run", data: { status: "done" } },
    steps: [{ run_id: "$run", kind: "message", payload: { role: "assistant", text: REPLY, stop_reason: "end_turn" } }] }] },
  ...extra,
});

async function open(page: Page, theme: "dark" | "light" = "dark") {
  await page.goto("/");
  await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await page.getByRole("tab", { name: "Studies" }).click();
  await expect(page.getByTestId("studies")).toBeVisible();
}

for (const theme of ["dark", "light"] as const) {
  test(`studies_opens_on_the_agents_proposals_${theme}`, async ({ page, fake }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await fake.reset(scenario() as never);
    await open(page, theme);
    const pane = page.getByTestId("studies-agent");
    await expect(pane).toBeVisible();
    const props = pane.getByTestId("study-proposal");
    await expect(props).toHaveCount(2);
    await expect(props.first()).toContainText("Stress: beam at 2x body weight");
    await expect(props.first()).toContainText("has no stress study");
    await expect(props.first().getByTestId("run-proposal")).toBeEnabled();
    await expect(pane.getByTestId("proposals-source")).toHaveAttribute("data-source", "agent");
    // the manual form is behind "Set up manually", collapsed
    await expect(page.getByTestId("manual-setup-toggle")).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByTestId("schema-form")).toHaveCount(0);
    // ~40% of the page for the agent, the rest for the result
    const side = (await page.locator(".st-side").boundingBox())!;
    const main = (await page.locator(".st-main").boundingBox())!;
    expect(side.width / (side.width + main.width)).toBeGreaterThan(0.33);
    expect(side.width / (side.width + main.width)).toBeLessThan(0.45);
    await page.screenshot({ path: `test-results/ud-studies-agent-${theme}.png` });
  });
}

test("a_study_run_from_the_studies_page_lands_in_the_docks_simulations_stream (FW-app I3)", async ({ page, fake }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await fake.reset(scenario() as never);
  await open(page);
  // the build's earlier studies (the scenario's real records) are in the stream already
  await page.getByRole("tab", { name: "Design" }).click();
  const sim = page.locator("[data-dock] [data-stream=sim]");
  const cards = sim.locator("[data-record=sim] [data-testid=study-card]");
  await expect(sim.locator("[data-stream-empty]")).toHaveCount(0);
  const before = await cards.count();
  expect(before).toBeGreaterThan(0);
  await page.getByRole("tab", { name: "Studies" }).click();
  const first = page.getByTestId("study-proposal").first();
  const partId = await first.getAttribute("data-part");
  await first.getByTestId("run-proposal").click();
  await expect(page.getByTestId("agent-study-card")).toBeVisible();
  // back on the build: the dock's Simulations stream has it too (its own "studies" run is not in
  // the dock's agent tree) — on top while it runs, newest first once done
  await page.getByRole("tab", { name: "Design" }).click();
  await expect(cards).toHaveCount(before + 1, { timeout: 10_000 });
  const mine = cards.first();
  await expect(mine).toHaveAttribute("aria-label", new RegExp(`study of ${partId}`));
  await expect(mine).toHaveAttribute("data-status", "done", { timeout: 15_000 });
});

test("run_on_a_proposal_starts_its_study_and_its_card_appears", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await open(page);
  const first = page.getByTestId("study-proposal").first();
  const partId = await first.getAttribute("data-part");
  await first.getByTestId("run-proposal").click();
  const card = page.getByTestId("agent-study-card");
  await expect(card).toBeVisible();
  const calls = await fake.calls();
  const post = calls.find((c) => c.method === "POST" && c.path === "/sims");
  expect(post?.body).toMatchObject({ type: "static", part_id: partId });
  expect(((post?.body as { settings: { load_cases: { loads: { newtons: number }[] }[] } }).settings.load_cases[0].loads[0].newtons)).toBe(60);
  // it finishes: verdict, numbers, "guide only"
  await expect(card).toHaveAttribute("data-status", "done", { timeout: 15_000 });
  await expect(card).toContainText("Guide only");
  await expect(page.getByTestId("study-result")).toBeVisible();
  // the card sits under the proposal that started it, and names the proposal's own case
  await expect(first.getByTestId("agent-study-card")).toBeVisible();
  await expect(page.getByTestId("governing-case")).toHaveCount(0);   // one case: no case table
  for (const theme of ["dark", "light"] as const) {
    await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
    await page.screenshot({ path: `test-results/ud-studies-run-${theme}.png` });
  }
  // the past-study count follows the study list
  await expect(page.getByTestId("proposals-source")).not.toContainText(" 0 past studies");
  // Run state survives a reload
  await page.reload();
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await page.getByRole("tab", { name: "Studies" }).click();
  await expect(page.getByTestId("study-proposal").first().getByTestId("run-proposal")).toHaveAttribute("data-state", "ran");
});

test("manual_setup_still_runs_a_study", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await open(page);
  await page.getByTestId("manual-setup-toggle").click();
  await expect(page.getByTestId("manual-setup")).toBeVisible();
  await page.locator("#st-part").selectOption("beam");
  await page.getByTestId("type-modal").click();
  await expect(page.getByTestId("schema-form")).toBeVisible();
  await page.getByTestId("run-study").click();
  await expect(page.getByTestId("study-result")).toHaveAttribute("data-type", "modal", { timeout: 15_000 });
  const post = (await fake.calls()).find((c) => c.method === "POST" && c.path === "/sims");
  expect(post?.body).toMatchObject({ type: "modal", part_id: "beam" });
  // the choice is remembered: the manual setup stays open after a reload
  await page.reload();
  await page.getByRole("tab", { name: "Studies" }).click();
  await expect(page.getByTestId("manual-setup")).toBeVisible();
});

test("the_composer_starts_the_builds_studies_agent_and_shows_its_reply", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await open(page);
  const pane = page.getByTestId("studies-agent");
  await pane.getByTestId("studies-agent-input").fill("Which part should I test first?");
  await pane.getByTestId("studies-agent-input").press("Enter");
  await expect(pane.getByTestId("sa-user")).toHaveText("Which part should I test first?");
  await expect(pane.getByTestId("sa-agent")).toContainText("run the stress study first");
  const post = (await fake.calls()).find((c) => c.method === "POST" && c.path === "/runs");
  expect(post?.body).toMatchObject({ agent: "simulation", prompt: "Which part should I test first?" });
  expect(typeof (post?.body as { project_id?: unknown }).project_id).toBe("string");
});
