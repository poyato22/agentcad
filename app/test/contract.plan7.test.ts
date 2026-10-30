// @vitest-environment node
// Plan 7 (U3a ↔ U3c): the real core's routing, lead queue, agents view and Plan 7 events
// (core scripts/dump_agents_fixtures.py → core/tests/fixtures/ui/plan7) parse with the app's
// schemas and parsers, and the fake core answers in the same shapes.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { agentRunSchema, agentsViewSchema, coreErrorBodySchema, createRunResultSchema, stepSchema } from "../src/api/schemas";
import type { AgentRun, MisesEvent, Step } from "../src/api/types";
import { parseAnimateSpec } from "../src/state/assemblyAnimation";
import { announcementOf, deliveredOf, queuedOf } from "../src/ui/console/announceModel";
import { startFakeCore, type FakeCore } from "./fake-core/server";
import { shapeDiff, type Json } from "./shape";

const DIR = resolve(import.meta.dirname, "../../core/tests/fixtures/ui/plan7");
const read = (n: string): Json => JSON.parse(readFileSync(resolve(DIR, n), "utf8")) as Json;
const lines = (n: string): MisesEvent[] => readFileSync(resolve(DIR, n), "utf8").trim().split("\n").map((l) => JSON.parse(l) as MisesEvent);

let core: FakeCore;
const fake = new Map<string, Json>();

async function call(method: string, path: string, body?: unknown): Promise<{ status: number; json: Json }> {
  const headers: Record<string, string> = { Authorization: `Bearer ${core.token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(core.url + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  return { status: r.status, json: (await r.json()) as Json };
}

beforeAll(async () => {
  core = await startFakeCore({ port: 0, token: "plan7", scenario: "base" });
  core.state.runs.clear();
  const pid = core.state.builds.builds[0].id;
  fake.set("project_agents_empty.json", (await call("GET", `/projects/${pid}/agents`)).json);
  const first = (await call("POST", "/runs", { prompt: "Build me a drone", project_id: pid })).json as { run_id: string };
  fake.set("run_routed_new.json", first);
  const lead = first.run_id;
  // the lead spawns a titled sub-agent, then ends its turn
  const t = Date.now() / 1000;
  const sub: AgentRun = { id: "sub_fixture", parent_id: lead, agent: "designer", provider: "fake", model: "m", task: "Make a 30x30x4 mm motor mount plate.",
    status: "done", branch: null, onshape_calls: 0, input_tokens: 0, output_tokens: 0, usd: 0, created_at: t, updated_at: t, owner_pid: null,
    external: false, session_ref: null, project_id: pid, role: "sub", title: "Design motor mount" };
  core.emit({ type: "agent_announce", run_id: lead, data: { run_id: lead, kind: "agent_spawned", title: "Spawned designer: Design motor mount", detail: "", ref: { agent: "designer", parent_id: lead, run_id: sub.id } } }, { run: sub });
  core.emit({ type: "run_finished", run_id: lead, data: { status: "done" } });
  await call("POST", "/runs", { prompt: "check", agent: "verifier", project_id: pid });
  fake.set("run_routed_followup.json", (await call("POST", "/runs", { prompt: "now test the frame for stress", project_id: pid })).json);
  // the lead is working: the next prompt queues; a full queue answers 429
  fake.set("run_routed_queued.json", (await call("POST", "/runs", { prompt: "and paint it red", project_id: pid })).json);
  for (let i = 0; i < 19; i++) await call("POST", "/runs", { prompt: `more ${i}`, project_id: pid });
  const full = await call("POST", "/runs", { prompt: "one more", project_id: pid });
  fake.set("queue_full.json", { status: full.status, body: full.json });
  fake.set("project_agents.json", (await call("GET", `/projects/${pid}/agents`)).json);
  fake.set("run_lead.json", (await call("GET", `/runs/${lead}`)).json);
  core.emit({ type: "run_finished", run_id: lead, data: { status: "done" } });       // delivers the first queued message
  await new Promise((r) => setTimeout(r, 30));
  fake.set("lead_steps.json", (await call("GET", `/runs/${lead}/steps?after_id=0`)).json);
});
afterAll(async () => { await core.stop(); });

const CASES: [string, { safeParse(v: unknown): { success: boolean } }][] = [
  ["run_routed_new.json", createRunResultSchema], ["run_routed_followup.json", createRunResultSchema], ["run_routed_queued.json", createRunResultSchema],
  ["project_agents.json", agentsViewSchema], ["project_agents_empty.json", agentsViewSchema], ["run_lead.json", agentRunSchema],
];

describe("contract (Plan 7): real U3c answers ↔ app schemas ↔ fake core", () => {
  for (const [name, schema] of CASES) {
    it(`${name}: the app parses the real answer, and the fake core answers the same shape`, () => {
      expect(schema.safeParse(read(name)).success).toBe(true);
      expect(schema.safeParse(fake.get(name)).success).toBe(true);
      expect(shapeDiff(read(name), fake.get(name), name)).toEqual([]);
    });
  }

  it("a full lead queue is a 429 with the core's error body", () => {
    const real = read("queue_full.json") as { status: number; body: unknown };
    const f = fake.get("queue_full.json") as { status: number; body: unknown };
    expect(real.status).toBe(429);
    expect(f.status).toBe(429);
    expect(coreErrorBodySchema.safeParse(real.body).success).toBe(true);
    expect(shapeDiff(real.body as Json, f.body as Json, "queue_full")).toEqual([]);
  });

  it("the lead's stored steps: announcement, queued message and its delivery, in the core's shapes", () => {
    const real = (read("lead_steps.json") as unknown[]).map((s) => stepSchema.parse(s)) as Step[];
    const ours = (fake.get("lead_steps.json") as unknown[]).map((s) => stepSchema.parse(s)) as Step[];
    for (const key of ["agent_announce", "queued_message", "queued_message_delivered"]) {
      const r = real.find((s) => key in s.payload);
      const f = ours.find((s) => key in s.payload);
      expect(r, `real ${key}`).toBeTruthy();
      expect(f, `fake ${key}`).toBeTruthy();
      expect(shapeDiff(r?.payload as Json, f?.payload as Json, key)).toEqual([]);
    }
    expect(announcementOf(real.find((s) => "agent_announce" in s.payload) as Step)?.kind).toBe("agent_spawned");
    const q = queuedOf(real.find((s) => "queued_message" in s.payload) as Step);
    expect(q?.text).toBe("and paint it red");
    expect(deliveredOf(real.find((s) => "queued_message_delivered" in s.payload) as Step)).toBe(q?.messageId);
  });

  it("message_queued / message_delivered events: the fake core sends the core's fields", () => {
    const real = lines("agent_events.jsonl");
    for (const type of ["message_queued", "message_delivered"]) {
      const r = real.find((e) => e.type === type);
      expect(r, type).toBeTruthy();
      const keys = Object.keys(r?.data ?? {}).sort();
      expect(keys, type).toEqual(type === "message_queued" ? ["message_id", "position", "run_id", "text"] : ["message_id", "run_id"]);
    }
  });

  it("assembly_animate: the real single-joint and sequence events parse into playable steps", () => {
    const [single, seq] = lines("animate_events.jsonl");
    expect(parseAnimateSpec(single)?.steps).toHaveLength(1);
    expect(parseAnimateSpec(single)?.loops).toBeGreaterThan(1);
    expect(parseAnimateSpec(seq)?.steps).toHaveLength(2);
  });
});
