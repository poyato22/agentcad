// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { calibrationFor, calibrationSchema, provenance } from "../../../../api/accuracy";
import { settingsMarkers } from "../model";
import {
  ampMean, bodyKey, bodyOf, cycleRange, cyclesShort, cyclesText, eFromResidual, eRows, goodman, implied, printDirectionText, rowProblems, rowsOf,
  snCycles, snStress, type SheetRow,
} from "./model";

const DIR = resolve(import.meta.dirname, "../../../../../../core/tests/fixtures/ui");
const read = (n: string): unknown => JSON.parse(readFileSync(resolve(DIR, n), "utf8"));
const SAVED = calibrationSchema.parse(read("s3_cal_save.json"));

describe("calibration tests on their own", () => {
  it("a tensile bar gives its strength; a bend gives E = P L³ / (48 δ I)", () => {
    expect(implied({ kind: "tensile", t: { force_n: 1200, width_mm: 10, thickness_mm: 4, orientation: "xy" } }).strength_mpa).toBe(30);
    expect(implied({ kind: "tensile", t: { force_n: 1200, area_mm2: 60, orientation: "xy" } }).strength_mpa).toBe(20);
    // 40 N, L 64, b 10, h 4, δ 2 → 40·64³ / (48·2·(10·4³/12)) = 2048 MPa
    expect(implied({ kind: "bend", t: { span_mm: 64, width_mm: 10, thickness_mm: 4, load_n: 40, deflection_mm: 2, orientation: "xy" } }).e_mpa).toBeCloseTo(2048, 6);
    expect(implied({ kind: "part", t: { sim_id: "sim_000000000001", point: [1, 2, 3], load_n: 5, deflection_mm: 0.3 } })).toEqual({ e_mpa: null, strength_mpa: null });
  });

  it("the body the core takes: its keys only, empty optionals left out; a saved record round-trips", () => {
    const rows: SheetRow[] = [
      { kind: "bend", t: { span_mm: 64, width_mm: 10, thickness_mm: 4, load_n: 40, deflection_mm: 2, break_load_n: null, orientation: "xy" } },
      { kind: "tensile", t: { force_n: 1300, width_mm: 10, thickness_mm: 4, elongation_pct: null, orientation: "xy" } },
    ];
    expect(bodyOf(" PLA ", "MK4", rows)).toEqual({
      material: "PLA", printer: "MK4", tensile: [{ force_n: 1300, width_mm: 10, thickness_mm: 4, orientation: "xy" }],
      bend: [{ span_mm: 64, width_mm: 10, thickness_mm: 4, load_n: 40, deflection_mm: 2, orientation: "xy" }], part: [],
    });
    // the real core's stored tests carry defaults (note "", area_mm2 null …): the same body as the user's own sheet
    const sheet = rowsOf(SAVED);
    expect(bodyKey(bodyOf(SAVED.material, SAVED.printer, sheet))).toBe(bodyKey(bodyOf("PLA", "MK4", sheet.map((r) => ({ ...r, t: { ...r.t } }) as SheetRow))));
    expect(bodyOf("PLA", "MK4", sheet).tensile[0]).toEqual({ force_n: 1300, width_mm: 10, thickness_mm: 4, orientation: "xy" });
  });

  it("the fitted E residuals belong to the xy bends, then the part tests", () => {
    const rows = rowsOf(SAVED);
    const idx = eRows(rows, SAVED.fit);
    expect(idx.map((i) => rows[i].kind)).toEqual(["bend", "bend", "part"]);
    const e = SAVED.fit.e!;
    // each test's own E from the real fit: the bend rows match the formula
    expect(eFromResidual(e.value_mpa, e.residuals[0])).toBeCloseTo(implied(rows[idx[0]]).e_mpa!, 6);
  });

  it("row problems, and the badge only where a calibration applies", () => {
    expect(rowProblems({ kind: "part", t: { sim_id: "", point: [Number.NaN, 0, 0], load_n: 1, deflection_mm: 1 } })).toEqual(["the study of the loaded part", "the point where you measured"]);
    expect(rowProblems({ kind: "tensile", t: { force_n: 1, orientation: "xy" } })).toEqual(["width and thickness (or the area)"]);
    const pla = { name: "PLA" };
    expect(provenance(pla).label).toBe("datasheet / estimate");
    expect(provenance(pla, calibrationFor([SAVED], "PLA", "mk4")).label).toBe("calibrated (7 tests)");
    expect(calibrationFor([SAVED], "PLA", null)).toBeNull();
    // a study's resolved material carries the core's own word
    const calibrated = (read("s3_static_calibrated.json") as { material: Record<string, unknown> & { name: string } }).material;
    expect(provenance(calibrated).label).toBe("calibrated (7 tests)");
  });
});

describe("fatigue arithmetic (core sim/fatigue.py)", () => {
  it("cycle shapes from min_pct", () => {
    expect(cycleRange("fully_reversed", null)).toEqual([-1, 1]);
    expect(cycleRange("zero_to_max", null)).toEqual([0, 1]);
    expect(cycleRange("min_max", 20)).toEqual([0.2, 1]);
    expect(cycleRange("min_max", 700)).toEqual([1, 1]);
    expect(ampMean(10, "zero_to_max")).toEqual({ amp: 5, mean: 5 });
  });

  it("σa = A·N^b round-trips, matches the real study's allowable amplitude, and Goodman under a tensile mean", () => {
    const f = (read("s3_static.json") as { results: { fatigue: { sn_curve: { a_mpa: number; b: number; uts_mpa: number }; cycles_required: number; allowable_amplitude_mpa: number; equivalent_amplitude_mpa: number; cycles_to_failure: number } } }).results.fatigue;
    expect(snStress(f.sn_curve, f.cycles_required)).toBeCloseTo(f.allowable_amplitude_mpa, 9);
    expect(snCycles(f.sn_curve, f.equivalent_amplitude_mpa)).toBeCloseTo(f.cycles_to_failure, 0);
    expect(goodman(10, 25, 50)).toBeCloseTo(20, 9);
    expect(goodman(10, -5, 50)).toBe(10);
    expect(goodman(10, 60, 50)).toBeNull();
  });

  it("cycle counts as words", () => {
    expect(cyclesText(2.5e6)).toBe("2.5 million");
    expect(cyclesText(350)).toBe("350");
    expect(cyclesShort(1e6)).toBe("10⁶");
    expect(cyclesShort(1e10)).toBe("10¹⁰");
  });

  it("contact faces and the print direction (material.anisotropy) become viewer markers", () => {
    const m = settingsMarkers("static", {
      load_cases: [],
      nonlinear: { contact: [{ kind: "faces", master: "f1", slave: "f2" }, { kind: "rigid", face: "bottom" }] },
      material: { name: "PLA", anisotropy: { print_direction: "+x" } },
    });
    expect(m.filter((x) => x.role === "contact").map((x) => [x.kind, x.face, x.path.join(".")])).toEqual([
      ["contact 1", "f1", "nonlinear.contact.0.master"], ["contact 1", "f2", "nonlinear.contact.0.slave"], ["contact 2", "bottom", "nonlinear.contact.1.face"],
    ]);
    expect(m.find((x) => x.role === "print")?.direction).toBe("+x");
    expect(settingsMarkers("static", { load_cases: [], material: { anisotropy: null } }).some((x) => x.role === "print")).toBe(false);
  });

  it("print direction words", () => {
    expect(printDirectionText("+z")).toBe("layers stack along +z (printed flat)");
    expect(printDirectionText("-x")).toBe("layers stack along −x (printed on its side)");
    expect(printDirectionText(null)).toMatch(/isotropic/);
  });
});
