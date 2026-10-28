// @vitest-environment node
// Contract test: the app's zod schemas, its reducer and the fake core all agree with what the
// real core returns. The real core's answers are the fixtures core/tests/fixtures/ui, written by
// core/scripts/dump_ui_fixtures.py against a real Runtime (MISES_CONTRACT_LIVE=1 regenerates them
// into a temp dir first, so the check runs against the current core code).
//
// For every fixture route: the schema parses it, and the fake core's response for the same route
// has the same keys and value types (recursive; a failure lists every drifted path). Every event
// in events.jsonl parses and is handled by reduce() without reaching its "unknown" branch.
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import WebSocket from "ws";
import { z } from "zod";
import {
  agentInfoSchema, agentRunSchema, approvalSchema, claudeCodeStatusSchema, coreStatusSchema, eventSchema, keyStatusSchema, localActionSchema, localStatusSchema, localTestSchema,
  modelsInfoSchema, onshapeTestSchema, partSummarySchema, profileSchema, setupStateSchema, stepSchema,
} from "../src/api/schemas";
import { simRecordSchema, simSolversSchema, simStartedSchema, simSummarySchema } from "../src/api/sims";
import type { MisesEvent } from "../src/api/types";
import { emptyState, KNOWN_EVENTS, reduce, type AppState } from "../src/state/reduce";
import { loadScenario, SCENARIO_DIR, startFakeCore, type FakeCore } from "./fake-core/server";
import { discriminator, shapeDiff, type Json } from "./shape";

const debugCalls: unknown[][] = [];
vi.mock("../src/lib/log", () => ({ debug: (...args: unknown[]) => { debugCalls.push(args); } }));

const HERE = import.meta.dirname;
const CORE_FIXTURES = resolve(HERE, "../../core/tests/fixtures/ui");
const APP_FIXTURES = resolve(HERE, "fixtures/core-ui");

function fixtureDir(): string {
  if (process.env.MISES_CONTRACT_FIXTURES) return resolve(process.env.MISES_CONTRACT_FIXTURES);
  if (process.env.MISES_CONTRACT_LIVE !== "1") return CORE_FIXTURES;
  const out = mkdtempSync(join(tmpdir(), "mises-contract-"));
  const core = resolve(HERE, "../../core");
  execFileSync("uv", ["run", "--project", core, "python", join(core, "scripts/dump_ui_fixtures.py"), "--out", out], { cwd: core, stdio: "inherit" });
  return out;
}

const DIR = fixtureDir();
const read = (dir: string, name: string): unknown => JSON.parse(readFileSync(join(dir, name), "utf8"));
const readEvents = (dir: string): unknown[] => readFileSync(join(dir, "events.jsonl"), "utf8").split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l));

// ---- fake core

let core: FakeCore;
/** The real core's run (fixtures) and the base scenario's run (the fake core) have different ids. */
const RUN_ID = (read(DIR, "runs.json") as { id: string }[])[0].id;
const FAKE_RUN_ID = loadScenario("base").runs[0].id;

async function getFake(path: string, method = "GET", body?: unknown): Promise<Json> {
  const headers: Record<string, string> = { Authorization: `Bearer ${core.token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(core.url + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  expect(r.status, `${method} ${path}`).toBe(200);
  return (await r.json()) as Json;
}

beforeAll(async () => {
  core = await startFakeCore({ port: 0, token: "contract", scenario: "base" });
});
afterAll(async () => { await core.stop(); });

interface Route {
  fixture: string; schema: z.ZodType<unknown>; fake: () => string; root: string;
  /** Write routes: the same call the dump script made (core/scripts/dump_ui_fixtures.py). */
  method?: "POST" | "PUT"; body?: () => unknown;
  /** The fake-core scenario to answer from (default "base"; the /local routes need a `local` block). */
  scenario?: string;
}
/** Answers the client ignores (it re-reads status instead): any JSON, but the fake's must look like the core's. */
const anyJson = z.unknown();
const ROUTES: Route[] = [
  { fixture: "runs.json", schema: z.array(agentRunSchema), fake: () => "/runs?limit=50", root: "runs" },
  { fixture: "steps.json", schema: z.array(stepSchema), fake: () => `/runs/${FAKE_RUN_ID}/steps?after_id=0`, root: "steps" },
  { fixture: "approvals.json", schema: z.array(approvalSchema), fake: () => "/approvals?status=pending", root: "approvals" },
  { fixture: "parts.json", schema: z.array(partSummarySchema), fake: () => "/parts", root: "parts" },
  { fixture: "agents.json", schema: z.array(agentInfoSchema), fake: () => "/agents", root: "agents" },
  { fixture: "models.json", schema: modelsInfoSchema, fake: () => "/models", root: "models" },
  { fixture: "status.json", schema: coreStatusSchema, fake: () => "/status", root: "status" },
  // Plan 3: profile, keys, setup, the user's Claude Code, local/Onshape tests, UI context
  { fixture: "profile.json", schema: profileSchema, fake: () => "/profile", root: "profile" },
  { fixture: "profile_put.json", schema: profileSchema, fake: () => "/profile", root: "profile_put", method: "PUT", body: () => ({ display_name: "Fixture User" }) },
  { fixture: "keys.json", schema: z.array(keyStatusSchema), fake: () => "/keys", root: "keys" },
  { fixture: "setup.json", schema: setupStateSchema, fake: () => "/setup", root: "setup" },
  { fixture: "setup_complete.json", schema: setupStateSchema, fake: () => "/setup/complete", root: "setup_complete", method: "POST" },
  { fixture: "claude_code_status.json", schema: claudeCodeStatusSchema, fake: () => "/claude-code/status", root: "claude_code_status" },
  { fixture: "claude_code_logout.json", schema: claudeCodeStatusSchema, fake: () => "/claude-code/logout", root: "claude_code_logout", method: "POST" },
  { fixture: "claude_code_login.json", schema: anyJson, fake: () => "/claude-code/login", root: "claude_code_login", method: "POST" },
  { fixture: "local_test.json", schema: localTestSchema, fake: () => "/local/test", root: "local_test", method: "POST", body: () => ({ url: "http://localhost:11434/v1" }) },
  { fixture: "onshape_test.json", schema: onshapeTestSchema, fake: () => "/onshape/test", root: "onshape_test", method: "POST" },
  { fixture: "context_put.json", schema: anyJson, fake: () => "/context", root: "context_put", method: "PUT",
    body: () => ({ selected_part_id: "mount_plate", selected_run_id: FAKE_RUN_ID, picks: [] }) },
  // Plan 4: Ollama on this Mac (the dump script stands in a fixed Mac and Ollama answers)
  { fixture: "local_status.json", schema: localStatusSchema, fake: () => "/local/status", root: "local_status", scenario: "agent-ux" },
  { fixture: "local_start.json", schema: localActionSchema, fake: () => "/local/start", root: "local_start", method: "POST", scenario: "agent-ux" },
  { fixture: "local_pull_cancel.json", schema: localActionSchema, fake: () => "/local/pull/cancel", root: "local_pull_cancel", method: "POST",
    body: () => ({ model: "qwen3-coder:30b" }), scenario: "agent-ux" },
];

describe("contract: real core fixtures ↔ schemas ↔ fake core", () => {
  it("the base scenario serves one run, like the fixtures", () => {
    expect(loadScenario("base").runs).toHaveLength(1); // one designer run, like the fixtures
  });

  for (const r of ROUTES) {
    describe(r.fixture, () => {
      it("schema_parses_the_real_core_answer", () => {
        const parsed = r.schema.safeParse(read(DIR, r.fixture));
        expect(parsed.success, parsed.success ? "" : JSON.stringify(parsed.error.issues.slice(0, 5))).toBe(true);
      });

      it("fake_core_answer_has_the_same_shape", async () => {
        core.reset(r.scenario ?? "base");   // write routes change state: every route starts from its scenario
        const fake = await getFake(r.fake(), r.method, r.body?.());
        expect(r.schema.safeParse(fake).success).toBe(true);
        expect(shapeDiff(read(DIR, r.fixture), fake, r.root)).toEqual([]);
      });

      it("app_unit_test_copy_has_the_same_shape", () => {
        expect(shapeDiff(read(DIR, r.fixture), read(APP_FIXTURES, r.fixture), r.root)).toEqual([]);
      });
    });
  }

  describe("events.jsonl", () => {
    const events = readEvents(DIR);

    it("every_line_parses", () => {
      expect(events.length).toBeGreaterThan(0);
      events.forEach((e, i) => {
        const p = eventSchema.safeParse(e);
        expect(p.success, `line ${i + 1}`).toBe(true);
      });
    });

    it("every_event_type_is_handled_by_reduce_not_the_unknown_branch", () => {
      let state: AppState = emptyState();
      const runs = read(DIR, "runs.json") as AppState["runs"] extends ReadonlyMap<string, infer R> ? R[] : never;
      state = { ...state, runs: new Map(runs.map((r) => [r.id, r])) };
      for (const raw of events) {
        const e = eventSchema.parse(raw) as MisesEvent;
        expect(KNOWN_EVENTS.has(e.type), e.type).toBe(true);
        debugCalls.length = 0;
        const out = reduce(state, e);
        const unknown = debugCalls.filter((c) => String(c[0]).includes("unknown event") || String(c[0]).includes("reduce failed"));
        expect(unknown, e.type).toEqual([]);
        state = out.state;
      }
      // and it did something with them: the approval is pending, the open call c4 is in flight
      const approvals = read(DIR, "approvals.json") as { id: string }[];
      expect(state.approvals.has(approvals[0].id)).toBe(true);
      expect(state.inflight.has("c4")).toBe(true);
      expect(state.liveText.get(RUN_ID)).toBeTruthy(); // RUN_ID: the fixtures' own run
    });

    it("events_the_fake_core_sends_itself_match_the_real_ones", async () => {
      const ws = new WebSocket(`${core.url.replace("http", "ws")}/events?token=${core.token}`);
      const got: unknown[] = [];
      ws.on("message", (d) => got.push(JSON.parse(String(d))));
      await new Promise((ok, fail) => { ws.on("open", ok); ws.on("error", fail); });
      const post = (p: string, body: unknown) => fetch(core.url + p, {
        method: "POST", headers: { Authorization: `Bearer ${core.token}`, "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      await post("/runs", { agent: "designer", prompt: "make a plate" });
      for (let i = 0; i < 100 && got.length < 1; i++) await new Promise((r) => setTimeout(r, 5));
      ws.close();
      const real = events.find((e) => (e as MisesEvent).type === "run_started");
      const fake = got.find((e) => (e as MisesEvent).type === "run_started");
      expect(fake).toBeDefined();
      expect(shapeDiff(real, fake, "$event")).toEqual([]);
    });
  });

  describe("scenario files", () => {
    const files = readdirSync(SCENARIO_DIR).filter((f) => f.endsWith(".json"));
    const realSteps = read(DIR, "steps.json") as unknown[];
    const realEvents = readEvents(DIR) as MisesEvent[];

    it.each(files)("%s parses with the client schemas and matches the real shapes where they overlap", (file) => {
      const sc = loadScenario(file.replace(/\.json$/, ""));
      const problems: string[] = [];
      const check = (schema: z.ZodType<unknown>, v: unknown, where: string) => {
        const p = schema.safeParse(v);
        if (!p.success) problems.push(`${where}: ${p.error.issues.slice(0, 3).map((x) => `${x.path.join(".")} ${x.message}`).join("; ")}`);
      };
      check(z.array(agentRunSchema), sc.runs, "runs");
      check(z.array(stepSchema), sc.steps, "steps");
      check(z.array(approvalSchema), sc.approvals, "approvals");
      check(z.array(partSummarySchema), sc.parts, "parts");
      check(z.array(agentInfoSchema), sc.agents, "agents");
      check(modelsInfoSchema, sc.models, "models");
      check(coreStatusSchema, sc.status, "status");
      // steps of a kind + tool the real core produced must look like the real ones
      for (const s of sc.steps) {
        const real = realSteps.find((x) => discriminator(x) === discriminator(s));
        // a scenario may leave out tool args and result data it doesn't need (missing), but
        // must not invent fields or change types (extra / type: the UI would rely on them)
        if (real) problems.push(...shapeDiff(real, s, `steps[${s.id}]`).filter((p) => !(/\.payload\.(input|output\.data)\./.test(p) && /: missing/.test(p))));
      }
      // scripted events: envelope always, data where the real core sent the same type
      // timed scripts and trigger scripts ("$run" stands for the run they play for)
      const entries = [...(sc.script ?? []), ...Object.values(sc.on ?? {}).flat()];
      for (const [i, entry] of entries.entries()) {
        const ev = { type: entry.event.type, run_id: entry.event.run_id ?? null, data: entry.event.data ?? {}, ts: entry.event.ts ?? 0 };
        check(eventSchema, ev, `script[${i}]`);
        const real = realEvents.find((x) => x.type === ev.type && (x.data.name === undefined || x.data.name === ev.data.name));
        if (real && ev.type !== "approval_requested") problems.push(...shapeDiff(real.data, ev.data, `script[${i}].${ev.type}`));
        if (ev.type === "approval_requested") check(approvalSchema, ev.data, `script[${i}].approval`);
        for (const st of entry.steps ?? []) {
          check(stepSchema.partial().required({ kind: true }), st, `script[${i}].steps`);
          const realStep = realSteps.find((x) => discriminator(x) === discriminator(st)) as Record<string, unknown> | undefined;
          if (realStep) problems.push(...shapeDiff(realStep.payload, st.payload, `script[${i}].steps.payload`).filter((p) => !(/\.(input|output\.data)\./.test(p) && /: missing/.test(p))));
        }
      }
      expect(problems).toEqual([]);
    });
  });

  // Static stress studies (core server/sim_api.py). The dump only writes these with CalculiX
  // installed; the fake core's studies must look like the real ones.
  describe.skipIf(!existsSync(join(DIR, "sim_done.json")))("sims", () => {
    const sim = (sims: Record<string, unknown>) => ({ ...loadScenario("base"), sims });
    const BODY = { part_id: "mount_plate", fix: "x-min", load: { face: "x-max", newtons: 20, direction: "-z" }, material: "PETG", environment: "Desk" };
    const post = async (body: unknown) => {
      const r = await fetch(`${core.url}/sims`, {
        method: "POST", headers: { Authorization: `Bearer ${core.token}`, "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      expect(r.status).toBe(202); // like the core: accepted, the study runs in the background
      return (await r.json()) as Json;
    };
    const settle = async (runId: string) => {
      for (let i = 0; i < 300; i++) {
        const list = (await getFake(`/sims?run_id=${runId}`)) as { status: string }[];
        if (list[0] && list[0].status !== "running") return list;
        await new Promise((r) => setTimeout(r, 10));
      }
      throw new Error("fake study never finished");
    };

    it.each([
      ["sim_done.json", simRecordSchema], ["sim_failed.json", simRecordSchema], ["sims_list.json", z.array(simSummarySchema)],
      ["sims_solvers.json", simSolversSchema], ["sims_solvers_missing.json", simSolversSchema], ["sims_start.json", simStartedSchema],
    ] as const)("schema parses the real %s", (name, schema) => {
      const p = (schema as z.ZodType<unknown>).safeParse(read(DIR, name));
      expect(p.success, p.success ? "" : JSON.stringify(p.error.issues.slice(0, 5))).toBe(true);
    });

    it("solver status: same shape found and missing", async () => {
      core.reset(sim({}));
      expect(shapeDiff(read(DIR, "sims_solvers.json"), await getFake("/sims/solvers"), "solvers")).toEqual([]);
      core.reset(sim({ ccx: false }));
      expect(shapeDiff(read(DIR, "sims_solvers_missing.json"), await getFake("/sims/solvers"), "solvers")).toEqual([]);
    });

    it("a study the fake core runs looks like the real one (start, list, record)", async () => {
      core.reset(sim({ step_ms: 5 }));
      const started = await post(BODY);
      expect(shapeDiff(read(DIR, "sims_start.json"), started, "start")).toEqual([]);
      const list = await settle((started as { run_id: string }).run_id);
      const realList = read(DIR, "sims_list.json") as { status: string }[];
      expect(shapeDiff(realList.find((x) => x.status === "done"), list[0], "list[]")).toEqual([]);
      const rec = await getFake(`/sims/${(list[0] as unknown as { sim_id: string }).sim_id}`);
      expect(simRecordSchema.safeParse(rec).success).toBe(true);
      expect(shapeDiff(read(DIR, "sim_done.json"), rec, "sim")).toEqual([]);
    });

    it("a study without CalculiX fails like the real one", async () => {
      core.reset(sim({ ccx: false, step_ms: 5 }));
      const started = await post(BODY);
      const list = await settle((started as { run_id: string }).run_id);
      const rec = await getFake(`/sims/${(list[0] as unknown as { sim_id: string }).sim_id}`);
      const real = read(DIR, "sim_failed.json") as Record<string, unknown>;
      expect(shapeDiff(real, rec, "sim")).toEqual([]);
      expect((rec as Record<string, unknown>).error).toBe(real.error);
      expect((rec as Record<string, unknown>).hint).toBe(real.hint);
    });

    it("the events of a study match the real core's (run_started, tool_call, sim_progress, tool_result, run_finished)", async () => {
      core.reset(sim({ step_ms: 5 }));
      const ws = new WebSocket(`${core.url.replace("http", "ws")}/events?token=${core.token}`);
      const got: MisesEvent[] = [];
      ws.on("message", (d) => got.push(JSON.parse(String(d)) as MisesEvent));
      await new Promise((ok, fail) => { ws.on("open", ok); ws.on("error", fail); });
      const started = await post(BODY);
      await settle((started as { run_id: string }).run_id);
      for (let i = 0; i < 100 && !got.some((e) => e.type === "run_finished"); i++) await new Promise((r) => setTimeout(r, 5));
      ws.close();
      const realSim = readFileSync(join(DIR, "sim_events.jsonl"), "utf8").split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l) as MisesEvent);
      const realOk = realSim.slice(0, realSim.findIndex((e) => e.type === "run_finished") + 1);
      expect(got.map((e) => e.type)).toEqual(realOk.map((e) => e.type));
      got.forEach((e, i) => expect(shapeDiff(realOk[i].data, e.data, `$${e.type}`)).toEqual([]));
    });
  });

  it("shape_diff_lists_every_drifted_path", () => {
    const real = { a: 1, b: { c: "x", d: [1, 2] }, e: null };
    const fake = { a: "1", b: { d: ["y"], z: true }, e: 3 };
    expect(shapeDiff(real, fake)).toEqual([
      "$.a: string (real core: number)",
      "$.b.c: missing (real core has it)",
      "$.b.z: extra (real core lacks it)",
      "$.b.d[]: string (real core: number)",
    ]);
  });
});
