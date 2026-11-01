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

/** Open the full log or the agent browser: Profile & settings → Developer (Plan 10 E22). The dock
 *  has no ⋯ menu any more (P10 E24 took New run, E22 the rest). */
export async function consoleMenu(page: import("@playwright/test").Page, item: "Agent browser" | "Full log"): Promise<void> {
  await developerView(page, item);
}

/** Plan 10 E22: open the dock's full log or agent browser from Profile & settings → Developer. */
export async function developerView(page: import("@playwright/test").Page, item: "Agent browser" | "Full log"): Promise<void> {
  await page.locator("[data-region=topbar]").getByRole("button", { name: "Profile", exact: true }).click();
  const profile = page.getByRole("dialog", { name: "Profile" });
  await profile.getByRole("navigation", { name: "Profile sections" }).getByRole("button", { name: "Developer" }).click();
  await profile.getByRole("button", { name: item === "Full log" ? "Show full agent log" : "Show agent browser" }).click();
}

/** Plan 10 E2: pick an item of Home's Settings menu (Parts library, Onshape and Trash toggle their
 *  view; Profile & settings opens the Profile panel). */
export async function homeMenu(page: import("@playwright/test").Page, item: "Parts library" | "Onshape" | "Trash" | "Profile & settings"): Promise<void> {
  await page.locator('[data-testid="home"] .home-bar').getByRole("button", { name: "Settings", exact: true }).click();
  const menu = page.getByRole("menu", { name: "Settings" });
  if (item === "Profile & settings") await menu.getByRole("menuitem", { name: item }).click();
  else await menu.getByRole("menuitemcheckbox", { name: item === "Trash" ? /^Trash/ : item }).click();
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

/** Plan 9 D7: the Design page shows the build's assembly; a part opens alone from the left tree.
 *  Opens `partId` (default: the first row) in the part viewer, as a click on its row does. */
export async function openPartView(page: import("@playwright/test").Page, partId?: string): Promise<void> {
  await showParts(page);
  const row = partId ? page.locator(`[data-part="${partId}"] .tree-name`) : page.locator("[data-part] .tree-name").first();
  await row.click();
  await page.getByTestId("viewer-canvas").waitFor();
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

/** P10 E6: the dock opens the Conversation alone by default. The specs about the View and
 *  Simulations streams (written for three streams at once) see all three columns unless they
 *  stored a layout of their own; the rest keep the default, so the model keeps its room for
 *  viewport clicks. `useDefaultDock` (before page.goto) undoes the seed. */
const ALL_COLUMNS = new Set(["agent-browser.spec.ts", "concept-sketch.spec.ts", "dock.spec.ts", "jobs.spec.ts", "generative.spec.ts", "geometry.spec.ts",
  "motion.spec.ts", "studies-agent.spec.ts", "studies.spec.ts"]);
/** G1: the top area shows one of View / Simulations: the specs about studies open on Simulations. */
const SIM_FIRST = new Set(["jobs.spec.ts", "generative.spec.ts", "studies-agent.spec.ts", "studies.spec.ts"]);
export async function openAllDockColumns(page: import("@playwright/test").Page, active: "view" | "sim" = "view"): Promise<void> {
  await page.addInitScript((a) => {
    try {
      if (!localStorage.getItem("mises.dock.stack")) localStorage.setItem("mises.dock.stack", JSON.stringify({ active: a, collapsed: { top: false, text: false } }));
    } catch { /* none */ }
  }, active);
}
/** P10 FWA: with three dock columns open the Parts surface folds to its tab by itself so the
 *  model keeps 520 px (ui/shell/room); a spec that works the part tree opens it, as a user would. */
export async function showParts(page: import("@playwright/test").Page): Promise<void> {
  await expect(page.locator("[data-region=app]")).toHaveAttribute("data-conn", "open");   // the room has settled
  await page.locator("[data-region=left]").first().waitFor();
  const tab = page.getByRole("button", { name: "Show the parts" });
  if (await tab.isVisible()) await tab.click();
  await expect(page.locator(".app")).toHaveAttribute("data-left", "open");
}
export async function useDefaultDock(page: import("@playwright/test").Page): Promise<void> {
  await page.addInitScript(() => {
    try { if (!sessionStorage.getItem("dock-default")) { sessionStorage.setItem("dock-default", "1"); localStorage.removeItem("mises.dock.stack"); } } catch { /* none */ }
  });
}

export const test = base.extend<{ fake: FakeControl }>({
  // Existing specs drive the build shell; land there by default so launch-on-Home (R1) doesn't
  // hide the panes they assert. A Home spec opts back in with openHome().
  page: async ({ page }, use, info) => {
    await openBuildShell(page);
    const spec = info.file.split("/").pop() ?? "";
    if (ALL_COLUMNS.has(spec)) await openAllDockColumns(page, SIM_FIRST.has(spec) ? "sim" : "view");
    await use(page);
  },
  // eslint-disable-next-line no-empty-pattern
  fake: async ({}, use) => { await use(control()); },
});

export { expect };
