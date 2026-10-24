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
import { mkdtempSync, readdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import WebSocket from "ws";
import { z } from "zod";
import {
  agentInfoSchema, agentRunSchema, approvalSchema, coreStatusSchema, eventSchema, modelsInfoSchema, partSummarySchema, stepSchema,
} from "../src/api/schemas";
import type { MisesEvent } from "../src/api/types";
import { emptyState, KNOWN_EVENTS, reduce, type AppState } from "../src/state/reduce";
import { loadScenario, SCENARIO_DIR, startFakeCore, type FakeCore } from "./fake-core/server";

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

// ---- recursive shape diff

type Json = null | boolean | number | string | Json[] | { [k: string]: Json };

function typeOf(v: unknown): string {
  if (v === null) return "null";
  if (Array.isArray(v)) return "array";
  return typeof v;
}

/** Objects whose keys are data (variable names, tool inputs, approval payloads), not schema. */
const OPEN_MAPS = [/\.vars$/, /\.deltas$/, /^approvals\[\]\.payload$/, /\.edited_payload$/];

/** A step / event pairs with the fixture element of the same kind and tool (or role). */
function discriminator(v: unknown): string | null {
  if (typeof v !== "object" || v === null || Array.isArray(v)) return null;
  const o = v as Record<string, unknown>;
  const p = (typeof o.payload === "object" && o.payload !== null ? o.payload : {}) as Record<string, unknown>;
  if (typeof o.kind === "string") return `${o.kind}:${String(p.name ?? p.role ?? "")}`;
  return null;
}

/** Paths where `actual` (fake core) differs in keys or value types from `expected` (real core). null on either side is
 *  accepted (nullable fields; zod checks those). */
export function shapeDiff(expected: unknown, actual: unknown, path = "$", out: string[] = []): string[] {
  const te = typeOf(expected);
  const ta = typeOf(actual);
  if (te === "null" || ta === "null") return out;
  if (te !== ta) { out.push(`${path}: ${ta} (real core: ${te})`); return out; }
  if (te === "array") {
    const e = expected as unknown[];
    const a = actual as unknown[];
    if (e.length === 0 || a.length === 0) return out;
    a.forEach((item, i) => {
      const d = discriminator(item);
      const match = d === null ? e[0] : e.find((x) => discriminator(x) === d);
      if (match === undefined) { out.push(`${path}[${i}]: ${d} has no counterpart in the real core's answer`); return; }
      shapeDiff(match, item, `${path}[]`, out);
    });
    return out;
  }
  if (te === "object") {
    const e = expected as Record<string, unknown>;
    const a = actual as Record<string, unknown>;
    if (OPEN_MAPS.some((re) => re.test(path))) {
      const sample = Object.values(e).find((v) => v !== null);
      if (sample !== undefined) for (const [k, v] of Object.entries(a)) shapeDiff(sample, v, `${path}.${k}`, out);
      return out;
    }
    for (const k of Object.keys(e)) if (!(k in a)) out.push(`${path}.${k}: missing (real core has it)`);
    for (const k of Object.keys(a)) if (!(k in e)) out.push(`${path}.${k}: extra (real core lacks it)`);
    for (const k of Object.keys(e)) if (k in a) shapeDiff(e[k], a[k], `${path}.${k}`, out);
  }
  return out;
}

// ---- fake core

let core: FakeCore;
/** The real core's run (fixtures) and the base scenario's run (the fake core) have different ids. */
const RUN_ID = (read(DIR, "runs.json") as { id: string }[])[0].id;
const FAKE_RUN_ID = loadScenario("base").runs[0].id;

async function getFake(path: string): Promise<Json> {
  const r = await fetch(core.url + path, { headers: { Authorization: `Bearer ${core.token}` } });
  expect(r.status, path).toBe(200);
  return (await r.json()) as Json;
}

beforeAll(async () => {
  core = await startFakeCore({ port: 0, token: "contract", scenario: "base" });
});
afterAll(async () => { await core.stop(); });

interface Route { fixture: string; schema: z.ZodType<unknown>; fake: () => string; root: string }
const ROUTES: Route[] = [
  { fixture: "runs.json", schema: z.array(agentRunSchema), fake: () => "/runs?limit=50", root: "runs" },
  { fixture: "steps.json", schema: z.array(stepSchema), fake: () => `/runs/${FAKE_RUN_ID}/steps?after_id=0`, root: "steps" },
  { fixture: "approvals.json", schema: z.array(approvalSchema), fake: () => "/approvals?status=pending", root: "approvals" },
  { fixture: "parts.json", schema: z.array(partSummarySchema), fake: () => "/parts", root: "parts" },
  { fixture: "agents.json", schema: z.array(agentInfoSchema), fake: () => "/agents", root: "agents" },
  { fixture: "models.json", schema: modelsInfoSchema, fake: () => "/models", root: "models" },
  { fixture: "status.json", schema: coreStatusSchema, fake: () => "/status", root: "status" },
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
        const fake = await getFake(r.fake());
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
      for (const [i, entry] of (sc.script ?? []).entries()) {
        const ev = { type: entry.event.type, run_id: entry.event.run_id ?? null, data: entry.event.data ?? {}, ts: entry.event.ts ?? 0 };
        check(eventSchema, ev, `script[${i}]`);
        const real = realEvents.find((x) => x.type === ev.type && (x.data.name === undefined || x.data.name === ev.data.name));
        if (real && ev.type !== "approval_requested") problems.push(...shapeDiff(real.data, ev.data, `script[${i}].${ev.type}`));
        if (ev.type === "approval_requested") check(approvalSchema, ev.data, `script[${i}].approval`);
        for (const st of entry.steps ?? []) check(stepSchema.partial().required({ kind: true }), st, `script[${i}].steps`);
      }
      expect(problems).toEqual([]);
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
