// @vitest-environment node
// Contract test, Plan 4: drawing / printer / slice / print answers. The real core's answers are
// core/tests/fixtures/ui (core/scripts/dump_ui_fixtures.py: the core tests' fake PrusaSlicer and
// fake PrusaLink). For each: the app schema parses it, the fake core's answer for the same step
// of the same flow has the same keys and value types, and the app's unit-test copy matches.
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { z } from "zod";
import {
  approvalSchema, drawingInfoSchema, printCallSchema, printCapabilitiesSchema, printerSchema, printerStatusSchema, printStartSchema,
  sliceSchema, sliceStartSchema,
} from "../src/api/schemas";
import { startFakeCore, type FakeCore, type Scenario } from "./fake-core/server";
import { loadScenario } from "./fake-core/server";
import { shapeDiff } from "./shape";

const HERE = import.meta.dirname;
const DIR = process.env.MISES_CONTRACT_FIXTURES ? resolve(process.env.MISES_CONTRACT_FIXTURES) : resolve(HERE, "../../core/tests/fixtures/ui");
const APP = resolve(HERE, "fixtures/core-ui");
const read = (dir: string, name: string): unknown => JSON.parse(readFileSync(join(dir, name), "utf8"));
/** The card's payload / deltas are compared key for key below, not as the generic diff's open maps. */
const shell = (name: string, v: unknown): unknown => {
  if (name !== "print_approval.json" || typeof v !== "object" || v === null) return v;
  const { payload: _p, deltas: _d, ...rest } = v as Record<string, unknown>;
  return rest;
};

let core: FakeCore;
const fakeAnswers = new Map<string, unknown>();

async function call(method: string, path: string, body?: unknown): Promise<unknown> {
  const headers: Record<string, string> = { Authorization: `Bearer ${core.token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(core.url + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  expect(r.status, `${method} ${path}`).toBe(200);
  return r.json();
}

async function until(path: string, ok: (v: Record<string, unknown>) => boolean): Promise<Record<string, unknown>> {
  for (let i = 0; i < 200; i++) {
    const v = (await call("GET", path)) as Record<string, unknown>;
    if (ok(v)) return v;
    await new Promise((r) => setTimeout(r, 10));
  }
  throw new Error(`${path} never settled`);
}

/** The same flow the dump script runs against the real core. */
async function flow(): Promise<void> {
  const sc: Scenario = { ...loadScenario("base"), print: { slice_ms: 20, card_ms: 10 } };
  core.reset(sc);
  const a = fakeAnswers;
  a.set("drawing.json", await call("GET", "/parts/mount_plate/drawing.json"));
  a.set("print_capabilities.json", await call("GET", "/print/capabilities"));
  a.set("printer_add.json", await call("POST", "/printers", { name: "Bench MK4", host: "127.0.0.1:8080", api_key: "fake-key-abc" }));
  a.set("printers.json", await call("GET", "/printers"));
  a.set("printer_status.json", await call("GET", "/printers/bench_mk4/status"));
  const start = (await call("POST", "/slices", { part_id: "mount_plate", printer_id: "bench_mk4" })) as { slice_id: string };
  a.set("slice_start.json", start);
  a.set("slice.json", await until(`/slices/${start.slice_id}`, (v) => v.status !== "running"));
  const sent = (await call("POST", "/prints", { slice_id: start.slice_id, printer_id: "bench_mk4" })) as { call_id: string };
  a.set("print_start.json", sent);
  a.set("print_running.json", await call("GET", `/prints/${sent.call_id}`));
  let card: { id: string; payload_hash: string } | undefined;
  for (let i = 0; i < 100 && !card; i++) {
    card = ((await call("GET", "/approvals")) as { id: string; action: string; payload_hash: string }[]).find((x) => x.action === "print_send");
    if (!card) await new Promise((r) => setTimeout(r, 10));
  }
  if (!card) throw new Error("no print_send card");
  a.set("print_approval.json", card);
  await call("POST", `/approvals/${card.id}/decide`, { payload_hash: card.payload_hash, approve: true });
  a.set("print_done.json", await until(`/prints/${sent.call_id}`, (v) => v.status !== "running"));
  a.set("printer_status_printing.json", await call("GET", "/printers/bench_mk4/status"));
  core.state.print.states.bench_mk4 = { state: null };
  a.set("printer_status_unreachable.json", await call("GET", "/printers/bench_mk4/status"));
  a.set("printer_delete.json", await call("DELETE", "/printers/bench_mk4"));
}

beforeAll(async () => {
  core = await startFakeCore({ port: 0, token: "contract", scenario: "base" });
  await flow();
});
afterAll(async () => { await core.stop(); });

const CASES: [string, z.ZodType<unknown>][] = [
  ["drawing.json", drawingInfoSchema], ["print_capabilities.json", printCapabilitiesSchema], ["printer_add.json", printerSchema],
  ["printers.json", z.array(printerSchema)], ["printer_status.json", printerStatusSchema], ["printer_status_printing.json", printerStatusSchema],
  ["printer_status_unreachable.json", printerStatusSchema], ["slice_start.json", sliceStartSchema], ["slice.json", sliceSchema],
  ["print_start.json", printStartSchema], ["print_running.json", printCallSchema], ["print_done.json", printCallSchema],
  ["print_approval.json", approvalSchema], ["printer_delete.json", z.object({ ok: z.literal(true) })],
];

describe("contract: drawing + print fixtures ↔ schemas ↔ fake core", () => {
  for (const [name, schema] of CASES) {
    describe(name, () => {
      it("schema_parses_the_real_core_answer", () => {
        const p = schema.safeParse(read(DIR, name));
        expect(p.success, p.success ? "" : JSON.stringify(p.error.issues.slice(0, 5))).toBe(true);
      });
      it("fake_core_answer_has_the_same_shape", () => {
        const fake = fakeAnswers.get(name);
        expect(schema.safeParse(fake).success).toBe(true);
        expect(shapeDiff(shell(name, read(DIR, name)), shell(name, fake), name)).toEqual([]);
      });
      it("app_unit_test_copy_has_the_same_shape", () => {
        expect(shapeDiff(shell(name, read(DIR, name)), shell(name, read(APP, name)), name)).toEqual([]);
      });
    });
  }

  it("print_card_payload_and_deltas_match_key_for_key", () => {
    // approval payload/deltas are open maps in the generic diff; the print card's are a contract
    const real = read(DIR, "print_approval.json") as { payload: unknown; deltas: unknown };
    const fake = fakeAnswers.get("print_approval.json") as { payload: unknown; deltas: unknown };
    expect(shapeDiff(real.payload, fake.payload, "print_payload")).toEqual([]);
    expect(shapeDiff(real.deltas, fake.deltas, "print_deltas")).toEqual([]);
    expect(Object.keys(fake.payload as object).sort()).toEqual(["file_name", "gcode_sha256", "printer_id", "slice_id"]);
  });

  it("drawing_svg_fixture_is_a_plain_svg_picture", () => {
    const svg = readFileSync(join(DIR, "drawing.svg"), "utf8");
    expect(svg).toContain("<svg");
    expect(svg).not.toMatch(/<script|on[a-z]+=|javascript:/i);
  });
});
