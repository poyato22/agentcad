// Shared Playwright fixtures. Every spec imports { test, expect } from here.
// The fake core is started once by playwright.config.ts (webServer); `fake` talks to its
// /__fake/* control routes. Call `fake.reset("<scenario>")` at the start of each test.
import { test as base, expect } from "@playwright/test";
import type { Approval, MisesEvent, PartSummary, Step, AgentRun } from "../../src/api/types";
import { CORE_TOKEN, CORE_URL } from "./ports";

export interface FakeCall { method: string; path: string; query: Record<string, string>; body: unknown; ts: number; ifNoneMatch?: string }
export interface EmitExtras {
  steps?: Partial<Step>[]; approval?: Approval; run?: AgentRun; parts?: PartSummary[];
}

export interface FakeControl {
  url: string;
  token: string;
  reset(scenario?: string | Record<string, unknown>): Promise<void>;
  emit(event: Omit<MisesEvent, "ts"> & { ts?: number }, extras?: EmitExtras): Promise<void>;
  calls(): Promise<FakeCall[]>;
  dropSockets(mode?: "close" | "terminate"): Promise<void>;
  rotateToken(token: string): Promise<void>;
  /** An agent's edit of a part (fake core builds.ts): version+1, part_changed by "agent". */
  agentEdit(body: { part_id: string; vars?: Record<string, number>; source?: string; run_id?: string; summary?: string }): Promise<void>;
  /** As after a core restart: the build's native leads can't be reached; the next prompt starts a new lead. */
  restartCore(): Promise<void>;
}

function control(): FakeControl {
  let token = CORE_TOKEN;
  const post = async (path: string, body: unknown) => {
    const r = await fetch(CORE_URL + path, {
      method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`${path} → ${r.status} ${await r.text()}`);
  };
  return {
    url: CORE_URL,
    get token() { return token; },
    reset: (scenario = "base") => post("/__fake/reset", { scenario, token: CORE_TOKEN }).then(() => { token = CORE_TOKEN; }),
    emit: (event, extras = {}) => post("/__fake/emit", { event, ...extras }),
    calls: async () => {
      const r = await fetch(CORE_URL + "/__fake/calls", { headers: { Authorization: `Bearer ${token}` } });
      return (await r.json()) as FakeCall[];
    },
    dropSockets: (mode = "close") => post("/__fake/drop_sockets", { mode }),
    rotateToken: (t) => post("/__fake/rotate_token", { token: t }).then(() => { token = t; }),
    agentEdit: (body) => post("/__fake/agent_edit", body),
    restartCore: () => post("/__fake/restart_core", {}),
  };
}

/** Open the console in its dense "Details" view (roster · filters · step log) instead of the
 *  conversation view, for specs about the step log itself. Call before page.goto. */
export async function useDetailsView(page: import("@playwright/test").Page): Promise<void> {
  await page.addInitScript(() => { try { localStorage.setItem("mises.console.view", "details"); } catch { /* none */ } });
}

/** Pick an item of the Conversation header's ⋯ menu ("New run…", "Agent browser", "Full log"):
 *  the agent dock has no button bar (Plan 9 D2). */
export async function consoleMenu(page: import("@playwright/test").Page, item: "New run…" | "Agent browser" | "Full log"): Promise<void> {
  await page.getByRole("button", { name: "Conversation options" }).click();
  await page.getByRole("menu", { name: "Conversation options" }).getByRole("menuitem", { name: item }).click();
}

/** Show a run's thread in the dock, from the agent manager's run tree (the console has no run
 *  dropdown, Plan 9 D2). */
export async function showRun(page: import("@playwright/test").Page, runId: string): Promise<void> {
  await page.keyboard.press("Meta+Shift+KeyA");
  const manager = page.getByRole("dialog", { name: "Agent manager" });
  const adv = manager.getByRole("button", { name: "Advanced ▸" });
  if (await adv.count()) await adv.click();
  await manager.getByRole("tab", { name: "All runs" }).click();
  await manager.locator(`[data-run-row="${runId}"]`).focus();
  await page.keyboard.press("Enter");
}

/** Launch straight into the build shell (not the Home grid) — the default for shell specs. Home
 *  specs call `openHome` instead. */
export async function openBuildShell(page: import("@playwright/test").Page): Promise<void> {
  await page.addInitScript(() => { try { localStorage.setItem("mises.home.open", "0"); } catch { /* none */ } });
}

/** Plan 9 U-D: the Studies page opens on the studies agent with "Set up manually" collapsed;
 *  specs about the manual form open it up front. Call before page.goto. */
export async function openManualStudySetup(page: import("@playwright/test").Page): Promise<void> {
  await page.addInitScript(() => { try { localStorage.setItem("mises.studies.manual", "1"); } catch { /* none */ } });
}

/** Launch into the Home grid explicitly. */
export async function openHome(page: import("@playwright/test").Page): Promise<void> {
  await page.addInitScript(() => { try { localStorage.setItem("mises.home.open", "1"); } catch { /* none */ } });
}

export const test = base.extend<{ fake: FakeControl }>({
  // Existing specs drive the build shell; land there by default so launch-on-Home (R1) doesn't
  // hide the panes they assert. A Home spec opts back in with openHome().
  page: async ({ page }, use) => { await openBuildShell(page); await use(page); },
  // eslint-disable-next-line no-empty-pattern
  fake: async ({}, use) => { await use(control()); },
});

export { expect };
