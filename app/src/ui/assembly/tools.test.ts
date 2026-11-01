// Plan 10 E16: the assembly's two-spot measure.
import { describe, expect, it } from "vitest";
import { measureBetween } from "./tools";
import { dirText } from "../pages/studies/StudyView";

describe("assembly measure (E16)", () => {
  it("distance, its X / Y / Z and the angle between the two surfaces", () => {
    const out = Object.fromEntries(measureBetween({ p: [0, 0, 0], n: [0, 0, 1] }, { p: [30, 40, 0], n: [1, 0, 0] }).map((v) => [v.key, v.text]));
    expect(out).toEqual({ distance: "50 mm", dx: "30 mm", dy: "40 mm", dz: "0 mm", angle: "90°" });
    const back = Object.fromEntries(measureBetween({ p: [5, 0, 0], n: null }, { p: [0, 0, 0], n: null }).map((v) => [v.key, v.text]));
    expect(back.dx).toBe("−5 mm");
    expect(back.angle).toBeUndefined();
  });
});

describe("load labels (E8)", () => {
  it("say which way a load acts", () => {
    expect(dirText([0, 0, -1])).toBe(" · −Z");
    expect(dirText("-z")).toBe(" · −Z");
    expect(dirText([3, 0, 4])).toBe(" · (0.6, 0, 0.8)");
    expect(dirText(null)).toBe("");
  });
});
