import { describe, expect, it, vi } from "vitest";
import { CoreClient, CoreError } from "./client";
import type { CoreInfo } from "./types";

const info: CoreInfo = { baseUrl: "http://127.0.0.1:7400", wsUrl: "ws://127.0.0.1:7400/events", token: "tok-abc", owned: false };

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });
}
function clientWith(impl: (url: string, init: RequestInit) => Promise<Response>) {
  const f = vi.fn(impl);
  return { client: new CoreClient(info, f as unknown as typeof fetch), f };
}

describe("CoreClient browser + BOM (Plan 7)", () => {
  it("runBrowser lists a run's snapshots", async () => {
    const { client, f } = clientWith(async () => json([{ id: "s1", url: "https://x", title: "T" }]));
    const snaps = await client.runBrowser("run1");
    expect(f.mock.calls[0][0]).toBe("http://127.0.0.1:7400/runs/run1/browser");
    expect(snaps[0].id).toBe("s1");
  });

  it("snapshotImage fetches only a core proxy path and refuses a raw URL", async () => {
    const { client, f } = clientWith(async () => new Response(new Uint8Array([1, 2]), { status: 200, headers: { "content-type": "image/png" } }));
    const blob = await client.snapshotImage("/browser/snapshots/s1/images/0");
    expect(blob.type).toBe("image/png");
    expect(f.mock.calls[0][0]).toBe("http://127.0.0.1:7400/browser/snapshots/s1/images/0");
    await expect(client.snapshotImage("https://cdn.example/x.png")).rejects.toBeInstanceOf(CoreError);
  });

  it("bom reads lines and totals; bomAdd/Patch/Delete hit the right routes", async () => {
    const line = { id: "l1", kind: "bought", name: "Servo", qty: 1, unit_cost: 24.5, vendor: "ServoCity" };
    const { client, f } = clientWith(async (url, init) => {
      if (url.endsWith("/bom") && init.method === "GET") return json({ lines: [line], totals: { amount: 24.5, by_vendor: [] } });
      if (url.endsWith("/bom") && init.method === "POST") return json(line);
      if (url.endsWith("/bom/l1") && init.method === "PATCH") return json({ ...line, qty: 3 });
      if (url.endsWith("/bom/l1") && init.method === "DELETE") return json({ ok: true });
      throw new Error(`unexpected ${init.method} ${url}`);
    });
    const bom = await client.bom("b1");
    expect(bom.lines[0].name).toBe("Servo");
    expect(bom.totals.amount).toBe(24.5);
    await client.bomAdd("b1", { kind: "bought", name: "Servo" });
    const patched = await client.bomPatch("b1", "l1", { qty: 3 });
    expect(patched.qty).toBe(3);
    await client.bomDelete("b1", "l1");
    expect(f.mock.calls.map((c) => `${c[1].method} ${new URL(c[0] as string).pathname}`)).toEqual([
      "GET /projects/b1/bom", "POST /projects/b1/bom", "PATCH /projects/b1/bom/l1", "DELETE /projects/b1/bom/l1",
    ]);
  });

  it("bomCsv returns a typed csv blob", async () => {
    const { client } = clientWith(async () => new Response("name,qty\nServo,1\n", { status: 200, headers: { "content-type": "text/csv" } }));
    const blob = await client.bomCsv("b1");
    expect(blob.type).toBe("text/csv");
    expect(await blob.text()).toContain("Servo");
  });
});
