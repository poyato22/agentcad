// @vitest-environment node
// Contract test, Plan 10 C1: the real core's chat / queued-cancel / retry answers, the classified
// error step and the edited message (core/scripts/dump_p10_c1_fixtures.py → fixtures/ui/p10)
// against the app's schemas, the thread model and the fake core.
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createRunResultSchema, okSchema, stepSchema } from "../src/api/schemas";
import { loadScenario, startFakeCore, type FakeCore } from "./fake-core/server";
import type { Json } from "./shape";

const HERE = import.meta.dirname;
const DIR = process.env.MISES_CONTRACT_FIXTURES
  ? resolve(process.env.MISES_CONTRACT_FIXTURES, "p10") : resolve(HERE, "../../core/tests/fixtures/ui/p10");
const real = (name: string): Json => JSON.parse(readFileSync(join(DIR, name), "utf8")) as Json;

let core: FakeCore;
async function fake(method: string, path: string, body?: unknown): Promise<{ status: number; json: unknown }> {
  const headers: Record<string, string> = { Authorization: `Bearer ${core.token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(core.url + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  return { status: r.status, json: r.headers.get("content-type")?.includes("json") ? await r.json() : await r.text() };
}

beforeAll(async () => {
  core = await startFakeCore({ port: 0, token: "contract", scenario: "base" });
  core.reset("base");
});
afterAll(async () => { await core.stop(); });

describe("contract: Plan 10 C1 real-core answers ↔ app schemas ↔ fake core", () => {
  it("POST /projects/{id}/chat answers like POST /runs, first message and follow-up", async () => {
    for (const name of ["chat.json", "chat_followup.json"]) {
      expect(createRunResultSchema.safeParse(real(name)).success, name).toBe(true);
    }
    expect((real("chat.json") as { new_lead: boolean }).new_lead).toBe(true);
    expect((real("chat_followup.json") as { new_lead: boolean }).new_lead).toBe(false);
    const builds = (await fake("GET", "/projects")).json as { id: string }[];
    const got = await fake("POST", `/projects/${builds[0].id}/chat`, { text: "Build a robot arm" });
    expect(got.status).toBe(200);
    expect(createRunResultSchema.safeParse(got.json).success).toBe(true);
    // the keys the app reads are there on both sides
    for (const k of ["run_id", "routed", "new_lead", "queued"]) {
      expect(k in (real("chat.json") as object), k).toBe(true);
      expect(k in (got.json as object), k).toBe(true);
    }
  });

  it("queued-message cancel answers {ok: true} and drops it with reason \"cancelled\"", async () => {
    expect(okSchema.safeParse(real("queued_cancel.json")).success).toBe(true);
    const ev = readFileSync(join(DIR, "queued_cancel_events.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l));
    expect(ev.map((e) => [e.type, e.data.reason])).toEqual([["message_dropped", "cancelled"]]);
    // the fake core refuses an unknown id the way the core does (a 404 core error)
    const run = loadScenario("base").runs[0].id;
    expect((await fake("POST", `/runs/${run}/queued/msg_000000000000/cancel`)).status).toBe(404);
  });

  it("retry answers {ok, run_id}", () => {
    const r = real("retry.json") as { ok: boolean; run_id: string };
    expect(r.ok).toBe(true);
    expect(typeof r.run_id).toBe("string");
  });

  it("a classified Claude Code error step carries kind / cause / hint", () => {
    const steps = (real("error_step_tls_steps.json") as unknown[]).map((s) => stepSchema.parse(s));
    const err = steps.find((s) => s.kind === "error");
    expect(err).toBeTruthy();
    const p = err!.payload as { kind?: string; cause?: string; hint?: string };
    expect(p.kind).toBe("tls");
    expect(typeof p.cause).toBe("string");
    expect(p.hint).toContain("NODE_EXTRA_CA_CERTS");
  });

  it("an edited message's new user step names the step it edits", () => {
    const steps = (real("message_edit_steps.json") as unknown[]).map((s) => stepSchema.parse(s));
    const edited = steps.filter((s) => s.kind === "message" && (s.payload as { edits?: string }).edits);
    expect(edited.length).toBe(1);
    const id = (edited[0].payload as { edits: string }).edits;
    expect(steps.some((s) => String(s.id) === id)).toBe(true);
  });
});
