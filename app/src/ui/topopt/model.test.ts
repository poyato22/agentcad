import { describe, expect, it } from "vitest";
import { topoptRecordSchema, type TopoptRecord } from "../../api/topopt";
import {
  applyEvent, defaultForm, fmtMass, merged, overallPct, previewVariant, ranked, statusLine, toBody, validate, variantRows,
  verdict, vfSpread,
} from "./model";

const variant = (id: string, over: Record<string, unknown> = {}) => ({
  id, name: `vf ${id}`, status: "done", phase: "done", pct: 100, volume_fraction: 0.2, resolution_mm: 1,
  filter_radius_mm: 2, mass_g: 11.8, verified: true, passes: true, pareto: true, rank: 1,
  verify: { max_deflection_mm: 0.75, max_von_mises_mpa: 25.5, safety_factor: 1.4 }, ...over,
});
const record = (variants: unknown[], over: Record<string, unknown> = {}): TopoptRecord =>
  topoptRecordSchema.parse({ topopt_id: "to_0123456789ab", part_id: "bracket", status: "done", variants, recommended: "v0", ...over });

describe("form", () => {
  it("defaults to a valid 3-variant volume sweep", () => {
    const f = defaultForm("bracket");
    expect(validate(f)).toEqual([]);
    const b = toBody(f);
    expect(b.part_id).toBe("bracket");
    expect(b.fixtures).toEqual([{ kind: "fixed", face: "bottom" }]);
    expect(b.load_cases[0].loads).toEqual([{ face: "top", newtons: 100, direction: "-z" }]);
    expect(b.sweep).toEqual({ volume_fraction: [0.17, 0.25, 0.33] });
    expect(b.max_variants).toBe(3);
    expect(b.volume_fraction).toBe(0.25);
  });

  it("one variant sends no sweep; keep faces and symmetry go through", () => {
    const b = toBody({ ...defaultForm("p"), variants: 1, keepFaces: ["f3"], keepThickness: 3, symmetry: ["y"], process: "print_3d" });
    expect(b.sweep).toBeUndefined();
    expect(b.keep).toEqual([{ kind: "faces", faces: ["f3"], thickness_mm: 3 }]);
    expect(b.manufacturing).toEqual({ process: "print_3d", symmetry: ["y"] });
  });

  it("a deflection goal asks for the lightest variant within it", () => {
    const b = toBody({ ...defaultForm("p"), goal: "deflection", maxDeflection: 0.8 });
    expect(b.objective).toBe("min_mass");
    expect(b.max_deflection_mm).toBe(0.8);
    expect(b.volume_fraction).toBeUndefined();
    // one variant asked for: still a sweep of 3 (the lightest within the target needs several)
    const one = toBody({ ...defaultForm("p"), goal: "deflection", maxDeflection: 0.8, variants: 1 });
    expect(one.sweep?.volume_fraction).toHaveLength(3);
    expect(one.max_variants).toBe(3);
  });

  it("states the conservative peak (fixture zone included) and the one away from it", () => {
    const v = topoptRecordSchema.parse({ topopt_id: "t", part_id: "p", status: "done", variants: [variant("v0", {
      verify: { max_deflection_mm: 0.7, max_von_mises_mpa: 12.5, max_von_mises_incl_fixture_mpa: 20.1, safety_factor: 1.78 } })] }).variants[0];
    expect(variantRows(v).map((r) => `${r.label}: ${r.value}`)).toEqual([
      "Mass: 11.8 g", "Deflection: 0.7 mm", "Peak stress: 20.1 MPa", "Safety factor: 1.78", "Away from the fixture: 12.5 MPa"]);
  });

  it("checks the setup", () => {
    const f = defaultForm("p");
    expect(validate({ ...f, fixtures: [] }).map((e) => e.field)).toContain("fixtures");
    expect(validate({ ...f, loads: [{ face: "bottom", newtons: 10, direction: "-z" }] })[0].message).toMatch(/both held and loaded/);
    expect(validate({ ...f, volumeFraction: 0.9 })[0].field).toBe("volumeFraction");
    expect(validate({ ...f, goal: "deflection", maxDeflection: null })[0].field).toBe("maxDeflection");
    expect(validate({ ...f, loads: [{ face: "top", newtons: 0, direction: "-z" }] })[0].field).toBe("loads.0");
  });

  it("spreads volume fractions inside the allowed range", () => {
    expect(vfSpread(0.1, 3)).toEqual([0.08, 0.16, 0.24]);
    expect(vfSpread(0.7, 2)).toEqual([0.64, 0.72]);
    expect(vfSpread(0.3, 1)).toEqual([0.3]);
  });
});

describe("live state", () => {
  it("lays the latest progress over a running record", () => {
    const rec = record([variant("v0", { status: "running", phase: "optimize", pct: 10, verified: false, rank: null })], { status: "running" });
    const live = applyEvent(new Map(), "topopt_progress", { topopt_id: rec.topopt_id, part_id: "bracket", variant: "v0", phase: "optimize", pct: 40, it: 20, max_iter: 60, preview: 20 });
    const m = merged(rec, live.get(rec.topopt_id));
    expect(m.variants[0].it).toBe(20);
    expect(m.variants[0].pct).toBe(40);
    expect(previewVariant(m)?.id).toBe("v0");
    expect(overallPct(m)).toBe(40);
    expect(statusLine(m)).toBe("Running · 0 of 1 variants done · 40%");
    // other events are ignored
    expect(applyEvent(live, "topopt_finished", { topopt_id: rec.topopt_id, part_id: "bracket" })).toBe(live);
  });
});

describe("variant cards", () => {
  it("reads CalculiX numbers and the verdict", () => {
    const v = topoptRecordSchema.parse({ topopt_id: "t", part_id: "p", status: "done", variants: [variant("v0")] }).variants[0];
    expect(variantRows(v).map((r) => r.value)).toEqual(["11.8 g", "0.75 mm", "25.5 MPa", "1.40"]);
    expect(verdict(v)).toEqual({ kind: "pass", text: "✓ Verified" });
    expect(verdict({ ...v, passes: false })).toEqual({ kind: "fail", text: "✗ Fails target" });
    expect(verdict({ ...v, verified: false, verify: { error: "ccx missing" } }).text).toBe("Not verified");
    expect(fmtMass(1500)).toBe("1.50 kg");
    expect(fmtMass(null)).toBe("—");
  });

  it("orders by the core's rank and states the recommendation", () => {
    const rec = record([variant("v0", { rank: 2 }), variant("v1", { rank: 1, name: "vf 0.30" }), variant("v2", { rank: null, status: "failed" })], { recommended: "v1" });
    expect(ranked(rec).map((v) => v.id)).toEqual(["v1", "v0", "v2"]);
    expect(statusLine(rec)).toBe("Done · recommended vf 0.30");
    expect(statusLine({ ...rec, recommended: null })).toBe("Done · no variant passed verification");
  });
});
