// The browser / part card / BOM / sites schemas against the REAL core's answers (dumped by
// core/scripts/dump_browser_bom_fixtures.py), and their strictness: a contract mismatch fails
// the parse (the client shows an error) instead of turning into zeros or dropped rows.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  bomBody, bomRowSchema, bomSchema, browserListSchema, browserSnapshotSchema, isProxyPath, onDomain, partCardSchema,
  partShowResultSchema, suggestedSitesSchema,
} from "./browser";

const FX = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui/browser");
const real = (name: string): unknown => JSON.parse(readFileSync(resolve(FX, name), "utf8"));

describe("isProxyPath", () => {
  it("accepts a core-relative path and rejects raw URLs, protocol-relative, backslashes and dot segments", () => {
    expect(isProxyPath("/browser/snapshots/snap_0123456789abcdef/images/0")).toBe(true);
    for (const bad of ["https://evil.example/x.png", "//evil.example/x.png", "relative/x.png", "/\\evil.com/x", "/browser/../profile",
      "/browser/./x", "data:image/png;base64,AA", 42, null]) expect(isProxyPath(bad), String(bad)).toBe(false);
  });
});

describe("real-core answers parse", () => {
  it("GET /runs/{id}/browser?full=1: page, search and blocked snapshots", () => {
    const list = browserListSchema.parse(real("browser_list_full.json"));
    expect(list.map((s) => s.kind)).toEqual(["search", "page", "page"]);
    const [search, page, blocked] = list;
    expect(search.results.length).toBe(3);
    expect(search.site).toBe("Adafruit");
    expect(page.text).toContain("Ignore all previous");
    expect(page.tables[0].rows[1]).toEqual(["Weight", "9 g"]);
    expect(page.images.every((i) => isProxyPath(i.proxy_url))).toBe(true);
    expect(blocked.blocked).toMatch(/^site blocked automated access/);
  });

  it("the summary list (no ?full=1) is NOT a full snapshot: parsing it fails loudly", () => {
    expect(browserListSchema.safeParse(real("browser_list.json")).success).toBe(false);
  });

  it("GET /browser/snapshots/{sid} and the search snapshot", () => {
    expect(browserSnapshotSchema.parse(real("browser_snapshot.json")).kind).toBe("page");
    expect(browserSnapshotSchema.parse(real("browser_search_snapshot.json")).results[0].site).toBe("Adafruit");
  });

  it("part_show's result is ids only; the card comes from GET /browser/cards/{id}", () => {
    const res = partShowResultSchema.parse(real("part_show_result.json"));
    const card = partCardSchema.parse(real("browser_card.json"));
    expect(card.id).toBe(res.card_id);
    expect(card.name).toBe("Micro servo - TowerPro SG92R");
    expect(card.price).toBe(5.95);
    expect(card.currency).toBe("USD");
    expect(card.catalog_backed).toBe(true);
    expect(card.project_id).toBeTruthy();
    expect(isProxyPath(card.image)).toBe(true);
  });

  it("a card's non-proxy image is dropped, never fetched", () => {
    const c = { ...(real("browser_card.json") as Record<string, unknown>), image: "https://cdn.evil/x.png" };
    expect(partCardSchema.parse(c).image).toBeNull();
  });

  it("GET /projects/{id}/bom: per-currency totals, estimate flags and the auto source", () => {
    const bom = bomSchema.parse(real("bom.json"));
    expect(bom.totals.by_currency).toEqual({ USD: 35.97, EUR: 2.4, unknown: 3.0 });
    expect(bom.totals.unpriced).toBe(1);
    const auto = bom.items.find((r) => r.source === "auto");
    expect(auto?.estimate).toBe(true);
    expect(auto?.estimate_note).toMatch(/^estimate: 12\.4 g of PLA/);
    expect(bom.items.find((r) => r.name === "M3 screws")?.currency).toBeNull();
    expect(bomRowSchema.parse(real("bom_add.json")).source).toBe("user");
    expect(bomRowSchema.parse(real("bom_patch.json")).qty).toBe(6);
  });

  it("a totals shape mismatch fails (no silent $0.00)", () => {
    const b = real("bom.json") as Record<string, unknown>;
    const wrong = { ...b, totals: { amount: 99.68, currency: "USD", made: 1.68, bought: 98, estimated: true, by_vendor: [] } };
    expect(bomSchema.safeParse(wrong).success).toBe(false);
  });

  it("suggested sites keep entries with a null search_url (datasheet hosts)", () => {
    const sites = suggestedSitesSchema.parse(real("sites_defaults.json"));
    const raw = real("sites_defaults.json") as unknown[];
    expect(sites).toHaveLength(raw.length);
    expect(sites.some((s) => s.search_url === null && s.domain === "ti.com")).toBe(true);
    expect(suggestedSitesSchema.parse(real("sites_restore.json"))).toHaveLength(raw.length);
  });
});

describe("bomBody", () => {
  it("sends only the keys the core accepts, and only a 3-letter currency", () => {
    const body = bomBody({ kind: "bought", name: "x", currency: "usd", ...({ snapshot_id: "s", source: "user" } as object) }, "new");
    expect(Object.keys(body).sort()).toEqual(["currency", "kind", "name"]);
    expect(body.currency).toBeNull();
    expect(bomBody({ qty: 2, currency: "EUR", ...({ kind: "made" } as object) }, "patch")).toEqual({ qty: 2, currency: "EUR" });
  });
});

describe("onDomain", () => {
  it("matches the domain and its subdomains only", () => {
    expect(onDomain("www.pololu.com", "pololu.com")).toBe(true);
    expect(onDomain("pololu.com", "pololu.com")).toBe(true);
    expect(onDomain("evilpololu.com", "pololu.com")).toBe(false);
  });
});
