// @vitest-environment node
// The stress histogram scales its x axis to the data: a far-away allowable line no longer
// squashes tiny stresses into one pixel at the left edge, and tiny tick values keep their digits.
import { describe, expect, it } from "vitest";
import type { StudyChart } from "../../../../api/studies";
import { minBarPx, niceTicks, prepare, tickText } from "./chartModel";

const hist = (edges: number[], counts: number[], allowable: number | null): StudyChart => ({
  id: "von_mises_histogram", kind: "histogram", title: "Stress distribution",
  x: { label: "von mises", unit: "MPa" }, y: { label: "nodes", unit: "" }, edges, counts,
  markers: allowable === null ? [] : [{ label: "allowable", x: allowable }],
});

describe("histogram axis scaling", () => {
  it("tiny stresses fill the axis; the far allowable is off-scale, not stretching it", () => {
    const edges = Array.from({ length: 21 }, (_, i) => i * 0.0005); // 0 … 0.01 MPa
    const p = prepare(hist(edges, edges.slice(1).map((_, i) => 20 - i), 35.7));
    expect(p.x.lo).toBe(0);
    expect(p.x.hi).toBeCloseTo(0.01, 9);
    expect(p.markers).toEqual([]);
    expect(p.offscale).toEqual([{ label: "allowable", x: 35.7, side: "right" }]);
  });

  it("an allowable just past the data stays on the axis", () => {
    const p = prepare(hist([0, 10, 20, 30], [5, 3, 1], 35.7));
    expect(p.markers.map((m) => m.x)).toEqual([35.7]);
    expect(p.x.hi).toBe(35.7);
    expect(p.offscale).toEqual([]);
  });

  it("an allowable far below the data is off-scale to the left", () => {
    const p = prepare(hist([100, 110, 120], [1, 1], 2));
    expect(p.x.lo).toBe(100);
    expect(p.offscale[0]).toMatchObject({ side: "left" });
  });

  it("all-equal edges (a uniform field) still give a finite, non-empty axis", () => {
    const p = prepare(hist([0.002, 0.002, 0.002], [7, 0], 35));
    expect(p.x.hi).toBeGreaterThan(p.x.lo);
    expect(Number.isFinite(p.x.lo)).toBe(true);
  });

  it("tiny ticks keep two significant digits", () => {
    expect(tickText(2.5e-5)).toBe("2.5e-5");
    expect(tickText(1e-5)).toBe("1e-5");
    expect(tickText(0.0005)).toBe("5e-4");
    // every tick label distinct
    const t = niceTicks(0, 0.0003, 6).map(tickText);
    expect(new Set(t).size).toBe(t.length);
    expect(t).toContain("2.5e-4");
  });

  it("a non-zero count is never drawn thinner than 2px", () => {
    expect(minBarPx(1, 0)).toBe(2);
    expect(minBarPx(0, 0)).toBe(0);
    expect(minBarPx(5, 40)).toBe(40);
  });
});
