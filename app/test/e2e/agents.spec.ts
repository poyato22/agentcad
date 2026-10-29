// Plan 5 Task A2 against the fake core ("agent-ux" scenario): the agent manager by keyboard (⌘6)
// and from the activity pill, the orchestrator → worker tree, a delete refused in plain words and
// then done with Cancel and delete, and the live activity indicators in the thread.
import type { Page } from "@playwright/test";
import type { AgentRun } from "../../src/api/types";
import { expect, test } from "./fixtures";

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
const row = (page: Page, id: string) => manager(page).locator(`[data-run-row="${id}"]`);

test.beforeEach(async ({ page, fake }) => {
  await fake.reset("agent-ux");
  await page.setViewportSize({ width: 1440, height: 900 });
});

test("cmd6_opens_the_manager_with_the_orchestrator_worker_tree_and_esc_closes", async ({ page, fake }) => {
  await open(page);
  await fake.emit({ type: "run_finished", run_id: "orch01", data: { status: "done" } },
    { run: run("orch01", { agent: "orchestrator", task: "design drone frame" }) });
  await fake.emit({ type: "run_finished", run_id: "work01", data: { status: "done" } },
    { run: run("work01", { parent_id: "orch01", agent: "verifier", task: "check arm stiffness" }) });
  await page.keyboard.press("Meta+6");
  await expect(manager(page)).toBeVisible();
  await expect(row(page, "orch01")).toHaveAttribute("aria-level", "1");
  await expect(row(page, "work01")).toHaveAttribute("aria-level", "2");
  await expect(row(page, "work01")).toContainText("verifier · fake-model");
  await expect(row(page, "orch01")).toContainText("$0.03 · 1.5k tokens");
  // tabs by keyboard
  await manager(page).getByRole("tab", { name: "Runs" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(manager(page).getByRole("tab", { name: "Agents" })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Escape");
  await expect(manager(page)).toHaveCount(0);
});

test("a_working_run_is_refused_in_plain_words_then_cancel_and_delete_removes_it", async ({ page, fake }) => {
  await open(page);
  await page.getByRole("button", { name: "Manage", exact: true }).click();
  const r = row(page, DES);
  await r.getByRole("button", { name: "Delete", exact: true }).click();
  await r.getByRole("button", { name: "Confirm delete" }).click();
  await expect(r.locator("[data-refusal=running]")).toContainText("This run is still working. Stop it first, or use Cancel and delete.");
  await r.getByRole("button", { name: "Cancel and delete" }).click();
  await r.getByRole("button", { name: "Stop it and delete?" }).click();
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
