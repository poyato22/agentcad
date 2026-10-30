import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { CoreClient, CoreError } from "./client";
import type { CoreInfo } from "./types";

const info: CoreInfo = { baseUrl: "http://127.0.0.1:7400", wsUrl: "ws://127.0.0.1:7400/events", token: "tok-abc", owned: false };
const FX = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui/browser");
const real = (name: string): unknown => JSON.parse(readFileSync(resolve(FX, name), "utf8"));

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}
function clientWith(impl: (url: string, init: RequestInit) => Promise<Response>) {
  const f = vi.fn(impl);
  return { client: new CoreClient(info, f as unknown as typeof fetch), f };
}
const path = (u: unknown) => { const x = new URL(String(u)); return x.pathname + x.search; };

describe("CoreClient browser + BOM (real-core shapes)", () => {
  it("runBrowser asks for whole snapshots (?full=1)", async () => {
    const { client, f } = clientWith(async () => json(real("browser_list_full.json")));
    const snaps = await client.runBrowser("run1");
    expect(path(f.mock.calls[0][0])).toBe("/runs/run1/browser?full=1");
    expect(snaps).toHaveLength(3);
  });

  it("partCard reads GET /browser/cards/{id}", async () => {
    const { client, f } = clientWith(async () => json(real("browser_card.json")));
    const card = await client.partCard("card_2931e9ba8d83edbb");
    expect(path(f.mock.calls[0][0])).toBe("/browser/cards/card_2931e9ba8d83edbb");
    expect(card.vendor).toBe("TowerPro");
  });

  it("snapshotImage fetches only a core proxy path and only an image", async () => {
    const { client } = clientWith(async () => new Response(new Uint8Array([1]), { status: 200, headers: { "content-type": "image/png" } }));
    expect((await client.snapshotImage("/browser/snapshots/snap_0123456789abcdef/images/0")).type).toBe("image/png");
    await expect(client.snapshotImage("https://cdn.example/x.png")).rejects.toBeInstanceOf(CoreError);
    const svg = clientWith(async () => new Response("<svg/>", { status: 200, headers: { "content-type": "image/svg+xml" } }));
    await expect(svg.client.snapshotImage("/browser/snapshots/snap_0123456789abcdef/images/0")).rejects.toBeInstanceOf(CoreError);
  });

  it("bomAdd sends exactly the core's BomItemIn fields (no source, no snapshot_id)", async () => {
    const { client, f } = clientWith(async () => json(real("bom_add.json")));
    await client.bomAdd("b1", { kind: "bought", name: "Servo", currency: "eur", ...({ source: "user", snapshot_id: "snap_x" } as object) });
    const body = JSON.parse(String(f.mock.calls[0][1].body));
    expect(body).toEqual({ kind: "bought", name: "Servo", currency: null });
  });

  it("a BOM with the wrong totals shape is an error, not zeros", async () => {
    const { client } = clientWith(async () => json({ ...(real("bom.json") as object), totals: { amount: 0 } }));
    const e = await client.bom("b1").catch((x: unknown) => x);
    expect(e).toBeInstanceOf(CoreError);
    expect((e as CoreError).code).toBe("validation");
  });

  it("the core's 400 for an extra field surfaces its message", async () => {
    const r = real("bom_add_extra_field.json") as { status: number; body: unknown };
    const { client } = clientWith(async () => json(r.body, r.status));
    const e = (await client.bomAdd("b1", { name: "x" }).catch((x: unknown) => x)) as CoreError;
    expect(e.message).toMatch(/Extra inputs are not permitted/);
  });

  it("restoreDefaultSites posts to the core's restore route", async () => {
    const { client, f } = clientWith(async () => json(real("sites_restore.json")));
    const sites = await client.restoreDefaultSites();
    expect(f.mock.calls[0][1].method).toBe("POST");
    expect(path(f.mock.calls[0][0])).toBe("/browser/sites/restore_defaults");
    expect(sites.length).toBeGreaterThan(10);
  });

  it("bomCsv returns the core's CSV as a typed blob", async () => {
    const csv = readFileSync(resolve(FX, "bom.csv"), "utf8");
    const { client } = clientWith(async () => new Response(csv, { status: 200, headers: { "content-type": "text/csv" } }));
    const text = await (await client.bomCsv("b1")).text();
    expect(text).toContain(`"'=HYPERLINK(""https://evil.example"")"`);      // the core neutralized the formula
  });
});
