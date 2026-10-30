// m7 (final review): the Studies page says each number once, in words a user reads.
import { describe, expect, it } from "vitest";
import { calWarningText } from "./accuracy/model";
import { nullMeaning, SHOWN_WHEN } from "./formSchema";
import { fmtMetric, fmtStudyMetric, metricLabel, summaryMetrics } from "./model";

describe("Studies wording (m7)", () => {
  it("fractions read as percents: load reached 1 → 100%", () => {
    expect(fmtMetric({ key: "load_fraction", label: "load reached", unit: "", value: 1 })).toBe("100%");
    expect(fmtMetric({ key: "max_plastic_strain", label: "max plastic strain", unit: "", value: 0.162 })).toBe("16.2%");
  });

  it("a solid study's error band says it is the mesh's, next to the validated model band", () => {
    expect(metricLabel({ key: "error_band_pct", label: "error band" }, "static")).toBe("mesh error band");
    expect(metricLabel({ key: "error_band_pct", label: "model error (validated)" }, "wind")).toBe("model error (validated)");
  });

  it("the fatigue section's numbers are not repeated in the summary row", () => {
    const ms = [{ key: "safety_factor", value: 2 }, { key: "fatigue_safety_factor", value: 0.976 }, { key: "cycles_to_failure", value: 873538 }];
    expect(summaryMetrics(ms, { fatigue: { cycles_to_failure: 873538 } }).map((m) => m.key)).toEqual(["safety_factor"]);
    expect(summaryMetrics(ms, {}).map((m) => m.key)).toEqual(["safety_factor", "fatigue_safety_factor", "cycles_to_failure"]);
  });

  it("OpenFOAM's iterations and cores show only with OpenFOAM chosen", () => {
    expect(SHOWN_WHEN.cpus({ solver: "auto" })).toBe(false);
    expect(SHOWN_WHEN.openfoam_iterations({ solver: "openfoam" })).toBe(true);
  });

  it("what null means keeps its brackets whole", () => {
    expect(nullMeaning("slope after yield; null = the material's (or an estimate)")).toBe("the material's (or an estimate)");
    expect(nullMeaning("size (null = auto)")).toBe("auto");
    expect(nullMeaning("no null here")).toBeNull();
  });

  it("a free-free modal study's rigid-body 'first frequency' reads as none (m13)", () => {
    const f = { key: "first_freq_hz", label: "first frequency", unit: "Hz", value: 0.00143 };
    const warn = ["free-free: no supports, so the first six modes are rigid-body modes (~0 Hz) and are left out of first_freq_hz"];
    expect(fmtStudyMetric(f, warn)).toBe("— (rigid-body modes only)");
    expect(fmtStudyMetric({ ...f, value: 412 }, warn)).toBe("412 Hz");
    expect(fmtStudyMetric(f, [])).not.toMatch(/—/);
  });

  it("calibration warnings in words", () => {
    expect(calWarningText("e: only 2 test(s): low confidence")).toBe("Stiffness (E): only 2 tests, low confidence");
    expect(calWarningText("strength: scatter (CV) 30% > 20%: low confidence")).toBe("Strength: scatter (CV) 30% > 20%, low confidence");
    expect(calWarningText("something else")).toBe("something else");
  });
});
