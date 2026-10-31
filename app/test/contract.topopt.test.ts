// @vitest-environment node
// Contract test, Plan 9 D14: the fake core's generative design routes against the real core's
// answers (core/scripts/dump_topopt_fixtures.py → core/tests/fixtures/ui/topopt_*): same shapes
// for start, running and done records, list, events, cancel and accept, and every real fixture
// parses with the app's schemas.
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { WebSocket } from "ws";
import { topoptAcceptResultSchema, topoptEventSchema, topoptRecordSchema } from "../src/api/topopt";
import type { MisesEvent, PartSummary } from "../src/api/types";
import { loadScenario, startFakeCore, type FakeCore, type Scenario } from "./fake-core/server";
import { shapeDiff, type Json } from "./shape";

const DIR = resolve(import.meta.dirname, "../../core/tests/fixtures/ui");
const read = (name: string): Json => JSON.parse(readFileSync(join(DIR, name), "utf8")) as Json;
const EVENTS = readFileSync(join(DIR, "topopt_events.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l) as MisesEvent);

const BLOCK = { part_id: "block", vars: {}, mtime: 1, metrics: {
  valid: true, volume_mm3: 3072, area_mm2: 1280, mass_g: 3.8, bbox: { min: [0, 0, 0], max: [24, 16, 8], size: [24, 16, 8] },
  center_of_mass: [12, 8, 4], faces: 6, edges: 12, holes: [], solids: 1, face_list: [],
} } as PartSummary;
const BODY = {
  part_id: "block", fixtures: [{ face: "x-min" }], load_cases: [{ name: "tip", loads: [{ face: "x-max", newtons: 40, direction: "-z" }] }],
  volume_fraction: 0.35, sweep: { volume_fraction: [0.3, 0.4] }, max_variants: 2,
};

let core: FakeCore;
const scenario = (): Scenario => ({ ...loadScenario("base"), parts: [BLOCK] });

async function call(method: string, path: string, body?: unknown): Promise<{ status: number; json: Json; type: string }> {
  const headers: Record<string, string> = { Authorization: `Bearer ${core.token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(core.url + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const type = r.headers.get("content-type") ?? "";
  return { status: r.status, type, json: type.includes("json") ? (await r.json()) as Json : null };
}

beforeAll(async () => { core = await startFakeCore({ port: 0, token: "contract-token", scenario: "base" }); });
afterAll(async () => { await core.stop(); });

describe("generative design: real fixtures parse", () => {
  it("records, accept and events", () => {
    for (const n of ["topopt_start.json", "topopt_running.json", "topopt_record.json"]) expect(topoptRecordSchema.safeParse(read(n)).success).toBe(true);
    for (const r of read("topopt_list.json") as Json[]) expect(topoptRecordSchema.safeParse(r).success).toBe(true);
    expect(topoptAcceptResultSchema.safeParse(read("topopt_accept.json")).success).toBe(true);
    expect(EVENTS.length).toBeGreaterThan(4);
    for (const ev of EVENTS) {
      expect(topoptEventSchema.safeParse(ev.data).success).toBe(true);
      expect(ev.data.stream).toBe("sim");
    }
    expect(EVENTS.at(-1)?.type).toBe("topopt_finished");
  });
});

describe("generative design routes: fake core = real core", () => {
  it("start, progress events, done record, list, files, cancel conflict, accept", async () => {
    core.reset(scenario());
    const ws = new WebSocket(`${core.url.replace("http", "ws")}/events?token=${core.token}`);
    const got: MisesEvent[] = [];
    ws.on("message", (d) => got.push(JSON.parse(String(d)) as MisesEvent));
    await new Promise((ok, fail) => { ws.on("open", ok); ws.on("error", fail); });
    try {
      const bad = await call("POST", "/topopt", { ...BODY, volume_fraction: 0.95 });
      expect(bad.status).toBe(400);
      expect(shapeDiff(read("topopt_start_invalid.json"), bad.json, "invalid")).toEqual([]);
      const start = await call("POST", "/topopt", BODY);
      expect(start.status).toBe(202);
      expect(shapeDiff(read("topopt_start.json"), start.json, "start")).toEqual([]);
      const id = (start.json as { topopt_id: string }).topopt_id;
      const end = Date.now() + 8000;
      let rec = start.json as { status: string };
      while (rec.status === "running" && Date.now() < end) {
        await new Promise((r) => setTimeout(r, 50));
        rec = (await call("GET", `/topopt/${id}`)).json as { status: string };
      }
      expect(rec.status).toBe("done");
      expect(shapeDiff(read("topopt_record.json"), rec, "done")).toEqual([]);
      const realProgress = EVENTS.find((e) => e.type === "topopt_progress" && e.data.preview);
      const fakeProgress = got.find((e) => e.type === "topopt_progress");
      expect(shapeDiff(realProgress?.data as Json, fakeProgress?.data as Json, "progress").filter((x) => !/extra/.test(x))).toEqual([]);
      expect(got.some((e) => e.type === "topopt_finished")).toBe(true);
      const list = await call("GET", "/topopt?part_id=block");
      expect(shapeDiff((read("topopt_list.json") as Json[])[0], (list.json as Json[])[0], "list[]")).toEqual([]);
      const png = await call("GET", `/topopt/${id}/variants/v0/render.png`);
      expect(png.status).toBe(200);
      expect(png.type).toBe("image/png");
      const conflict = await call("POST", `/topopt/${id}/cancel`);
      expect(conflict.status).toBe(409);
      expect(shapeDiff(read("topopt_cancel_conflict.json"), conflict.json, "conflict")).toEqual([]);
      const acc = await call("POST", `/topopt/${id}/accept`, { mode: "new" });
      expect(acc.status).toBe(200);
      expect(shapeDiff(read("topopt_accept.json"), acc.json, "accept")).toEqual([]);
      expect((acc.json as { part_id: string }).part_id).toBe("block_generative");
    } finally {
      ws.close();
    }
  });
});
