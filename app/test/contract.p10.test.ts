// @vitest-environment node
// Contract test, Plan 10 C1: the real core's chat / queued-cancel / retry answers, the classified
// error step and the edited message (core/scripts/dump_p10_c1_fixtures.py → fixtures/ui/p10)
// against the app's schemas, the thread model and the fake core.
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createRunResultSchema, okSchema, profileSchema, stepSchema } from "../src/api/schemas";
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

describe("contract: P10 FWA Profile → Claude Code → Network (claude_code_env) real core ↔ app ↔ fake core", () => {
  const ENV = { HTTPS_PROXY: "http://alice:s3cret@proxy.corp:8080", NO_PROXY: "localhost,.corp.example" };

  it("a proxy's credentials come back masked from PUT and GET, and the masked value round-trips", async () => {
    for (const name of ["claude_env_put.json", "claude_env_get.json"]) {
      const p = profileSchema.parse(real(name));
      expect(p.claude_code_env, name).toEqual({ HTTPS_PROXY: "http://***@proxy.corp:8080", NO_PROXY: "localhost,.corp.example" });
      expect(JSON.stringify(real(name))).not.toContain("s3cret");
    }
    const put = await fake("PUT", "/profile", { claude_code_env: ENV });
    expect(put.status).toBe(200);
    expect((put.json as { claude_code_env: unknown }).claude_code_env).toEqual((real("claude_env_put.json") as { claude_code_env: unknown }).claude_code_env);
    const got = (await fake("GET", "/profile")).json as { claude_code_env: Record<string, string> };
    expect(got.claude_code_env).toEqual((real("claude_env_get.json") as { claude_code_env: unknown }).claude_code_env);
    // the app sends the masked value back unchanged with another edit: still the same proxy
    const again = await fake("PUT", "/profile", { claude_code_env: { ...got.claude_code_env, NO_PROXY: "localhost" } });
    expect(again.status).toBe(200);
    expect((again.json as { claude_code_env: Record<string, string> }).claude_code_env.HTTPS_PROXY).toBe("http://***@proxy.corp:8080");
    await fake("PUT", "/profile", { claude_code_env: {} });
  });

  it("refuses what the core refuses, with the core's message", async () => {
    for (const [name, env] of [
      ["claude_env_bad_proxy.json", { HTTPS_PROXY: "proxy.corp:8080" }],
      ["claude_env_missing_ca.json", { NODE_EXTRA_CA_CERTS: "/nonexistent/mises-ca.pem" }],
      ["claude_env_bad_key.json", { ANTHROPIC_API_KEY: "sk-not-allowed" }],
    ] as const) {
      const want = real(name) as { status: number; body: Record<string, unknown> };
      const got = await fake("PUT", "/profile", { claude_code_env: env });
      expect(got.status, name).toBe(want.status);
      expect(got.json, name).toEqual(want.body);
    }
  });
});
