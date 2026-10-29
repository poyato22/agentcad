import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { catalogItemSchema, type CatalogItem } from "../../../api/directory";
import { draftFrom, editDiff, fmtSpecValue, httpsLink, humanKey, massInfo, mountGeometry, onlineLink, onshapeDocUrl, parseDraft } from "./model";

const FX = resolve(import.meta.dirname, "../../../../test/fixtures/core-ui");
const read = (n: string) => JSON.parse(readFileSync(resolve(FX, n), "utf8")) as unknown;
const ALL = (read("catalog_all.json") as unknown[]).map((x) => catalogItemSchema.parse(x));
const byId = (id: string) => ALL.find((i) => i.id === id) as CatalogItem;
const IMPORTED = catalogItemSchema.parse(read("catalog_import.json"));


describe("mountGeometry (same placement as the core's envelope)", () => {
  it("motor 'opposite': pairs across the bolt circle at 45°", () => {
    const g = mountGeometry(byId("motor_2207_class").mount)!;
    expect(g.circle).toBe(16);
    expect(g.holes).toHaveLength(4);
    // the core's insert placed this hole at (-5.657, -5.657) (catalog_insert.json face f4)
    expect(g.holes.some((h) => Math.abs(h.x + 5.657) < 1e-3 && Math.abs(h.y + 5.657) < 1e-3)).toBe(true);
    for (const h of g.holes) expect(Math.hypot(h.x, h.y)).toBeCloseTo(8, 6);
    expect(g.holes[0].r).toBe(1.5);
  });

  it("square stacks: corners at ±a/2", () => {
    const stack = ALL.find((i) => i.mount?.layout === "square")!;
    const g = mountGeometry(stack.mount)!;
    const a = stack.mount!.spacing_mm![0] / 2;
    expect(g.holes.map((h) => [Math.abs(h.x), Math.abs(h.y)])).toEqual([[a, a], [a, a], [a, a], [a, a]]);
  });

  it("servo 'inline': two holes on the x axis", () => {
    const servo = ALL.find((i) => i.mount?.layout === "inline")!;
    const g = mountGeometry(servo.mount)!;
    expect(g.holes).toHaveLength(2);
    expect(g.holes.every((h) => h.y === 0)).toBe(true);
  });

  it("boards: explicit holes_mm and the board outline", () => {
    const board = ALL.find((i) => i.mount?.holes_mm?.length)!;
    const g = mountGeometry(board.mount, board.dims)!;
    expect(g.holes.map((h) => [h.x, h.y])).toEqual(board.mount!.holes_mm);
    expect(g.outline).toEqual({ w: board.dims.length_mm, h: board.dims.width_mm });
    expect(g.extent).toBeGreaterThanOrEqual(board.dims.length_mm / 2);
  });

  it("an imported link item with no layout falls back to the corners; no mount → null", () => {
    expect(mountGeometry(IMPORTED.mount)!.holes).toHaveLength(4);
    expect(mountGeometry(null)).toBeNull();
    expect(mountGeometry({ pattern: null, hole_d: 3, spacing_mm: null, thread: null, layout: null, holes_mm: null })).toBeNull();
  });
});

describe("mass and specs", () => {
  it("a stated mass is a catalog value; anything else is an estimate", () => {
    expect(massInfo(byId("motor_2207_class"))).toEqual({ text: "33 g", estimate: false });
    const nut = byId("m3_hex_nut");
    expect(nut.mass_source).toBe("estimate");
    expect(massInfo(nut).estimate).toBe(true);
    // a mass_g the directory didn't state as catalog is still shown as an estimate
    expect(massInfo({ dims: { mass_g: 12 }, mass_source: "estimate" })).toEqual({ text: "12 g", estimate: true });
  });

  it("a missing mass_source parses as an estimate", () => {
    const raw = { ...(read("catalog_item.json") as Record<string, unknown>) };
    delete raw.mass_source;
    expect(catalogItemSchema.parse(raw).mass_source).toBe("estimate");
  });

  it("formats values and keys", () => {
    expect(fmtSpecValue({ value: 34.0194, unit: "g" })).toBe("34.02 g");
    expect(fmtSpecValue({ value: "16x16", unit: "mm" })).toBe("16x16 mm");
    expect(humanKey("max_thrust")).toBe("Max thrust");
  });
});

describe("links", () => {
  it("only https links open, and their host is shown", () => {
    expect(httpsLink("https://shop.example.com/p/x?a=1")).toEqual({ href: "https://shop.example.com/p/x?a=1", host: "shop.example.com" });
    expect(httpsLink("http://shop.example.com")).toBeNull();
    expect(httpsLink("javascript:alert(1)")).toBeNull();
    expect(httpsLink("https://user:pw@evil.example.com")).toBeNull();
    expect(onlineLink(IMPORTED)?.host).toBe("shop.example.com");
    expect(onlineLink(byId("motor_2207_class"))?.host).toBe("www.getfpv.com");
  });

  it("Onshape document links are built only from real ids", () => {
    expect(onshapeDocUrl("000000000000000000000001", "000000000000000000000002")).toBe(
      "https://cad.onshape.com/documents/000000000000000000000001/w/000000000000000000000002");
    expect(onshapeDocUrl("../../evil")).toBeNull();
  });
});

describe("review form", () => {
  it("an untouched draft has no changes; edits send only what changed", () => {
    const d = draftFrom(IMPORTED);
    expect(editDiff(IMPORTED, parseDraft(d).fields)).toEqual({});
    const d2 = { ...d, name: "Falcon 2207", dims: [...d.dims, { key: "length_mm", value: "27.5" }] };
    const diff = editDiff(IMPORTED, parseDraft(d2).fields);
    expect(Object.keys(diff).sort()).toEqual(["dims", "name"]);
    expect(diff.dims?.length_mm).toBe(27.5);
  });

  it("numbers stay numbers, notes are kept, and bad rows are reported", () => {
    const item = byId("motor_2207_class");
    const { fields } = parseDraft(draftFrom(item));
    expect(fields.specs.find((s) => s.key === "mass")).toEqual({ key: "mass", value: 33, unit: "g", note: "typical" });
    const bad = parseDraft({ ...draftFrom(IMPORTED), name: " ", dims: [{ key: "Length", value: "x" }] });
    expect(bad.errors.length).toBe(2);
  });
});
