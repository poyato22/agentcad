// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import WebSocket from "ws";
import { loadScenario, startFakeCore, type FakeCore, type Scenario } from "./server";

let core: FakeCore;
let events: { type: string; run_id: string | null; data: Record<string, unknown> }[];
let ws: WebSocket;

async function start(directory: Scenario["directory"]) {
  core = await startFakeCore({ port: 0, token: "t", scenario: { ...loadScenario("base"), directory } });
  events = [];
  ws = new WebSocket(`${core.url.replace("http", "ws")}/events?token=t`);
  ws.on("message", (m) => events.push(JSON.parse(String(m))));
  await new Promise((r) => ws.once("open", r));
}
async function req(method: string, path: string, body?: unknown) {
  const r = await fetch(core.url + path, {
    method, headers: { Authorization: "Bearer t", ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { status: r.status, json: r.headers.get("content-type")?.includes("json") ? await r.json() : null };
}
const settle = () => new Promise((r) => setTimeout(r, 30));
const budgets = () => events.filter((e) => e.type === "onshape_budget");

beforeEach(() => { events = []; });
afterEach(async () => { ws?.close(); await core.stop(); });

describe("fake core: Onshape browser", () => {
  it("not connected: 403 before any call, no budget event", async () => {
    await start({ onshape: false });
    const r = await req("GET", "/onshape/documents");
    expect(r.status).toBe(403);
    expect(r.json.message).toBe("Onshape is not connected");
    await settle();
    expect(budgets()).toEqual([]);
  });

  it("pages by offset and sends one budget event per real request", async () => {
    await start({ onshape: true, extra_docs: 20 });
    const p1 = await req("GET", "/onshape/documents?limit=20");
    const p2 = await req("GET", "/onshape/documents?offset=20&limit=20");
    expect(p1.json).toHaveLength(20);
    expect(p2.json).toHaveLength(3);
    const thumb = (p1.json as { thumbnail_url: string }[])[0].thumbnail_url;
    await fetch(core.url + thumb, { headers: { Authorization: "Bearer t" } });
    await fetch(core.url + thumb, { headers: { Authorization: "Bearer t" } });     // cached: free
    await settle();
    expect(budgets().map((e) => e.data.used_year)).toEqual([1, 2, 3]);
    expect((await req("GET", "/onshape/budget")).json.used_year).toBe(3);
  });

  it("an import costs two calls; the same element again costs only the element list", async () => {
    await start({ onshape: true });
    const docs = (await req("GET", "/onshape/documents")).json as { did: string; default_wid: string }[];
    const body = { did: docs[0].did, wid: docs[0].default_wid, eid: "000000000000000000000003", project_id: "b" };
    expect((await req("POST", "/onshape/import", body)).json.existing).toBe(false);
    expect((await req("POST", "/onshape/import", body)).json.existing).toBe(true);
    await settle();
    expect(budgets()).toHaveLength(1 + 2 + 1);
    expect(events.some((e) => e.type === "part_changed")).toBe(true);
  });
});

describe("fake core: parts directory", () => {
  it("import saves at once; a known link comes back unchanged unless refresh", async () => {
    await start({});
    const url = "https://shop.example.com/p/falcon-2207";
    const a = (await req("POST", "/catalog/import", { url })).json;
    expect(a.existing).toBe(false);
    await req("PATCH", `/catalog/items/${a.id}`, { name: "Mine" });
    const b = (await req("POST", "/catalog/import", { url })).json;
    expect([b.existing, b.name]).toEqual([true, "Mine"]);
    const c = (await req("POST", "/catalog/import", { url, refresh: true })).json;
    expect([c.existing, c.name === "Mine"]).toEqual([false, false]);
  });

  it("refuses http, page-text edits and deleting built-ins, like the core", async () => {
    await start({});
    expect((await req("POST", "/catalog/import", { url: "http://shop.example.com/p" })).json.message).toBe("only https links can be imported");
    const a = (await req("POST", "/catalog/import", { url: "https://shop.example.com/p/sketchy" })).json;
    expect((await req("PATCH", `/catalog/items/${a.id}`, { raw_text: "x" })).status).toBe(400);
    expect((await req("PATCH", "/catalog/items/m3_hex_nut", { name: "x" })).status).toBe(400);
    expect((await req("DELETE", "/catalog/items/m3_hex_nut")).status).toBe(400);
    expect((await req("DELETE", `/catalog/items/${a.id}`)).json).toEqual({ ok: true });
  });

  it("search, allowed domains, builds", async () => {
    await start({ builds: [{ id: "b1", name: "Quad" }] });
    const hits = (await req("GET", "/catalog/search?q=608")).json as { id: string }[];
    expect(hits[0].id).toBe("bearing_608_deep_groove");
    expect((await req("POST", "/catalog/allowed_domains", { domain: "Shop.Example.com" })).json).toEqual(["shop.example.com"]);
    expect((await req("POST", "/catalog/allowed_domains", { domain: "not a domain" })).status).toBe(400);
    expect((await req("DELETE", "/catalog/allowed_domains/shop.example.com")).json).toEqual([]);
    expect(((await req("GET", "/projects")).json as { name: string }[]).map((b) => b.name)).toEqual(["Quad"]);
  });
});
