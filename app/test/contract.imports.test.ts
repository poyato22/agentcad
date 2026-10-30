// @vitest-environment node
// Contract test, Plan 7 U5: the drawing import routes. The real core's answers
// (core/scripts/dump_import_fixtures.py → core/tests/fixtures/ui) parse with the app's schemas;
// the fake core answers the same calls with the same shapes.
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { drawingImportSchema } from "../src/api/imports";
import { coreErrorBodySchema } from "../src/api/schemas";
import { startFakeCore, type FakeCore } from "./fake-core/server";
import { shapeDiff, type Json } from "./shape";

const HERE = import.meta.dirname;
const DIR = process.env.MISES_CONTRACT_FIXTURES ? resolve(process.env.MISES_CONTRACT_FIXTURES) : resolve(HERE, "../../core/tests/fixtures/ui");
const DRAWINGS = resolve(HERE, "../../core/tests/fixtures/drawings");
const read = (name: string): unknown => JSON.parse(readFileSync(join(DIR, name), "utf8"));

let core: FakeCore;
const fake = new Map<string, unknown>();
let originalType = "";
let originalCsp = "";

async function upload(name: string, bytes: Buffer): Promise<{ status: number; json: unknown }> {
  const form = new FormData();
  form.append("file", new Blob([new Uint8Array(bytes)]), name);
  const r = await fetch(`${core.url}/imports/drawing`, { method: "POST", headers: { Authorization: `Bearer ${core.token}` }, body: form });
  return { status: r.status, json: await r.json() };
}
async function get(path: string): Promise<Response> {
  return fetch(core.url + path, { headers: { Authorization: `Bearer ${core.token}` } });
}

beforeAll(async () => {
  core = await startFakeCore({ port: 0, token: "contract", scenario: "base" });
  // the same calls, in the same order, as the dump script
  const up = await upload("Mounting Plate.dxf", readFileSync(join(DRAWINGS, "plate_holes.dxf")));
  expect(up.status).toBe(200);
  fake.set("drawing_import_upload.json", up.json);
  const id = (up.json as { import_id: string }).import_id;
  let done: unknown = null;
  for (let i = 0; i < 20; i++) {
    done = await (await get(`/imports/${id}`)).json();
    if ((done as { status: string }).status === "done") break;
  }
  fake.set("drawing_import_done.json", done);
  const o = await get(`/imports/${id}/original`);
  originalType = o.headers.get("content-type") ?? "";
  originalCsp = o.headers.get("content-security-policy") ?? "";
  const refused = await upload("novision.png", readFileSync(join(DRAWINGS, "plate_drawing.png")));
  expect(refused.status).toBe(400);
  fake.set("drawing_import_refused.json", refused.json);
});
afterAll(async () => { await core.stop(); });

describe("contract: drawing import ↔ schemas ↔ fake core", () => {
  for (const name of ["drawing_import_upload.json", "drawing_import_done.json"]) {
    it(`${name}: the real core's answer parses; the fake's has the same shape`, () => {
      const real = drawingImportSchema.safeParse(read(name));
      expect(real.success, real.success ? "" : JSON.stringify(real.error.issues.slice(0, 5))).toBe(true);
      expect(drawingImportSchema.safeParse(fake.get(name)).success).toBe(true);
      expect(shapeDiff(read(name) as Json, fake.get(name) as Json, name)).toEqual([]);
    });
  }

  it("the real DXF import ends done with every dimension matching", () => {
    const done = drawingImportSchema.parse(read("drawing_import_done.json"));
    expect(done.status).toBe("done");
    expect(done.result).toBe("match");
    expect(done.part_id).toBe("mounting_plate");
    expect(done.table?.rows.map((r) => r.kind)).toEqual(expect.arrayContaining(["length", "width", "diameter", "callout"]));
  });

  it("no vision model: the same error body (message names the problem, hint names the models)", () => {
    const real = coreErrorBodySchema.parse(read("drawing_import_refused.json"));
    const got = coreErrorBodySchema.parse(fake.get("drawing_import_refused.json"));
    expect(got).toEqual(real);
    expect(real.message).toContain("cannot read images");
    expect(real.hint).toContain("DXF drawings need no model");
  });

  it("the original drawing of a DXF is an SVG served with a no-script CSP", () => {
    expect(originalType).toContain("image/svg+xml");
    expect(originalCsp).toBe("default-src 'none'; style-src 'unsafe-inline'");
    const svg = readFileSync(join(DIR, "drawing_import_original.svg"), "utf8");
    expect(svg.startsWith("<svg")).toBe(true);
    expect(svg).not.toContain("<script");
  });
});
