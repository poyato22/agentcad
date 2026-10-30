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
  };
}

/** Open the console in its dense "Details" view (roster · filters · step log) instead of the
 *  conversation view, for specs about the step log itself. Call before page.goto. */
export async function useDetailsView(page: import("@playwright/test").Page): Promise<void> {
  await page.addInitScript(() => { try { localStorage.setItem("mises.console.view", "details"); } catch { /* none */ } });
}

/** Launch straight into the build shell (not the Home grid) — the default for shell specs. Home
 *  specs call `openHome` instead. */
export async function openBuildShell(page: import("@playwright/test").Page): Promise<void> {
  await page.addInitScript(() => { try { localStorage.setItem("mises.home.open", "0"); } catch { /* none */ } });
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
