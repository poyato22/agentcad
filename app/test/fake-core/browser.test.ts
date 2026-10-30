// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { loadScenario, startFakeCore, type FakeCore, type Scenario } from "./server";

let core: FakeCore;

async function start(browser?: Scenario["browser"]) {
  core = await startFakeCore({ port: 0, token: "t", scenario: { ...loadScenario("base"), browser } });
}
async function req(method: string, path: string, body?: unknown) {
  const r = await fetch(core.url + path, {
    method, headers: { Authorization: "Bearer t", ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const ct = r.headers.get("content-type") ?? "";
  return { status: r.status, ct, json: ct.includes("json") ? await r.json() : null, text: ct.includes("csv") || ct.includes("image") ? await r.text() : null, bytes: Number(r.headers.get("content-length") ?? 0) };
}

afterEach(async () => { await core.stop(); });
beforeEach(() => {});

describe("fake core: agent browser (R3)", () => {
  it("lists a run's snapshots, serves one, and proxies its image as PNG", async () => {
    await start();
    const list = await req("GET", "/runs/r_lead/browser");
    expect(list.status).toBe(200);
    expect(list.json).toHaveLength(1);
    expect(list.json[0].title).toBe("Falcon 2207 brushless motor");
    expect(list.json[0].images[0].proxy_url).toBe("/browser/snapshots/snap_falcon/images/0");

    const one = await req("GET", "/browser/snapshots/snap_falcon");
    expect(one.json.url).toBe("https://servocity.com/falcon-2207");

    const img = await req("GET", "/browser/snapshots/snap_falcon/images/0");
    expect(img.status).toBe(200);
    expect(img.ct).toBe("image/png");
    expect(img.bytes).toBeGreaterThan(0);
  });

  it("unknown snapshot / image → 404", async () => {
    await start();
    expect((await req("GET", "/browser/snapshots/nope")).status).toBe(404);
    expect((await req("GET", "/browser/snapshots/snap_falcon/images/9")).status).toBe(404);
  });
});

describe("fake core: parts list / BOM (R5)", () => {
  it("returns lines with recomputed totals (made is an estimate)", async () => {
    await start();
    const r = await req("GET", "/projects/b_default/bom");
    expect(r.json.lines).toHaveLength(2);
    // 2*0.84 made + 4*24.5 bought = 1.68 + 98 = 99.68
    expect(r.json.totals.amount).toBeCloseTo(99.68, 2);
    expect(r.json.totals.made).toBeCloseTo(1.68, 2);
    expect(r.json.totals.estimated).toBe(true);
    const bracket = r.json.lines.find((l: { id: string }) => l.id === "bom_bracket");
    expect(bracket.estimated).toBe(true);
    expect(r.json.totals.by_vendor.find((v: { vendor: string | null }) => v.vendor === "ServoCity").amount).toBeCloseTo(98, 2);
  });

  it("adds, edits and deletes a line and exports CSV", async () => {
    await start({ bom: [] });
    const added = await req("POST", "/projects/b_default/bom", { kind: "bought", name: "M3 screw", qty: 10, unit_cost: 0.1, vendor: "McMaster-Carr" });
    expect(added.json.source).toBe("user");
    const id = added.json.id;

    const patched = await req("PATCH", `/projects/b_default/bom/${id}`, { qty: 20, notes: "pan head" });
    expect(patched.json.qty).toBe(20);
    expect(patched.json.notes).toBe("pan head");

    const total = await req("GET", "/projects/b_default/bom");
    expect(total.json.totals.amount).toBeCloseTo(2, 2);

    const csv = await req("GET", "/projects/b_default/bom.csv");
    expect(csv.ct).toContain("text/csv");
    expect(csv.text).toContain("M3 screw");

    expect((await req("DELETE", `/projects/b_default/bom/${id}`)).status).toBe(200);
    expect((await req("GET", "/projects/b_default/bom")).json.lines).toHaveLength(0);
    expect((await req("PATCH", "/projects/b_default/bom/gone", { qty: 1 })).status).toBe(404);
  });
});

describe("fake core: suggested sites in the profile (R3)", () => {
  it("GET /profile ships defaults; PUT round-trips a user list", async () => {
    await start();
    const prof = await req("GET", "/profile");
    expect(prof.json.suggested_sites.length).toBeGreaterThan(0);
    expect(prof.json.suggested_sites[0].suggested).toBe(true);

    const sites = [{ name: "Pololu", domain: "pololu.com", search_url: "https://www.pololu.com/search?query={q}", categories: ["motors"], suggested: false }];
    const put = await req("PUT", "/profile", { suggested_sites: sites });
    expect(put.json.suggested_sites).toEqual(sites);

    const bad = await req("PUT", "/profile", { suggested_sites: [{ name: "x" }] });
    expect(bad.status).toBe(422);
  });
});
