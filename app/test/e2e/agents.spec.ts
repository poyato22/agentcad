// Plan 5 Task A2 against the fake core ("agent-ux" scenario): the agent manager by keyboard (⇧⌘A)
// and from the activity pill, the orchestrator → worker tree, a delete refused in plain words and
// then done with Cancel and delete, and the live activity indicators in the thread.
import type { Page } from "@playwright/test";
import type { AgentRun } from "../../src/api/types";
import { consoleMenu, expect, test } from "./fixtures";
import { CORE_URL } from "./ports";

const DES = "b7c79a57cbb4";       // the scenario's running designer run

function run(id: string, over: Partial<AgentRun>): AgentRun {
  return {
    id, parent_id: null, agent: "designer", provider: "fake", model: "fake-model", task: "check arm stiffness",
    status: "done", branch: null, onshape_calls: 0, input_tokens: 1200, output_tokens: 300, usd: 0.03,
    created_at: 1_790_000_000, updated_at: 1_790_000_000, owner_pid: null, external: false, session_ref: null, ...over,
  };
}

async function open(page: Page) {
  await page.goto("/");
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");
}
const manager = (page: Page) => page.getByRole("dialog", { name: "Agent manager" });
const row = (page: Page, id: string) => manager(page).locator(`[data-agent-row="${id}"]`);

test.beforeEach(async ({ page, fake }) => {
  await fake.reset("agent-ux");
  await page.setViewportSize({ width: 1440, height: 900 });
});

test("cmd_shift_a_opens_the_manager_with_the_orchestrator_worker_tree_and_esc_closes", async ({ page, fake }) => {
  await open(page);
  await fake.emit({ type: "run_finished", run_id: "orch01", data: { status: "done" } },
    { run: run("orch01", { agent: "orchestrator", task: "design drone frame" }) });
  await fake.emit({ type: "run_finished", run_id: "work01", data: { status: "done" } },
    { run: run("work01", { parent_id: "orch01", agent: "verifier", task: "check arm stiffness" }) });
  await page.keyboard.press("Meta+Shift+KeyA");
  await expect(manager(page)).toBeVisible();
  // R10: one view — every build's agents, a worker indented under the run that spawned it
  await expect(manager(page).getByRole("tablist")).toHaveCount(0);
  await expect(row(page, "orch01")).toBeVisible();
  await expect(row(page, "work01")).toBeVisible();
  await expect(row(page, "work01")).toHaveAttribute("style", /--depth:\s*1/);
  await expect(row(page, "work01")).toContainText("verifier");
  await expect(row(page, "orch01")).toContainText("$0.03 · 1.5k tokens");
  // I7: the dense tree and the definitions sit behind Advanced; tabs by keyboard there
  await manager(page).getByRole("button", { name: "Advanced ▸" }).click();
  await manager(page).getByRole("tab", { name: "Agents" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(manager(page).getByRole("tab", { name: "All runs" })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Escape");
  await expect(manager(page)).toHaveCount(0);
});

test("the_build_lead_with_its_titled_sub_agent_and_a_message_queued_while_it_works", async ({ page, fake }) => {
  await open(page);
  // a routed prompt starts the build's lead; it spawns a titled sub-agent and keeps working
  await consoleMenu(page, "New run…");
  const form = page.getByRole("form", { name: "New run" });
  await form.getByLabel("Task").fill("Build me a drone");
  await form.getByRole("button", { name: "Start" }).click();     // waits for the model to prefill
  await expect(form).toHaveCount(0);
  const runs = await (await fetch(`${CORE_URL}/runs?limit=50`, { headers: { Authorization: `Bearer ${fake.token}` } })).json() as AgentRun[];
  const leadRun = runs.find((r) => r.role === "lead") as AgentRun;
  await fake.emit({ type: "agent_announce", run_id: leadRun.id, data: { run_id: leadRun.id, kind: "agent_spawned", title: "Spawned designer: Design motor mount", detail: "", ref: { agent: "designer", parent_id: leadRun.id, run_id: "sub01" } } },
    { run: run("sub01", { parent_id: leadRun.id, role: "sub", title: "Design motor mount", project_id: leadRun.project_id, status: "running" }) });
  // R16: the card shows in the lead's thread
  await expect(page.locator("[data-announce=agent_spawned]")).toContainText("Started an agent");
  // I2: the lead is still working — the composer stays usable and the message queues
  await consoleMenu(page, "New run…");
  await form.getByLabel("Task").fill("paint it red");
  await form.getByRole("button", { name: "Start" }).click();
  const queued = page.locator("[data-kind=queued]", { hasText: "paint it red" });
  await expect(queued.locator("[data-queued-chip]")).toContainText("queued · delivered when the agent finishes this step");
  // the lead finishes its step: the message is delivered, in place
  await fake.emit({ type: "run_finished", run_id: leadRun.id, data: { status: "done" } });
  await expect(queued).toHaveAttribute("data-queued-status", "delivered");
  await expect(page.locator("[data-msg=user]", { hasText: "paint it red" })).toHaveCount(1);
  // R10: the manager shows the lead on top, its sub-agent by task title beneath
  await page.keyboard.press("Meta+Shift+KeyA");
  await expect(row(page, leadRun.id).locator("[data-lead]")).toBeVisible();
  await expect(row(page, "sub01")).toContainText("Design motor mount");
  await expect(row(page, "sub01")).toHaveAttribute("style", /--depth:\s*1/);
});

test("a_working_run_is_refused_in_plain_words_then_cancel_and_delete_removes_it", async ({ page, fake }) => {
  await open(page);
  await page.keyboard.press("Meta+Shift+KeyA");   // the agent manager (no console button bar, Plan 9 D2)
  const r = row(page, DES);
  await r.getByRole("button", { name: /^More for / }).click();
  await r.getByRole("menuitem", { name: "Delete…" }).click();
  await r.locator("[data-delete-confirm]").getByRole("button", { name: /^Delete / }).click();
  await expect(r.locator("[data-refusal=running]")).toContainText("This run is still working. Stop it first, or use Cancel and delete.");
  await r.getByRole("button", { name: "Cancel and delete" }).click();
  await expect(row(page, DES)).toHaveCount(0);
  const calls = await fake.calls();
  expect(calls.some((c) => c.method === "POST" && c.path === `/runs/${DES}/cancel`)).toBe(true);
  expect(calls.filter((c) => c.method === "DELETE" && c.path === `/runs/${DES}`).length).toBeGreaterThanOrEqual(2);
});

test("thinking_shows_in_the_thread_and_the_pill_opens_the_manager", async ({ page, fake }) => {
  await open(page);
  await fake.emit({ type: "agent_activity", run_id: DES, data: { state: "thinking", detail: "", since: Date.now() / 1000 } });
  await fake.emit({ type: "agent_thinking", run_id: DES, data: { text_delta: "The plate is 5 mm; 6 mm adds 0.8 cm³." } });
  const status = page.locator("[data-run-status=thinking]");
  await expect(status).toBeVisible();
  await status.getByRole("button", { name: "Show thinking" }).click();
  await expect(page.locator("[data-thinking]")).toHaveText("The plate is 5 mm; 6 mm adds 0.8 cm³.");
  await fake.emit({ type: "agent_activity", run_id: DES, data: { state: "tool", detail: "measuring mount plate", since: Date.now() / 1000 } });
  await expect(page.locator("[data-run-status=tool] [data-status-text]")).toContainText("measuring mount plate ·");
  await page.getByRole("button", { name: /working.*Open the agent manager/ }).click();
  await expect(manager(page)).toBeVisible();
  await expect(row(page, DES).locator("[data-row-activity]")).toHaveText("working · measuring mount plate");
});

// I2: after a core restart the core can't reach the build's lead; the next message starts a new
// lead. The console follows it (the message stays on screen), the Agents panel shows the new lead
// with the earlier one kept beneath, and a second message never fails with "unknown run".
test("after_a_core_restart_the_console_follows_the_new_lead", async ({ page, fake }) => {
  await open(page);
  const get = async <T,>(path: string) => await (await fetch(`${CORE_URL}${path}`, { headers: { Authorization: `Bearer ${fake.token}` } })).json() as T;
  const list = () => get<AgentRun[]>("/runs?limit=50");
  const tabs = await get<{ active_project_id: string }>("/builds/tabs").catch(() => null);
  const build = tabs?.active_project_id ?? (await get<{ id: string }[]>("/projects"))[0].id;
  // a native lead from the previous core process, finished
  const first = run("lead01", { agent: "engineer", task: "build robot arm", role: "lead", project_id: build, created_at: Date.now() / 1000 });
  await fake.emit({ type: "run_finished", run_id: first.id, data: { status: "done" } }, { run: first });
  await page.locator("[data-region=left]").getByRole("button", { name: /engineer · build robot arm/ }).click();
  await expect(page.getByTestId("target-chip")).toContainText("engineer · build robot arm");
  await fake.restartCore();
  const cmd = page.getByLabel("Command line");
  await cmd.fill("add landing gear");
  await cmd.press("Enter");
  await expect(page.getByTestId("cmd-info")).toContainText("a new lead took over");
  await expect(page.locator("[data-msg=user]", { hasText: "add landing gear" })).toBeVisible();
  const second = (await list()).find((r) => r.role === "lead" && r.id !== first.id) as AgentRun;
  expect(second).toBeTruthy();
  await fake.emit({ type: "run_finished", run_id: second.id, data: { status: "done" } });
  await cmd.fill("and a camera mount");
  await cmd.press("Enter");
  await expect(page.locator("[data-msg=user]", { hasText: "and a camera mount" })).toBeVisible();
  await expect(page.getByRole("alert").filter({ hasText: "unknown run" })).toHaveCount(0);
  await page.keyboard.press("Meta+Shift+KeyA");
  await expect(row(page, second.id).locator("[data-lead]")).toBeVisible();
  await expect(manager(page).getByRole("list", { name: /^Earlier leads of / }).locator(`[data-agent-row="${first.id}"]`)).toBeVisible();
});
