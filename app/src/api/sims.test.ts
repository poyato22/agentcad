// The /sims schemas parse the real core's answers (core/tests/fixtures/ui, dumped by
// core/scripts/dump_ui_fixtures.py with CalculiX installed), and the client calls the right routes.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { z } from "zod";
import { CoreClient, CoreError } from "./client";
import { simRecordSchema, simSolversSchema, simStartedSchema, simSummarySchema } from "./sims";
import type { CoreInfo } from "./types";

const FIX = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui");
const fx = (name: string): unknown => JSON.parse(readFileSync(resolve(FIX, name), "utf8"));
const info: CoreInfo = { baseUrl: "http://127.0.0.1:7400", wsUrl: "ws://127.0.0.1:7400/events", token: "tok", owned: false };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}
function clientWith(impl: (url: string, init: RequestInit) => Promise<Response>) {
  const f = vi.fn(impl);
  return { client: new CoreClient(info, f as unknown as typeof fetch), f };
}

describe("sim schemas vs the real core", () => {
  it.each([
    ["sim_done.json", simRecordSchema],
    ["sim_failed.json", simRecordSchema],
    ["sims_list.json", z.array(simSummarySchema)],
    ["sims_solvers.json", simSolversSchema],
    ["sims_solvers_missing.json", simSolversSchema],
    ["sims_start.json", simStartedSchema],
  ] as const)("%s parses", (name, schema) => {
    const r = (schema as z.ZodType<unknown>).safeParse(fx(name));
    expect(r.success, r.success ? "" : JSON.stringify(r.error.issues.slice(0, 3))).toBe(true);
  });

  it("a done record carries the guide labelling and the fixture-including safety factor", () => {
    const rec = simRecordSchema.parse(fx("sim_done.json"));
    expect(rec.guide).toBe(true);
    expect(rec.label).toBe("guide only, not a guarantee");
    expect(rec.solver?.name).toBe("CalculiX");
    expect(rec.results?.safety_factor_incl_fixture).toBeTypeOf("number");
    expect(rec.results?.peak_excludes).toBe("nodes on the fixed face");
  });
});

describe("CoreClient sim routes", () => {
  it("lists studies of a part and of a run", async () => {
    const { client, f } = clientWith(async () => json(fx("sims_list.json")));
    const list = await client.listSims({ partId: "mount_plate" });
    expect(list[0].sim_id).toMatch(/^sim_/);
    expect(f.mock.calls[0][0]).toBe("http://127.0.0.1:7400/sims?part_id=mount_plate");
    await client.listSims({ runId: "abc" });
    expect(f.mock.calls[1][0]).toBe("http://127.0.0.1:7400/sims?run_id=abc");
  });

  it("reads one study and the solver status", async () => {
    const { client, f } = clientWith(async (url) => json(url.endsWith("/solvers") ? fx("sims_solvers.json") : fx("sim_done.json")));
    expect((await client.getSim("sim_4e4ae12d0fbd")).status).toBe("done");
    expect(f.mock.calls[0][0]).toBe("http://127.0.0.1:7400/sims/sim_4e4ae12d0fbd");
    expect((await client.simSolvers()).ccx.found).toBe(true);
  });

  it("starts a study (202) with exactly the setup body", async () => {
    const { client, f } = clientWith(async () => json({ run_id: "r1", status: "running" }, 202));
    const body = { part_id: "mount_plate", fix: "x-min", load: { face: "x-max", newtons: 20, direction: "-z" as const }, material: "PLA", environment: null };
    expect(await client.startSim(body)).toEqual({ run_id: "r1", status: "running" });
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:7400/sims");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual(body);
  });

  it("an unbuilt part is a not_found CoreError", async () => {
    const { client } = clientWith(async () => json({ error: "not_found", message: "no built part 'x'", hint: null, retryable: false }, 404));
    const e = await client.startSim({ part_id: "xx", fix: "bottom", load: { face: "top", newtons: 1, direction: "-z" } }).catch((x: unknown) => x);
    expect(e).toBeInstanceOf(CoreError);
    expect((e as CoreError).code).toBe("not_found");
  });

  it("fetches the heatmap GLB with ETag revalidation", async () => {
    const { client, f } = clientWith(async (_u, init) => {
      if (new Headers(init.headers).get("if-none-match") === '"e1"') return new Response(null, { status: 304 });
      return new Response(new Uint8Array([1, 2, 3]), { status: 200, headers: { etag: '"e1"' } });
    });
    const first = await client.simGlb("sim_4e4ae12d0fbd");
    expect(first).not.toBe("not-modified");
    expect(f.mock.calls[0][0]).toBe("http://127.0.0.1:7400/sims/sim_4e4ae12d0fbd/result.glb");
    expect(await client.simGlb("sim_4e4ae12d0fbd", '"e1"')).toBe("not-modified");
  });
});
