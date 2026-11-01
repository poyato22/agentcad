// @vitest-environment node
// Contract test, Plan 11 F8: the real core's Think-control answers and the turn's effort steps
// (core/scripts/dump_p11_effort_fixtures.py → fixtures/ui/p11c) against the app's schemas, the
// thread model (each user message carries the effort its turn ran at) and the fake core.
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createRunResultSchema, stepSchema } from "../src/api/schemas";
import { effortChipText, effortDefaultSchema, effortOf } from "../src/api/think";
import type { Step } from "../src/api/types";
import { buildLog } from "../src/ui/console/logModel";
import { input } from "../src/ui/console/testSteps";
import { buildThread } from "../src/ui/console/threadModel";
import { startFakeCore, type FakeCore } from "./fake-core/server";
import { shapeDiff, type Json } from "./shape";

const HERE = import.meta.dirname;
const DIR = process.env.MISES_CONTRACT_FIXTURES
  ? resolve(process.env.MISES_CONTRACT_FIXTURES, "p11c") : resolve(HERE, "../../core/tests/fixtures/ui/p11c");
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

describe("contract: Plan 11 F8 real-core effort answers ↔ app ↔ fake core", () => {
  it("GET / PUT /projects/{id}/effort parse, and the fake answers the same shape", async () => {
    const got = effortDefaultSchema.parse(real("effort_get.json"));
    expect(got.default).toBe("auto");
    expect(effortDefaultSchema.parse(real("effort_put.json")).default).toBe("hard");
    expect((real("effort_bad.json") as { status: number }).status).toBe(422);
    const builds = (await fake("GET", "/projects")).json as { id: string }[];
    const g = await fake("GET", `/projects/${builds[0].id}/effort`);
    expect(shapeDiff(real("effort_get.json"), g.json as Json)).toEqual([]);
    const p = await fake("PUT", `/projects/${builds[0].id}/effort`, { default: "hard" });
    expect(p.json).toEqual(real("effort_put.json"));
    expect((await fake("PUT", `/projects/${builds[0].id}/effort`, { default: "ultra" })).status).toBe(422);
    expect((await fake("GET", `/projects/${builds[0].id}/effort`)).json).toMatchObject({ default: "hard" });
    await fake("PUT", `/projects/${builds[0].id}/effort`, { default: "auto" });
  });

  it("POST /projects/{id}/chat with effort answers like POST /runs; a bad pick is a 422", async () => {
    expect(createRunResultSchema.safeParse(real("chat_effort.json")).success).toBe(true);
    const builds = (await fake("GET", "/projects")).json as { id: string }[];
    expect((await fake("POST", `/projects/${builds[0].id}/chat`, { text: "hi", effort: "max" })).status).toBe(200);
    expect((await fake("POST", `/projects/${builds[0].id}/chat`, { text: "hi", effort: "ultra" })).status).toBe(422);
  });

  it("each user message in the thread carries its turn's effort decision", () => {
    const steps = (real("chat_effort_steps.json") as unknown as unknown[]).map((s) => stepSchema.parse(s)) as Step[];
    const decisions = steps.map(effortOf).filter((e) => e !== null);
    expect(decisions.map((d) => [d.level, d.source])).toEqual([["max", "user"], ["max", "auto"]]);
    const rid = steps[0].run_id;
    const items = buildThread(buildLog(input({ filter: { scope: "run", kind: "everything" }, selectedRunId: rid, stepList: steps })));
    const users = items.filter((i) => i.kind === "user");
    expect(users.map((u) => u.kind === "user" && u.effort?.level)).toEqual(["max", "max"]);
    // the decision steps are not "decided" notes of their own
    expect(items.filter((i) => i.kind === "note")).toEqual([]);
    const second = users[1];
    expect(second.kind === "user" && second.effort && effortChipText(second.effort)).toBe("Thinking: Max · open-ended: an egg-drop challenge");
  });
});
