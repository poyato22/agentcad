// Plan 9 U-B against the fake core: the agent dock's three streams are visible at once and never
// wait on each other — a study runs for 5 s in Simulations while text streams into the
// Conversation and a view lands in View — and the composer's send button is Stop while the lead
// works. Steps and cards are the real core's (core/tests/fixtures/ui: the base run from
// dump_ui_fixtures.py, an agent's sim_run from dump_study_fixtures.py, G1's presented view).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";
import type { Step } from "../../src/api/types";
import { expect, test, type FakeControl } from "./fixtures";

const FX = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui");
const read = (n: string): unknown => JSON.parse(readFileSync(resolve(FX, n), "utf8"));
const BASE = JSON.parse(readFileSync(resolve(import.meta.dirname, "../fake-core/scenarios/base.json"), "utf8")) as { runs: Record<string, unknown>[] } & Record<string, unknown>;
const G1 = JSON.parse(readFileSync(resolve(import.meta.dirname, "../fixtures/core-ui/g1_cards.json"), "utf8")) as Record<string, Record<string, unknown>>;
const STUDY = read("study_agent_steps.json") as Step[];
const BUILD = (read("projects.json") as { id: string }[])[0].id;
const LEAD = String(BASE.runs[0].id);
// the agent's first study: its tool_call, then (5 s later) its real result
const CALL = STUDY.find((s) => s.kind === "tool_call" && s.payload.name === "sim_run") as Step;
const RESULT = STUDY.find((s) => s.kind !== "tool_call" && s.payload.call_id === CALL.payload.call_id) as Step;
const SIM_ID = String(((RESULT.payload.output as Record<string, unknown>).data as { card: { sim_id: string } }).card.sim_id);
/** G1's real presented view, pointed at the fake build's own part (its lid / body aren't here). */
const VIEW = (() => {
  const v = structuredClone(G1.view_presented) as { ref: { view_spec: Record<string, unknown> & { target: Record<string, unknown> } } };
  Object.assign(v.ref.view_spec, { parts: ["mount_plate"], ghost: [], caption: "the mount plate, from above" });
  Object.assign(v.ref.view_spec.target, { parts: ["mount_plate"], ghost: [] });
  return v as unknown as Record<string, unknown>;
})();
const stepOf =(s: Step): Partial<Step> => ({ kind: s.kind, payload: s.payload, ts: Date.now() / 1000 });

const scenario = () => ({ ...BASE, runs: [{ ...BASE.runs[0], role: "lead", project_id: BUILD, status: "running" }] });
const con = (page: Page) => page.locator("[data-region=console]");
const stream = (page: Page, s: "view" | "sim" | "text") => con(page).locator(`[data-dock] [data-stream=${s}]`);

async function open(page: Page, theme: "dark" | "light") {
  await page.goto("/");
  await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await expect(stream(page, "text").getByRole("log", { name: "Conversation" })).toContainText("thicken the mount plate to 6 mm");
}

const activity = (fake: FakeControl, state: string, detail = "") =>
  fake.emit({ type: "agent_activity", run_id: LEAD, data: { state, detail, since: Date.now() / 1000 } });

for (const theme of ["dark", "light"] as const) {
  test(`three_streams_at_once_a_5s_study_never_holds_up_text_or_views_${theme}`, async ({ page, fake }) => {
    await fake.reset(scenario() as never);
    await page.setViewportSize({ width: 1440, height: 900 });
    await open(page, theme);
    await activity(fake, "tool", "running a study");
    // the lead starts a study (in flight: no result yet)
    const t0 = Date.now();
    await fake.emit({ type: "tool_call", run_id: LEAD, data: { call_id: CALL.payload.call_id, name: "sim_run", input: CALL.payload.input } }, { steps: [stepOf(CALL)] });
    const sim = stream(page, "sim");
    await expect(sim.locator("[data-study-slot]")).toHaveCount(1);
    await expect(sim.locator("[data-running]")).toHaveAttribute("data-running", "1");
    // while it runs: progress in Simulations, words in the Conversation, a view in View
    const words = ["The beam ", "study is running; ", "meanwhile here is ", "the lid from above."];
    for (const [i, w] of words.entries()) {
      await fake.emit({ type: "sim_progress", run_id: LEAD, data: { sim_id: SIM_ID, part_id: "beam", type: "static", stage: "running", phase: i < 2 ? "mesh" : "solve", pct: 20 + i * 20, call_id: CALL.payload.call_id } });
      await fake.emit({ type: "text_delta", run_id: LEAD, data: { text: w } });
      if (i === 1) await fake.emit({ type: "agent_announce", run_id: LEAD, data: VIEW });
    }
    const conv = stream(page, "text").getByRole("log", { name: "Conversation" });
    await expect(conv.locator("[data-live-text]")).toContainText("meanwhile here is the lid from above.");
    const view = stream(page, "view");
    await expect(view.getByTestId("view-card")).toBeVisible();
    await expect(view.getByTestId("view-card")).toContainText("the mount plate, from above");
    await expect(view.getByTestId("live-view")).toHaveAttribute("data-state", "ready", { timeout: 10_000 });
    await expect(sim.getByTestId("phase-bar")).toHaveAttribute("data-phase", "solve");
    // all three visible at once, side by side in one dock, and the study is still running
    for (const s of ["view", "sim", "text"] as const) {
      const box = await stream(page, s).boundingBox();
      expect(box && box.height > 60, s).toBe(true);
      await expect(stream(page, s).locator(".dock-t")).toHaveAttribute("aria-expanded", "true");
    }
    expect(Date.now() - t0).toBeLessThan(5000);
    await expect(sim.locator("[data-running]")).toHaveAttribute("data-running", "1");
    // D13: the view uses the dock's full width
    const card = await view.getByTestId("view-card").boundingBox();
    // (P10 E6: the full width of its column)
    expect(card!.width).toBeGreaterThanOrEqual((await view.boundingBox())!.width - 30);
    // the composer: Stop while the lead works
    await expect(con(page).getByRole("button", { name: "Stop" })).toBeVisible();
    await page.screenshot({ path: `test-results/ub-dock-${theme}.png` });
    // 5 s in, the study finishes: its card lands, the running count clears
    await page.waitForTimeout(Math.max(0, 5000 - (Date.now() - t0)));
    await fake.emit({ type: "tool_result", run_id: LEAD, data: { call_id: CALL.payload.call_id, name: "sim_run", ok: true } }, { steps: [stepOf(RESULT)] });
    await fake.emit({ type: "sim_finished", run_id: LEAD, data: { sim_id: SIM_ID, run_id: LEAD, build_id: BUILD, status: "done", summary: "max von Mises 6.0 MPa" } });
    await expect(sim.locator("[data-running]")).toHaveCount(0);
    await expect(sim.locator("[data-study-slot]")).toHaveCount(1);
    // the words and the view were never waiting on it
    await expect(conv.locator("[data-live-text]")).toContainText("the lid from above.");
    await expect(view.getByTestId("view-card")).toBeVisible();
  });
}

test("composer_send_becomes_stop_while_the_lead_works_and_stops_it", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await open(page, "dark");
  const box = con(page).getByRole("textbox", { name: "Command line" });
  await activity(fake, "thinking");
  const stop = con(page).getByRole("button", { name: "Stop" });
  await expect(stop).toBeVisible();
  await expect(con(page).getByRole("button", { name: "Send", exact: true })).toHaveCount(0);
  await stop.click();
  await expect.poll(async () => (await fake.calls()).some((c) => c.method === "POST" && c.path === `/runs/${LEAD}/cancel`)).toBe(true);
  // the run ends: the button is Send again
  await expect(con(page).getByRole("button", { name: "Send", exact: true })).toBeVisible();
  await expect(con(page).getByRole("button", { name: "Stop" })).toHaveCount(0);
  await box.fill("make the lid thinner");
  await con(page).getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(async () => (await fake.calls()).some((c) => c.method === "POST" && c.path === `/projects/${BUILD}/chat` && (c.body as { text?: string }).text === "make the lid thinner")).toBe(true);
});

test("esc_in_the_composer_stops_a_busy_lead", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await open(page, "dark");
  await activity(fake, "tool", "measuring");
  await expect(con(page).getByRole("button", { name: "Stop" })).toBeVisible();
  await con(page).getByRole("textbox", { name: "Command line" }).press("Escape");
  await expect.poll(async () => (await fake.calls()).some((c) => c.method === "POST" && c.path === `/runs/${LEAD}/cancel`)).toBe(true);
});

test("collapse_a_stream_count_what_arrives_and_resize_with_the_divider_remembered", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await open(page, "dark");
  await fake.emit({ type: "agent_announce", run_id: LEAD, data: G1.view_presented });
  await expect(stream(page, "view").getByTestId("view-card")).toBeVisible();
  // P10 E6: drag the divider between View and Simulations right 60 px: View widens
  const div = con(page).locator('[data-divider="view|sim"]');
  const before = (await stream(page, "view").boundingBox())!.width;
  const d = (await div.boundingBox())!;
  await page.mouse.move(d.x + d.width / 2, d.y + d.height / 2);
  await page.mouse.down();
  await page.mouse.move(d.x + d.width / 2 + 60, d.y + d.height / 2, { steps: 4 });
  await page.mouse.up();
  await expect.poll(async () => (await stream(page, "view").boundingBox())!.width).toBeGreaterThan(before + 50);
  // close Simulations to its slim tab; a study lands meanwhile: an unread count
  await stream(page, "sim").locator(".dock-t").click();
  await expect(stream(page, "sim")).toHaveAttribute("aria-expanded", "false");
  await fake.emit({ type: "tool_call", run_id: LEAD, data: { call_id: CALL.payload.call_id, name: "sim_run", input: CALL.payload.input } }, { steps: [stepOf(CALL)] });
  await expect(stream(page, "sim").locator("[data-unread]")).toHaveText("1");
  // remembered across a reload
  const w = (await stream(page, "view").boundingBox())!.width;
  await page.reload();
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
  await expect(stream(page, "sim")).toHaveAttribute("aria-expanded", "false");
  await expect.poll(async () => Math.round((await stream(page, "view").boundingBox())!.width)).toBe(Math.round(w));
});

test("a_sub_agent_s_study_and_view_appear_in_the_build_s_dock_while_the_lead_s_thread_is_shown", async ({ page, fake }) => {
  // fix round 1 (I1): View and Simulations aggregate the build's agent tree
  await fake.reset(scenario() as never);
  await open(page, "dark");
  const SUB = "sub0aa01";
  const base = BASE.runs[0];
  // the lead spawns a sub-agent (as the core does: run_started, then the lead's announcement)
  await fake.emit({ type: "run_started", run_id: SUB, data: {} },
    { run: { ...base, id: SUB, parent_id: LEAD, role: "sub", agent: "simulation", task: "check the beam", title: "Check the beam", project_id: BUILD, status: "running" } as never });
  await fake.emit({ type: "agent_announce", run_id: LEAD, data: { run_id: LEAD, kind: "agent_spawned", title: "Spawned simulation: Check the beam", detail: "", ref: { agent: "simulation", parent_id: LEAD, run_id: SUB } } });
  await fake.emit({ type: "tool_call", run_id: SUB, data: { call_id: CALL.payload.call_id, name: "sim_run", input: CALL.payload.input } }, { steps: [stepOf(CALL)] });
  await fake.emit({ type: "agent_announce", run_id: SUB, data: { ...VIEW, run_id: SUB } });
  // still the lead's thread
  await expect(page.locator("[data-conversation-head] [data-run-label]")).toHaveCount(0);
  await expect(stream(page, "text").getByRole("log", { name: "Conversation" })).toContainText("thicken the mount plate to 6 mm");
  const sim = stream(page, "sim");
  await expect(sim.locator(`[data-run="${SUB}"] [data-study-slot]`)).toHaveCount(1);
  await expect(sim.locator(`[data-run="${SUB}"] [data-run-tag]`)).toContainText("simulation");
  const view = stream(page, "view");
  await expect(view.getByTestId("view-card")).toContainText("the mount plate, from above");
  await expect(view.locator(`[data-view-main] [data-run-tag="${SUB}"]`)).toContainText("simulation");
  // the view the core mirrors onto the lead shows once
  await expect(view.locator("[data-filmstrip]")).toHaveCount(0);
});

test("esc_never_cancels_a_run_waiting_on_your_approval_the_card_decides", async ({ page, fake }) => {
  await fake.reset(scenario() as never);
  await open(page, "dark");
  await activity(fake, "waiting_approval", "waiting for approval: fixture_merge");
  await expect(con(page).getByRole("button", { name: "Stop" })).toBeVisible();
  await con(page).getByRole("textbox", { name: "Command line" }).press("Escape");
  await page.waitForTimeout(300);
  expect((await fake.calls()).some((c) => c.method === "POST" && c.path === `/runs/${LEAD}/cancel`)).toBe(false);
});

// Plan 9 G-B: the Conversation header's "Design focus" chip shows the class the core gave the
// build and sets the user's own through PATCH /projects/{id} {design_class}
test("design_focus_chip_shows_the_class_and_sets_the_users_own", async ({ page, fake }) => {
  const dc = { score: 0.82, cls: "design", per_part: {}, reasons: ["humanoid archetype"], source: "prior", archetype: "humanoid" };
  await fake.reset({ ...BASE, runs: [{ ...BASE.runs[0], role: "lead", project_id: BUILD, status: "running", design_class: dc }] } as never);
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page, "dark");
  const chip = con(page).locator("[data-conversation-head] [data-design-focus]");
  await expect(chip).toHaveAttribute("data-design-focus", "design");
  await expect(chip).toHaveAttribute("aria-label", "Design focus");   // P10 E23: compact, "Focus · Eng | Mixed | Design"
  await expect(chip.locator("[data-focus=design]")).toHaveAttribute("aria-pressed", "true");
  await chip.locator("[data-focus=engineering]").click();
  await expect(chip).toHaveAttribute("data-design-focus", "engineering");
  await expect(chip.locator("[data-focus-by-user]")).toBeVisible();
  expect((await fake.calls()).some((c) => c.method === "PATCH" && c.path === `/projects/${BUILD}`)).toBe(true);
  // clicking the user's own class hands it back: the classifier's answer shows, no "you"
  await chip.locator("[data-focus=engineering]").click();
  await expect(chip.locator("[data-focus-by-user]")).toHaveCount(0);
  await expect(chip).toHaveAttribute("data-design-focus", "engineering");
  expect((await fake.calls()).filter((c) => c.method === "PATCH" && c.path === `/projects/${BUILD}`).length).toBe(2);
});
