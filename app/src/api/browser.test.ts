import { describe, expect, it } from "vitest";
import {
  bomSchema, browserListSchema, browserSnapshotSchema, isProxyPath, partShownSchema, suggestedSitesSchema,
} from "./browser";

describe("isProxyPath", () => {
  it("accepts a same-origin relative path and rejects raw URLs / protocol-relative", () => {
    expect(isProxyPath("/browser/snapshots/s1/images/0")).toBe(true);
    expect(isProxyPath("https://evil.example/x.png")).toBe(false);
    expect(isProxyPath("//evil.example/x.png")).toBe(false);
    expect(isProxyPath("relative/x.png")).toBe(false);
    expect(isProxyPath(42)).toBe(false);
  });
});

describe("browserSnapshotSchema", () => {
  it("keeps proxied images, drops raw-URL images, and tolerates a bare-array table", () => {
    const snap = browserSnapshotSchema.parse({
      id: "s1", url: "https://shop.example/p/falcon", title: "Falcon 2207",
      text: "KV 1750, 35g", fetched_at: 100,
      images: [{ proxy_url: "/browser/snapshots/s1/images/0", alt: "motor" }, { proxy_url: "https://cdn.example/x.png" }],
      tables: [[["dim", "value"], ["mass", "35 g"]]],
    });
    expect(snap.images).toHaveLength(1);
    expect(snap.images[0].proxy_url).toBe("/browser/snapshots/s1/images/0");
    expect(snap.tables[0].rows).toHaveLength(2);
  });
});

describe("browserListSchema", () => {
  it("accepts a bare array or {snapshots} and drops a malformed snapshot", () => {
    const fromArray = browserListSchema.parse([{ id: "s1" }, { nope: true }]);
    expect(fromArray).toHaveLength(1);
    const wrapped = browserListSchema.parse({ snapshots: [{ id: "s2" }] });
    expect(wrapped[0].id).toBe("s2");
  });
});

describe("partShownSchema", () => {
  it("keeps a proxied image and drops a raw-URL one", () => {
    const kept = partShownSchema.parse({ name: "Falcon 2207", price: 24.5, currency: "USD", image: "/browser/snapshots/s1/images/0", catalog_id: "cat_x" });
    expect(kept.image).toBe("/browser/snapshots/s1/images/0");
    expect(kept.catalog_id).toBe("cat_x");
    const dropped = partShownSchema.parse({ name: "x", image: "https://cdn.example/x.png" });
    expect(dropped.image).toBeNull();
  });
});

describe("bomSchema", () => {
  it("defaults a made line to estimated and reads totals", () => {
    const bom = bomSchema.parse({
      lines: [
        { id: "l1", kind: "made", part_id: "bracket", name: "Bracket", qty: 2, unit_cost: 1.2 },
        { id: "l2", kind: "bought", catalog_id: "cat_x", name: "Servo", qty: 1, unit_cost: 24.5, vendor: "ServoCity", estimated: false },
      ],
      totals: { amount: 26.9, currency: "USD", by_vendor: [{ vendor: "ServoCity", amount: 24.5 }], made: 2.4, bought: 24.5, estimated: true },
    });
    expect(bom.lines[0].estimated).toBe(true);
    expect(bom.lines[1].estimated).toBe(false);
    expect(bom.totals.amount).toBe(26.9);
    expect(bom.totals.by_vendor[0].vendor).toBe("ServoCity");
  });
  it("accepts an {items} alias and empty totals", () => {
    const bom = bomSchema.parse({ items: [{ id: "l1", kind: "bought", name: "x" }] });
    expect(bom.lines).toHaveLength(1);
    expect(bom.totals.amount).toBe(0);
  });
});

describe("suggestedSitesSchema", () => {
  it("parses sites and drops malformed entries", () => {
    const sites = suggestedSitesSchema.parse([
      { name: "McMaster-Carr", domain: "mcmaster.com", search_url: "https://mcmaster.com/search?q={q}", categories: ["fasteners"], suggested: true },
      42,
    ]);
    expect(sites).toHaveLength(1);
    expect(sites[0].suggested).toBe(true);
  });
});
