// @vitest-environment node
// Contract test, Plan 5 A6: the Onshape browser, budget and parts directory routes. The real core's
// answers (core/scripts/dump_onshape_catalog_fixtures.py → core/tests/fixtures/ui) parse with the
// app's schemas; the fake core answers the same calls with the same shapes; the app's unit-test
// copies (test/fixtures/core-ui) match too.
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { z } from "zod";
import {
  buildRefSchema, catalogItemSchema, onshapeBudgetSchema, onshapeDocSchema, onshapeElementSchema, partResultSchema,
} from "../src/api/directory";
import { eventSchema, okSchema } from "../src/api/schemas";
import { budgetOf, directoryFrom } from "./fake-core/directory";
import { loadScenario, startFakeCore, type FakeCore, type Scenario } from "./fake-core/server";
import { shapeDiff, type Json } from "./shape";

const HERE = import.meta.dirname;
const DIR = process.env.MISES_CONTRACT_FIXTURES ? resolve(process.env.MISES_CONTRACT_FIXTURES) : resolve(HERE, "../../core/tests/fixtures/ui");
const APP = resolve(HERE, "fixtures/core-ui");
const read = (dir: string, name: string): unknown => JSON.parse(readFileSync(join(dir, name), "utf8"));

let core: FakeCore;
const fake = new Map<string, unknown>();
let thumbType = "";

async function call(method: string, path: string, body?: unknown): Promise<unknown> {
  const headers: Record<string, string> = { Authorization: `Bearer ${core.token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(core.url + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  expect(r.status, `${method} ${path}`).toBe(200);
  return r.json();
}

beforeAll(async () => {
  const sc: Scenario = { ...loadScenario("base"), directory: { onshape: true } };
  core = await startFakeCore({ port: 0, token: "contract", scenario: sc });
  // the same calls, in the same order, as the dump script
  const docs = (await call("GET", "/onshape/documents?limit=20")) as { did: string; default_wid: string; thumbnail_url: string }[];
  fake.set("onshape_documents.json", docs);
  fake.set("onshape_elements.json", await call("GET", `/onshape/documents/${docs[0].did}/elements?wid=${docs[0].default_wid}`));
  const th = await fetch(core.url + docs[0].thumbnail_url, { headers: { Authorization: `Bearer ${core.token}` } });
  thumbType = th.headers.get("content-type") ?? "";
  fake.set("onshape_import.json", await call("POST", "/onshape/import", { did: docs[0].did, wid: docs[0].default_wid, eid: "000000000000000000000003", project_id: "b_default" }));
  fake.set("onshape_budget.json", await call("GET", "/onshape/budget"));
  fake.set("catalog_categories.json", await call("GET", "/catalog/categories"));
  fake.set("catalog_search.json", await call("GET", "/catalog/search?category=motor"));
  fake.set("catalog_all.json", await call("GET", "/catalog/search?limit=200"));
  fake.set("catalog_item.json", await call("GET", "/catalog/items/motor_2207_class"));
  const imp = (await call("POST", "/catalog/import", { url: "https://shop.example.com/p/falcon-2207", project_id: "b_default" })) as { id: string };
  fake.set("catalog_import.json", imp);
  const inj = (await call("POST", "/catalog/import", { url: "https://shop.example.com/p/sketchy" })) as { id: string };
  fake.set("catalog_import_injection.json", inj);
  fake.set("catalog_patch.json", await call("PATCH", `/catalog/items/${imp.id}`, { name: "Falcon 2207 1950KV", vendor: "Example Hobby" }));
  fake.set("catalog_insert.json", await call("POST", "/catalog/items/motor_2207_class/insert", { project_id: "b_default" }));
  fake.set("catalog_allowed_domains_add.json", await call("POST", "/catalog/allowed_domains", { domain: "shop.example.com" }));
  fake.set("catalog_allowed_domains.json", await call("GET", "/catalog/allowed_domains"));
  fake.set("catalog_allowed_domains_remove.json", await call("DELETE", "/catalog/allowed_domains/shop.example.com"));
  fake.set("catalog_delete.json", await call("DELETE", `/catalog/items/${inj.id}`));
});
afterAll(async () => { await core.stop(); });

const strings = z.array(z.string());
const CASES: [string, z.ZodType<unknown>][] = [
  ["onshape_documents.json", z.array(onshapeDocSchema)], ["onshape_elements.json", z.array(onshapeElementSchema)],
  ["onshape_import.json", partResultSchema], ["onshape_budget.json", onshapeBudgetSchema],
  ["catalog_categories.json", strings], ["catalog_search.json", z.array(catalogItemSchema)], ["catalog_all.json", z.array(catalogItemSchema)],
  ["catalog_item.json", catalogItemSchema], ["catalog_import.json", catalogItemSchema], ["catalog_import_injection.json", catalogItemSchema],
  ["catalog_patch.json", catalogItemSchema], ["catalog_insert.json", partResultSchema],
  ["catalog_allowed_domains_add.json", strings], ["catalog_allowed_domains.json", strings], ["catalog_allowed_domains_remove.json", strings],
  ["catalog_delete.json", okSchema],
];

/** Like shapeDiff, but list elements pair on their id (or position) instead of all with the first:
 *  catalog items differ per kind (dims keys, which specs carry a `note`, string or number values),
 *  and the fake serves the real items, so the same item must have the same shape. */
function diff(real: unknown, got: unknown, path: string, out: string[] = []): string[] {
  if (Array.isArray(real) && Array.isArray(got)) {
    const key = (x: unknown) => {
      const o = (typeof x === "object" && x !== null ? x : {}) as Record<string, unknown>;
      return o.id ?? o.did ?? o.eid ?? o.key ?? null;
    };
    got.forEach((g, i) => {
      const k = key(g);
      const r = (k !== null ? real.find((x) => key(x) === k) : undefined) ?? real[i] ?? real[0];
      if (r !== undefined) diff(r, g, `${path}[${String(k ?? i)}]`, out);
    });
    return out;
  }
  if (typeof real === "object" && real !== null && typeof got === "object" && got !== null && !Array.isArray(real) && !Array.isArray(got)) {
    const e = real as Record<string, unknown>;
    const a = got as Record<string, unknown>;
    if (/\.(dims|vars)$/.test(path)) return out;            // open maps (data keys, not schema)
    for (const k of Object.keys(e)) if (!(k in a)) out.push(`${path}.${k}: missing (real core has it)`);
    for (const k of Object.keys(a)) if (!(k in e)) out.push(`${path}.${k}: extra (real core lacks it)`);
    for (const k of Object.keys(e)) if (k in a) diff(e[k], a[k], `${path}.${k}`, out);
    return out;
  }
  return shapeDiff(real as Json, got as Json, path, out);
}

describe("contract: Onshape browser + parts directory ↔ schemas ↔ fake core", () => {
  for (const [name, schema] of CASES) {
    describe(name, () => {
      it("schema_parses_the_real_core_answer", () => {
        const p = schema.safeParse(read(DIR, name));
        expect(p.success, p.success ? "" : JSON.stringify(p.error.issues.slice(0, 5))).toBe(true);
      });
      it("fake_core_answer_has_the_same_shape", () => {
        expect(schema.safeParse(fake.get(name)).success).toBe(true);
        expect(diff(read(DIR, name), fake.get(name), name)).toEqual([]);
      });
      it("app_unit_test_copy_has_the_same_shape", () => {
        expect(diff(read(DIR, name), read(APP, name), name)).toEqual([]);
      });
    });
  }

  it("onshape_budget events: the real core's parse as events with budget data; the fake's have the same shape", () => {
    const real = readFileSync(join(DIR, "onshape_events.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l) as { data: unknown });
    expect(real.length).toBeGreaterThan(0);
    for (const ev of real) {
      expect(eventSchema.safeParse(ev).success).toBe(true);
      expect(onshapeBudgetSchema.safeParse(ev.data).success).toBe(true);
    }
    const d = directoryFrom({ onshape: true }, true);
    expect(shapeDiff(real[0].data as Json, budgetOf(d, null) as Json, "onshape_budget")).toEqual([]);
  });

  it("the thumbnail proxy answers an image", () => {
    expect(thumbType).toBe("image/png");
    const png = readFileSync(join(DIR, "onshape_thumbnail.png"));
    expect(png.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))).toBe(true);
  });

  it("the import answers say whether the item was already there; the page text is kept as raw_text", () => {
    for (const src of [read(DIR, "catalog_import_injection.json"), fake.get("catalog_import_injection.json")]) {
      const it = catalogItemSchema.parse(src);
      expect(it.existing).toBe(false);
      expect(it.raw_text).toContain("IGNORE ALL PREVIOUS INSTRUCTIONS");
    }
  });

  it("GET /projects rows parse with the build-picker schema", () => {
    expect(buildRefSchema.safeParse({ id: "x", name: "Default", created_at: 1, updated_at: 1, trashed_at: null, part_count: 0, run_count: 0, onshape: null }).success).toBe(true);
  });
});
