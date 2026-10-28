import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { simRecordSchema, simSummarySchema, type SimSummary } from "../../../api/sims";
import { isAutoNumbered } from "../../../lib/names";
import {
  applyPreset, askPrompt, DEFAULT_SETUP, ENV_PRESETS, fmtBand, fmtSf, legendGradient, legendTicks, MATERIALS, setupProblems,
  sfVerdict, sig3, stageFraction, stageText, startBody, studyLabel, verdictTone, SIM_DIRECTIONS, SIM_FACE_PRESETS,
} from "./model";

const FIX = resolve(import.meta.dirname, "../../../../../core/tests/fixtures/ui");
const fx = (n: string): unknown => JSON.parse(readFileSync(resolve(FIX, n), "utf8"));

describe("presets mirror the real core (sim_env_list)", () => {
  const env = fx("sim_env.json") as {
    materials: { name: string }[]; face_presets: string[]; directions: string[];
    environments: { name: string; temperature_c: number; notes: string; suggested: { fix: string; load: { face: string; newtons: number; direction: string } } }[];
  };
  it("materials, faces, directions", () => {
    expect([...MATERIALS]).toEqual(env.materials.map((m) => m.name));
    expect([...SIM_FACE_PRESETS]).toEqual(env.face_presets);
    expect([...SIM_DIRECTIONS]).toEqual(env.directions);
  });
  it("environment presets and their suggested setups", () => {
    expect(ENV_PRESETS.map((p) => ({
      name: p.name, temperature_c: p.temperature_c, notes: p.notes,
      suggested: { fix: p.fix, load: { face: p.face, newtons: p.newtons, direction: p.direction } },
    }))).toEqual(env.environments.map(({ name, temperature_c, notes, suggested }) => ({ name, temperature_c, notes, suggested })));
  });
});

describe("setup", () => {
  it("a preset fills faces, force and direction and keeps the material", () => {
    const s = applyPreset({ ...DEFAULT_SETUP, material: "PETG", fix: "x-min" }, "Bike mount");
    expect(s).toEqual({ fix: "bottom", face: "top", newtons: 50, direction: "-z", material: "PETG", environment: "Bike mount" });
    expect(applyPreset(s, "none").environment).toBeNull();
  });

  it("flags what the core would reject", () => {
    expect(setupProblems(DEFAULT_SETUP)).toEqual([]);
    expect(setupProblems({ ...DEFAULT_SETUP, face: "bottom" })).toEqual(["the fixed face and the load face are the same: hold one face and load another"]);
    expect(setupProblems({ ...DEFAULT_SETUP, newtons: 0 })).toEqual(["the force must be more than 0 N"]);
    expect(setupProblems({ ...DEFAULT_SETUP, newtons: Number.NaN })).toHaveLength(1);
    expect(setupProblems({ ...DEFAULT_SETUP, newtons: 2e6 })).toEqual(["the force must be at most 1,000,000 N"]);
    expect(setupProblems({ ...DEFAULT_SETUP, fix: "f12" })).toEqual([]);
    expect(setupProblems({ ...DEFAULT_SETUP, fix: "middle" })).toHaveLength(1);
  });

  it("start body is exactly the core's StaticArgs", () => {
    expect(startBody("mount_plate", { ...DEFAULT_SETUP, environment: "Desk" })).toEqual({
      part_id: "mount_plate", fix: "bottom", load: { face: "top", newtons: 20, direction: "-z" }, material: "PLA", environment: "Desk",
    });
  });
});

describe("verdicts", () => {
  it("safety factor thresholds 2.0 / 1.2", () => {
    expect([2.0, 3.17, 1.99, 1.2, 1.19, 0.4].map(sfVerdict)).toEqual(["pass", "pass", "warn", "warn", "fail", "fail"]);
    expect(sfVerdict(null)).toBeNull();
    expect(sfVerdict(Number.POSITIVE_INFINITY)).toBeNull();
    expect(["pass", "warn", "fail", null].map((v) => verdictTone(v as never))).toEqual(["green", "amber", "red", "faint"]);
    expect(fmtSf(3.165770915716302)).toBe("3.17");
    expect(fmtSf(null)).toBe("n/a");
  });

  it("numbers read like the core summary", () => {
    expect(sig3(10.392413372891163)).toBe("10.4");
    expect(sig3(0.8316638894083745)).toBe("0.832");
    expect(sig3(20)).toBe("20");
    expect(sig3(16752)).toBe("16,752");
    expect(fmtBand({ error_band: 0.0507, error_band_pct: 5.1 })).toBe("±5.1%");
    expect(fmtBand({ error_band: null, error_band_pct: null })).toBe("n/a");
  });

  it("progress walks the six stages", () => {
    expect([null, "queued", "mesh_coarse", "solve_fine", "post", "done"].map((s) => stageFraction(s as never))).toEqual([0, 0, 1 / 6, 4 / 6, 5 / 6, 1]);
    expect(stageText("solve_fine")).toBe("solving (fine)");
  });

  it("legend spans 0..max with the core's color stops", () => {
    expect(legendTicks(0, 10)).toEqual([0, 2.5, 5, 7.5, 10]);
    expect(legendGradient()).toBe("linear-gradient(to top, rgb(13, 64, 242) 0%, rgb(0, 204, 242) 25%, rgb(26, 204, 51) 50%, rgb(250, 217, 26) 75%, rgb(230, 31, 26) 100%)");
  });
});

describe("names", () => {
  const list = fx("sims_list.json") as SimSummary[];
  it("history rows say what differs, never a counter", () => {
    const parsed = list.map((s) => simSummarySchema.parse(s));
    const a = studyLabel(parsed[1], [parsed[1]]);
    expect(a).toBe("Study · PETG · 20 N −z on x-max · fixed x-min");
    // two studies with the same setup get their start time, not a number
    const two = studyLabel(parsed[1], parsed);
    expect(two).toMatch(/^Study · \d\d:\d\d(:\d\d)? · PETG · 20 N −z on x-max · fixed x-min$/);
    expect(isAutoNumbered(two)).toBe(false);
  });
});

describe("ask agent to fix", () => {
  it("sends the result, the guide-only label and the fixture-including safety factor", () => {
    const rec = simRecordSchema.parse(fx("sim_done.json"));
    const p = askPrompt(rec);
    expect(p).toContain("guide only, not a guarantee");
    expect(p).toContain("max von Mises 10.4 MPa");
    expect(p).toContain("Safety factor 3.17 (peak excludes the fixed face); including the fixed face 3.17; error band ±5.1%");
    expect(p).toContain("fixed face x-min; load 20 N -z on x-max; material PETG; environment Desk");
    expect(p).toContain("at least 2.0");
  });
  it("a failed study sends its error", () => {
    const rec = simRecordSchema.parse(fx("sim_failed.json"));
    expect(askPrompt(rec)).toContain("The study failed: CalculiX (ccx) is not installed (install it with: conda install -c conda-forge calculix).");
  });
});
