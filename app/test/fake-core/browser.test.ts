// @vitest-environment node
// Fake core browser/BOM behaviour (the shapes themselves are checked against the real core in
// test/contract.browser.test.ts): summary vs full lists, cards, the proxied image, the BOM's
// core rules (extra fields refused, whole-number qty, unknown currency kept unknown,
// per-currency totals), the formula-safe CSV, and the sites routes.
import { afterEach, describe, expect, it } from "vitest";
import { loadScenario, startFakeCore, type FakeCore } from "./server";

let core: FakeCore;
const sc = () => loadScenario("parts-list");
const RUN = sc().runs[0].id;
const PROJ = sc().browser!.bom![0].project_id;

async function start() { core = await startFakeCore({ port: 0, token: "t", scenario: sc() }); }
/** The dumped build held only the dumped rows: base's own mount plate left out (its totals). */
async function startDumped() { core = await startFakeCore({ port: 0, token: "t", scenario: { ...sc(), parts: [] } }); }
async function req(method: string, path: string, body?: unknown) {
  const r = await fetch(core.url + path, {
    method, headers: { Authorization: "Bearer t", ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const ct = r.headers.get("content-type") ?? "";
  const text = await r.text();
  return { status: r.status, ct, json: ct.includes("json") ? JSON.parse(text) : null, text };
}
afterEach(async () => { await core.stop(); });

describe("fake core: agent browser (R3)", () => {
  it("lists summary rows by default and whole snapshots with ?full=1", async () => {
    await start();
    const rows = (await req("GET", `/runs/${RUN}/browser`)).json;
    expect(rows.map((r: { kind: string }) => r.kind)).toEqual(["search", "page", "page"]);
    expect("text" in rows[0]).toBe(false);
    expect(rows[1].has_part).toBe(true);
    const full = (await req("GET", `/runs/${RUN}/browser?full=1`)).json;
    expect(full[1].text).toContain("Ignore all previous");
    expect((await req("GET", "/runs/nope/browser")).status).toBe(404);
  });

  it("serves a snapshot, its proxied image as PNG, and part cards", async () => {
    await start();
    const sid = sc().browser!.snapshots![1].id;
    expect((await req("GET", `/browser/snapshots/${sid}`)).json.id).toBe(sid);
    const img = await req("GET", `/browser/snapshots/${sid}/images/0`);
    expect(img.status).toBe(200);
    expect(img.ct).toBe("image/png");
    expect((await req("GET", `/browser/snapshots/${sid}/images/9`)).status).toBe(404);
    const cards = (await req("GET", `/runs/${RUN}/part_cards`)).json;
    expect(cards).toHaveLength(2);
    expect((await req("GET", `/browser/cards/${cards[0].id}`)).json.name).toBe(cards[0].name);
    expect((await req("GET", "/browser/cards/card_0000000000000000")).status).toBe(404);
  });

  it("restores the default sites into the profile", async () => {
    await start();
    await req("PUT", "/profile", { suggested_sites: [] });
    expect((await req("GET", "/profile")).json.suggested_sites).toEqual([]);
    const restored = (await req("POST", "/browser/sites/restore_defaults")).json;
    expect(restored.length).toBe(18);
    expect((await req("GET", "/profile")).json.suggested_sites).toEqual(restored);
    expect((await req("GET", "/browser/sites/defaults")).json).toEqual(restored);
  });
});

describe("fake core: parts list (R5)", () => {
  it("totals per currency, never summed across currencies", async () => {
    await startDumped();
    const t = (await req("GET", `/projects/${PROJ}/bom`)).json.totals;
  // P10 E15: the dumped part_show inserts the servo, so its auto row adds $5.95
    expect(t.by_currency).toEqual({ USD: 41.92, EUR: 2.4, unknown: 3 });
    expect(t.unpriced).toBe(2);
    expect(t.has_estimates).toBe(true);
  });

  it("refuses an extra field, a fractional qty and a bad currency, like the core", async () => {
    await start();
    const extra = await req("POST", `/projects/${PROJ}/bom`, { name: "x", snapshot_id: "s" });
    expect(extra.status).toBe(400);
    expect(extra.json.message).toBe("invalid parts list row: snapshot_id: Extra inputs are not permitted");
    expect((await req("POST", `/projects/${PROJ}/bom`, { name: "x", source: "user" })).status).toBe(400);
    expect((await req("POST", `/projects/${PROJ}/bom`, { name: "x", qty: 1.5 })).status).toBe(400);
    expect((await req("POST", `/projects/${PROJ}/bom`, { name: "x", currency: "usd" })).status).toBe(400);
    expect((await req("POST", `/projects/${PROJ}/bom`, { name: "" })).status).toBe(400);
  });

  it("a row with no currency stays unknown; PATCH can set one; a made estimate comes back on unit_cost null", async () => {
    await start();
    const added = (await req("POST", `/projects/${PROJ}/bom`, { name: "Nut", unit_cost: 0.1, qty: 10 })).json;
    expect(added.source).toBe("user");
    expect(added.currency).toBeNull();
    expect(added.id).toMatch(/^bom_[0-9a-f]{12}$/);
    const set = (await req("PATCH", `/projects/${PROJ}/bom/${added.id}`, { currency: "EUR" })).json;
    expect(set.currency).toBe("EUR");
    const kept = (await req("PATCH", `/projects/${PROJ}/bom/${added.id}`, { currency: null })).json;
    expect(kept.currency).toBe("EUR");                              // not nullable on PATCH, like the core
    const auto = (await req("GET", `/projects/${PROJ}/bom`)).json.items.find((r: { source: string }) => r.source === "auto");
    const priced = (await req("PATCH", `/projects/${PROJ}/bom/${auto.id}`, { unit_cost: 2 })).json;
    expect(priced.estimate).toBe(false);
    expect(priced.source).toBe("user");
    const back = (await req("PATCH", `/projects/${PROJ}/bom/${auto.id}`, { unit_cost: null })).json;
    expect(back.estimate).toBe(true);
    expect(back.unit_cost).toBe(auto.unit_cost);
  });

  it("every part of the build is an automatic made row with its own mass and estimate, like the core (FW-app M8)", async () => {
    await start();
    const items = (await req("GET", `/projects/${PROJ}/bom`)).json.items as { id: string; kind: string; source: string; qty: number; unit_cost: number; estimate: boolean; estimate_note: string; specs: { key: string; value: unknown }[] }[];
    const plate = items.find((r) => r.id === "auto_mount_plate");
    expect(plate).toMatchObject({ kind: "made", source: "auto", qty: 1, estimate: true });
    const mass = plate?.specs.find((s) => s.key === "mass")?.value as number;
    expect(mass).toBeGreaterThan(0);
    expect(plate?.unit_cost).toBeCloseTo(Math.round(mass / 1000 * 22 * 100) / 100);
    expect(plate?.estimate_note).toMatch(/^estimate: [\d.]+ g of PLA solid \(100% infill\) x \$22\.00\/kg \(material only\)$/);
    // hidden on delete (it would come back otherwise), like the core
    expect((await req("DELETE", `/projects/${PROJ}/bom/auto_mount_plate`)).status).toBe(200);
    expect((await req("GET", `/projects/${PROJ}/bom`)).json.items.some((r: { id: string }) => r.id === "auto_mount_plate")).toBe(false);
  });

  it("deleting a part's row hides it; a stored row goes away", async () => {
    await start();
    const items = (await req("GET", `/projects/${PROJ}/bom`)).json.items;
    for (const it of items) expect((await req("DELETE", `/projects/${PROJ}/bom/${it.id}`)).status).toBe(200);
    expect((await req("GET", `/projects/${PROJ}/bom`)).json.items).toEqual([]);
    expect((await req("DELETE", `/projects/${PROJ}/bom/bom_000000000000`)).status).toBe(404);
  });

  it("the CSV neutralizes a formula-like name and lists one total row per currency", async () => {
    await startDumped();
    const csv = (await req("GET", `/projects/${PROJ}/bom.csv`)).text;
    expect(csv).toContain(`"'=HYPERLINK(""https://evil.example"")"`);
    const totals = csv.split("\r\n").filter((l) => l.startsWith("total,"));
    expect(totals).toEqual(["total,,,,USD,41.92,yes,,,,,,,", "total,,,,EUR,2.40,,,,,,,,", "total,,,,unknown,3.00,,,,,,,,"]);
  });
});
