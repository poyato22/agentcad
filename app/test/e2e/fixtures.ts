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
  };
}

export const test = base.extend<{ fake: FakeControl }>({
  // eslint-disable-next-line no-empty-pattern
  fake: async ({}, use) => { await use(control()); },
});

export { expect };
