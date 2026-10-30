// @vitest-environment node
import { describe, expect, it } from "vitest";
import { provenance } from "../../../../api/accuracy";
import { settingsMarkers } from "../model";
import {
  ampMean, confidenceOf, cycleRange, cyclesShort, cyclesText, goodman, implied, meanCv, printDirectionText, snCycles, snStress,
} from "./model";

describe("calibration tests on their own", () => {
  it("a tensile bar gives its strength; a bend gives E by the beam formula", () => {
    expect(implied({ kind: "tensile", break_n: 1200, width_mm: 10, thickness_mm: 4 }).strength_mpa).toBe(30);
    // F 20 N, L 64 mm, b 10, h 4, δ 2 mm → E = 20·64³ / (4·10·64·2) = 1024 MPa
    expect(implied({ kind: "bend", span_mm: 64, width_mm: 10, thickness_mm: 4, load_n: 20, deflection_mm: 2 }).e_mpa).toBeCloseTo(1024, 6);
    expect(implied({ kind: "bend", span_mm: 64, width_mm: 10, thickness_mm: 4, load_n: 20, deflection_mm: 0 }).e_mpa).toBeNull();
    expect(implied({ kind: "part_load", sim_id: "sim_000000000001", load_n: 5, deflection_mm: 0.3 })).toEqual({ e_mpa: null, strength_mpa: null });
  });

  it("scatter and confidence", () => {
    const m = meanCv([100, 110, 90, null]);
    expect(m?.mean).toBe(100);
    expect(m?.cvPct).toBeCloseTo(10, 6);
    expect(meanCv([])).toBeNull();
    expect(confidenceOf(1, null)).toBe("low");
    expect(confidenceOf(3, 10)).toBe("medium");
    expect(confidenceOf(6, 5)).toBe("high");
    expect(confidenceOf(6, 30)).toBe("low");
  });

  it("the badge: calibrated (n tests) or datasheet / estimate", () => {
    expect(provenance({ name: "PLA", calibration: { n_tests: 3, confidence: "medium" } }).label).toBe("calibrated (3 tests)");
    expect(provenance({ name: "PLA", calibration: { n_tests: 1 } }).label).toBe("calibrated (1 test)");
    expect(provenance({ name: "PLA" }).label).toBe("datasheet / estimate");
    expect(provenance({ name: "PLA", calibration: "junk" }).calibrated).toBe(false);
  });
});

describe("fatigue arithmetic", () => {
  it("cycle shapes", () => {
    expect(cycleRange("fully_reversed", null)).toEqual([-1, 1]);
    expect(cycleRange("zero_to_max", null)).toEqual([0, 1]);
    expect(cycleRange("min_max", 0.2)).toEqual([0.2, 1]);
    expect(cycleRange("min_max", 7)).toEqual([1, 1]);
    expect(ampMean(10, "zero_to_max")).toEqual({ amp: 5, mean: 5 });
  });

  it("Basquin round-trips and Goodman raises the amplitude under a tensile mean", () => {
    const sn = { sf_mpa: 80, b: -0.1 };
    const s = snStress(sn, 1e6);
    expect(snCycles(sn, s)).toBeCloseTo(1e6, -2);
    expect(goodman(10, 25, 50)).toBeCloseTo(20, 9);
    expect(goodman(10, -5, 50)).toBe(10);
    expect(goodman(10, 60, 50)).toBeNull();
  });

  it("cycle counts as words", () => {
    expect(cyclesText(2.5e6)).toBe("2.5 million");
    expect(cyclesText(350)).toBe("350");
    expect(cyclesText(null)).toBe("beyond the curve");
    expect(cyclesShort(1e6)).toBe("10⁶");
    expect(cyclesShort(1e10)).toBe("10¹⁰");
  });

  it("contact faces and the print direction become viewer markers", () => {
    const m = settingsMarkers("static", {
      load_cases: [],
      nonlinear: { contact: [{ kind: "faces", master: "f1", slave: "f2" }, { kind: "rigid", face: "bottom" }] },
      print_direction: "+x",
    });
    expect(m.filter((x) => x.role === "contact").map((x) => [x.kind, x.face, x.path.join(".")])).toEqual([
      ["contact 1", "f1", "nonlinear.contact.0.master"], ["contact 1", "f2", "nonlinear.contact.0.slave"], ["contact 2", "bottom", "nonlinear.contact.1.face"],
    ]);
    expect(m.find((x) => x.role === "print")?.direction).toBe("+x");
    expect(settingsMarkers("static", { load_cases: [], print_direction: null }).some((x) => x.role === "print")).toBe(false);
  });

  it("print direction words", () => {
    expect(printDirectionText("+z")).toBe("layers stack along +z (printed flat)");
    expect(printDirectionText("-x")).toBe("layers stack along −x (printed on its side)");
    expect(printDirectionText(null)).toMatch(/isotropic/);
  });
});
