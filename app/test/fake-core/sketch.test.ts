// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { CoreClient, CoreError } from "../../src/api/client";
import type { SketchUpload } from "../../src/api/types";
import { startFakeCore, type FakeCore } from "./server";

let core: FakeCore;
let client: CoreClient;

// 3 x 2 px PNG header (enough for the IHDR read)
const PNG = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13]), Buffer.from("IHDR"), Buffer.from([0, 0, 0x0f, 0xa0, 0, 0, 0x03, 0xe8])]);
const SKETCH: SketchUpload["sketch"] = {
  units: "mm", width_mm: 200, height_mm: 150, notes: "",
  strokes: [{ id: "s1", kind: "line", points: [[10, 10], [50, 10]], closed: false, label: "" }],
  dimensions: [{ stroke_id: "s1", text: "40", value_mm: 40, kind: "length" }],
};

async function failure(p: Promise<unknown>): Promise<CoreError> {
  const e = await p.then(() => null, (err: unknown) => err);
  if (!(e instanceof CoreError)) throw new Error(`expected a CoreError, got ${String(e)}`);
  return e;
}

beforeEach(async () => {
  core = await startFakeCore({ scenario: "base", token: "tok" });
  client = new CoreClient({ baseUrl: core.url, wsUrl: core.url.replace("http", "ws") + "/events", token: "tok", owned: false });
});
afterEach(async () => { await core.stop(); });

describe("fake core sketch routes", () => {
  it("stores a sketch and starts a run with it", async () => {
    const s = await client.postSketch({ sketch: SKETCH, photo: { data_b64: PNG.toString("base64"), name: "a.png" } });
    expect(s).toMatchObject({ strokes: 1, dimensions: 1, render: false, photo: { source_type: "png", width: 1568, height: 392 } });
    expect(s.sketch_id).toMatch(/^sk_[0-9a-f]{12}$/);
    expect(await client.getSketch(s.sketch_id)).toEqual(s);
    const { run_id } = await client.createRun({ agent: "designer", prompt: "Build the part shown in my sketch.", sketch_id: s.sketch_id });
    const steps = await client.steps(run_id);
    expect(steps[0].payload).toMatchObject({ role: "user", context: { sketch: { sketch_id: s.sketch_id } } });
  });

  it("answers like the core for bad photos, unknown sketches and bad ids", async () => {
    let e = await failure(client.postSketch({ sketch: SKETCH, photo: { data_b64: Buffer.from("GIF89a..").toString("base64") } }));
    expect([e.status, e.code, e.message]).toEqual([400, "invalid_input", "the photo must be a PNG, JPEG or HEIC image"]);
    const big = Buffer.concat([PNG, Buffer.alloc(10 * 1024 * 1024)]);
    e = await failure(client.postSketch({ sketch: SKETCH, photo: { data_b64: big.toString("base64") } }));
    expect(e.message).toBe("the photo is larger than 10 MB");
    e = await failure(client.postSketch({ sketch: { ...SKETCH, dimensions: [{ stroke_id: "zz", text: "1", value_mm: 1, kind: "length" }] } }));
    expect([e.status, e.code]).toEqual([422, "validation"]);
    e = await failure(client.createRun({ agent: "designer", prompt: "x", sketch_id: "sk_000000000000" }));
    expect([e.status, e.code]).toEqual([404, "not_found"]);
    e = await failure(client.createRun({ agent: "designer", prompt: "x", sketch_id: "../x" }));
    expect(e.status).toBe(422);
    expect([...core.state.runs.values()].some((r) => r.task === "x")).toBe(false);
  });
});
