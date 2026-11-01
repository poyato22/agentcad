// @vitest-environment node
// Contract (Plan 7 U4a ↔ U4c): the real core's browser / part card / BOM / sites answers
// (core/scripts/dump_browser_bom_fixtures.py → core/tests/fixtures/ui/browser) ↔ the app's
// schemas ↔ the fake core. The fake runs the "parts-list" scenario, which is built from the same
// dump (scenarios/make_parts_list.py), so every answer can be compared key for key and type for
// type; the CSV is compared cell for cell (formula neutralizing, money format, total rows).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { z } from "zod";
import {
  bomRowSchema, bomSchema, browserListSchema, browserSnapshotSchema, partCardSchema, partShowResultSchema, suggestedSitesSchema,
} from "../src/api/browser";
import { profileSchema } from "../src/api/schemas";
import { BROWSER_FIXTURES } from "./fake-core/browser";
import { loadScenario, startFakeCore, type FakeCore } from "./fake-core/server";
import { shapeDiff } from "./shape";

const real = (name: string): unknown => JSON.parse(readFileSync(resolve(BROWSER_FIXTURES, name), "utf8"));
const realText = (name: string): string => readFileSync(resolve(BROWSER_FIXTURES, name), "utf8");

let core: FakeCore;
const SC = loadScenario("parts-list");
const RUN = SC.runs[0].id;
const PROJ = SC.browser!.bom![0].project_id;
const SID = SC.browser!.snapshots!.find((s) => s.kind === "page" && !s.blocked)!.id;
const SEARCH = SC.browser!.snapshots!.find((s) => s.kind === "search")!.id;
const CARD = SC.browser!.cards![0].id;

beforeAll(async () => { core = await startFakeCore({ port: 0, token: "t", scenario: SC }); });
afterAll(async () => { await core.stop(); });

async function fake(method: string, path: string, body?: unknown): Promise<{ status: number; json: unknown; text: string }> {
  const r = await fetch(core.url + path, {
    method, headers: { Authorization: "Bearer t", ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await r.text();
  return { status: r.status, json: (r.headers.get("content-type") ?? "").includes("json") ? JSON.parse(text) : null, text };
}

const CASES: { fixture: string; schema: z.ZodType<unknown> | null; get: () => Promise<unknown> }[] = [
  { fixture: "browser_list.json", schema: null, get: async () => (await fake("GET", `/runs/${RUN}/browser`)).json },
  { fixture: "browser_list_full.json", schema: browserListSchema, get: async () => (await fake("GET", `/runs/${RUN}/browser?full=1`)).json },
  { fixture: "browser_snapshot.json", schema: browserSnapshotSchema, get: async () => (await fake("GET", `/browser/snapshots/${SID}`)).json },
  { fixture: "browser_search_snapshot.json", schema: browserSnapshotSchema, get: async () => (await fake("GET", `/browser/snapshots/${SEARCH}`)).json },
  { fixture: "browser_card.json", schema: partCardSchema, get: async () => (await fake("GET", `/browser/cards/${CARD}`)).json },
  { fixture: "browser_cards.json", schema: null, get: async () => (await fake("GET", `/runs/${RUN}/part_cards`)).json },
  { fixture: "bom.json", schema: bomSchema, get: async () => (await fake("GET", `/projects/${PROJ}/bom`)).json },
  { fixture: "sites_defaults.json", schema: suggestedSitesSchema, get: async () => (await fake("GET", "/browser/sites/defaults")).json },
  { fixture: "profile_browser.json", schema: profileSchema, get: async () => (await fake("GET", "/profile")).json },
];

describe("contract: real core browser/BOM answers ↔ app schemas ↔ fake core", () => {
  for (const c of CASES) {
    describe(c.fixture, () => {
      if (c.schema) {
        it("the app schema parses the real core's answer", () => {
          const p = c.schema!.safeParse(real(c.fixture));
          expect(p.success, p.success ? "" : JSON.stringify(p.error.issues.slice(0, 3))).toBe(true);
        });
      }
      it("the fake core's answer has the real shape", async () => {
        const want = real(c.fixture);
        const got = await c.get();
        // snapshot lists pair element by element (in visit order): shapeDiff would pair by `kind`
        // (its step discriminator) and match the blocked page against a plain one
        if (Array.isArray(want) && /^browser_list/.test(c.fixture)) {
          expect(Array.isArray(got) && got.length).toBe(want.length);
          want.forEach((w, i) => expect(shapeDiff(w, (got as unknown[])[i], `${c.fixture}[${i}]`)).toEqual([]));
          return;
        }
        expect(shapeDiff(want, got, c.fixture)).toEqual([]);
      });
    });
  }

  it("part_show's real result is ids only, and its card_id resolves to a card", () => {
    const r = partShowResultSchema.parse(real("part_show_result.json"));
    // P10 E15: plus where the part landed — the build part and its parts-list row (the
    // card's "In build" / "In parts list" chips)
    expect(Object.keys(real("part_show_result.json") as object).sort()).toEqual(
      ["bom_id", "card", "card_id", "catalog_id", "item_id", "part_id", "snapshot_id", "untrusted_page_data"]);
    expect(partCardSchema.parse(real("browser_card.json")).id).toBe(r.card_id);
    expect(r.part_id).toBeTruthy();
    expect(r.bom_id).toBe(`auto_${r.part_id}`);
  });

  it("POST / PATCH answers and the extra-field refusal match the real core", async () => {
    const add = await fake("POST", `/projects/${PROJ}/bom`, { kind: "bought", name: "Micro servo SG92R", qty: 4, unit_cost: 5.95, currency: "USD",
      vendor: "Adafruit", url: "https://www.adafruit.com/product/169", specs: [{ key: "mass", value: 9, unit: "g" }] });
    expect(bomRowSchema.safeParse(add.json).success).toBe(true);
    expect(shapeDiff(real("bom_add.json"), add.json, "bom_add")).toEqual([]);
    const id = (add.json as { id: string }).id;
    const patched = await fake("PATCH", `/projects/${PROJ}/bom/${id}`, { qty: 6, notes: "two spares" });
    expect(shapeDiff(real("bom_patch.json"), patched.json, "bom_patch")).toEqual([]);
    const refused = real("bom_add_extra_field.json") as { status: number; body: unknown };
    const bad = await fake("POST", `/projects/${PROJ}/bom`, { name: "x", snapshot_id: SID });
    expect(bad.status).toBe(refused.status);
    expect(bad.json).toEqual(refused.body);
    await fake("DELETE", `/projects/${PROJ}/bom/${id}`);
  });

  it("restore_defaults answers like the real core", async () => {
    const r = await fake("POST", "/browser/sites/restore_defaults");
    expect(r.json).toEqual(real("sites_restore.json"));
  });

  it("the fake core's CSV matches the real one cell for cell", async () => {
    // (spec values the core printed from Python floats, "9.0", read back from JSON as 9: the
    // specs column is compared without them)
    const norm = (csv: string) => csv.split("\r\n").map((l) => l.replace(/(\d+)\.0( |;|,|$)/g, "$1$2"));
    // the dumped build held only the dumped rows: the scenario's own mount plate (an automatic
    // made row, like the core's) is hidden first so the two lists match (FW-app M8)
    expect((await fake("DELETE", `/projects/${PROJ}/bom/auto_mount_plate`)).status).toBe(200);
    expect(norm((await fake("GET", `/projects/${PROJ}/bom.csv`)).text)).toEqual(norm(realText("bom.csv")));
  });
});
