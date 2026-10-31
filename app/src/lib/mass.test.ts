import { describe, expect, it } from "vitest";
import { partSummarySchema } from "../api/schemas";
import { assemblyMassTitle, fmtMass, partMassG, partMassNote, partMassText, partMaterialText } from "./mass";

const built = { part_id: "a", vars: {}, mtime: 1, metrics: null };

describe("part mass (Plan 9 B2)", () => {
  it("reads the core's tree mass first", () => {
    expect(partMassG({ ...built, mass_g: 2.54, metrics: null })).toBe(2.54);
    expect(partMassText({ ...built, mass_g: 2.54, mass_source: "built" })).toBe("2.5 g");
    expect(partMassText({ ...built, mass_g: 3.72, mass_source: "sum" }, 2)).toBe("Σ 3.72 g");
  });

  it("shows a dash and the reason when the mass is unknown", () => {
    const p = { ...built, mass_g: null, mass_source: "unknown" as const, mass_note: "build failed: the part has no successful build yet" };
    expect(partMassText(p)).toBe("—");
    expect(partMassNote(p)).toMatch(/build failed/);
  });

  it("falls back to metrics.mass_g for a core without mass_g", () => {
    const m = partSummarySchema.parse({ part_id: "a", vars: {}, mtime: 1, metrics: { mass_g: 7, volume_mm3: 1 } });
    expect(m.mass_g).toBeUndefined();
    expect(partMassG(m)).toBe(7);
    expect(partMassNote({ kind: "group", metrics: null })).toBe("empty group");
    expect(partMassNote({ kind: "part", metrics: null })).toBe("not built yet");
  });

  it("names what the assembly total leaves out", () => {
    expect(assemblyMassTitle([])).toMatch(/every instance/);
    expect(assemblyMassTitle([{ part_id: "bad", count: 2, reason: "not built yet" }])).toBe("Total mass leaves out bad (x2): not built yet");
  });

  it("formats kilograms", () => {
    expect(fmtMass(1234.5)).toBe("1.23 kg");
    expect(fmtMass(null)).toBe("—");
  });

  it("names the build default material", () => {
    expect(partMaterialText({ material: "PETG" }, "PLA")).toBe("PETG");
    expect(partMaterialText({ material: null }, "PLA")).toBe("PLA (build default)");
    expect(partMaterialText({}, null)).toBe("—");
  });

  it("one bad metrics field no longer nulls every metric", () => {
    const p = partSummarySchema.parse({
      part_id: "a", vars: {}, mtime: 1,
      metrics: { valid: true, volume_mm3: 4000, area_mm2: 2000, mass_g: 4.96, holes: 2, bbox: { min: [0, 0, 0], max: [40, 20, 5], size: [40, 20, 5] },
        center_of_mass: [20, 10, 2.5], faces: 6, edges: 12, solids: 1 },
      mass_g: 4.96, mass_source: "built", mass_note: "4000 mm3 x 1.24 g/cm3 (PLA, build default)", material: null,
    });
    expect(p.metrics?.volume_mm3).toBe(4000);
    expect(p.metrics?.holes).toEqual([]);
    expect(p.metrics?.bbox.size).toEqual([40, 20, 5]);
    expect(p.mass_g).toBe(4.96);
    // a non-object is still null, and a bad mass field is caught on its own
    const q = partSummarySchema.parse({ part_id: "a", vars: {}, mtime: 1, metrics: "x", mass_g: "heavy", mass_source: "guess" });
    expect(q.metrics).toBeNull();
    expect(q.mass_g).toBeNull();
    expect(q.mass_source).toBeNull();
  });
});
