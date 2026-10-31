import { describe, expect, it } from "vitest";
import partsJson from "../../../test/fixtures/core-ui/parts_tree.json";
import { partSummarySchema } from "../../api/schemas";
import type { PartSummary } from "../../api/types";
import { fmtMass, masses, ownMass, rowMassText, totalMass } from "./mass";

const metrics = (g: number) => ({
  valid: true, volume_mm3: 1, area_mm2: 1, mass_g: g, bbox: { min: [0, 0, 0], max: [1, 1, 1], size: [1, 1, 1] },
  center_of_mass: [0, 0, 0], faces: 1, edges: 1, holes: [], solids: 1,
}) as PartSummary["metrics"];
const row = (id: string, extra: Partial<PartSummary> = {}): PartSummary =>
  ({ part_id: id, id, label: id, vars: {}, metrics: null, mtime: 0, parent_id: null, order: 0, kind: "part", version: 1, ...extra });

describe("parts tree mass (Plan 9 D9)", () => {
  it("prefers B2's mass_g and falls back to the built metrics", () => {
    expect(ownMass(row("a", { mass_g: 12, metrics: metrics(99) })).g).toBe(12);
    expect(ownMass(row("a", { metrics: metrics(7.5) })).g).toBe(7.5);
    expect(ownMass(row("a", { mass_g: null, mass_note: "build failed" }))).toEqual({ g: null, note: "build failed" });
    expect(ownMass(row("a")).note).toBe("not built yet");
  });

  it("a group sums its children (unknown ones counted apart); the total counts each part once", () => {
    const list = [
      row("body", { kind: "group" }),
      row("leg", { parent_id: "body", metrics: metrics(180) }),
      row("hip", { parent_id: "body", order: 1, metrics: metrics(20.5) }),
      row("cover", { parent_id: "body", order: 2 }),
      row("bolt", { order: 1, kind: "catalog", mass_g: 2, mass_source: "catalog" }),
      row("skeleton_b", { kind: "skeleton", order: 2 }),
    ];
    const m = masses(list);
    expect(m.get("body")).toMatchObject({ g: 200.5, unknown: 1 });
    expect(m.get("skeleton_b")?.g).toBeNull();
    expect(totalMass(list)).toEqual({ g: 202.5, unknown: 1 });
    // B2's own group sum wins over ours
    expect(masses([row("g", { kind: "group", mass_g: 50, mass_source: "sum" }), row("x", { parent_id: "g", metrics: metrics(1) })]).get("g")?.g).toBe(50);
  });

  it("reads the real core's part tree (with or without B2's fields)", () => {
    const list = (partsJson as unknown[]).map((p) => partSummarySchema.parse(p) as PartSummary);
    const built = list.filter((p) => p.metrics);
    expect(built.length).toBeGreaterThan(0);
    for (const p of built) expect(masses(list).get(p.part_id)?.g).toBe(p.mass_g ?? p.metrics?.mass_g);
  });

  it("rows read lib/mass: the core's sum gets Σ, unknown a dash with the core's reason (B2)", () => {
    const list = [
      row("frame", { kind: "group", mass_g: 3.72, mass_source: "sum", mass_note: "sum of 2 items" }),
      row("a", { parent_id: "frame", mass_g: 1.24, mass_source: "built", metrics: metrics(99) }),
      row("b", { parent_id: "frame", order: 1, mass_g: null, mass_source: "unknown", mass_note: "build failed" }),
      row("skel", { kind: "skeleton", order: 1 }),
    ];
    const m = masses(list);
    expect(rowMassText(list[0], m.get("frame"))).toBe("Σ 3.7 g");
    expect(m.get("frame")).toMatchObject({ note: "sum of 2 items", unknown: 1 });
    expect(rowMassText(list[1], m.get("a"))).toBe("1.2 g");          // the tree mass, not metrics' 99 g
    expect(rowMassText(list[2], m.get("b"))).toBe("—");
    expect(m.get("b")?.note).toBe("build failed");
    expect(rowMassText(list[3], m.get("skel"))).toBe("");
    expect(totalMass(list)).toEqual({ g: 3.72, unknown: 1 });
  });

  it("formats grams and kilograms", () => {
    expect(fmtMass(180)).toBe("180 g");
    expect(fmtMass(1240)).toBe("1.24 kg");
    expect(fmtMass(12.34)).toBe("12.3 g");
    expect(fmtMass(0.42)).toBe("0.42 g");
  });
});
