import { describe, expect, it } from "vitest";
import type { StudyStreamlines } from "../../../../api/studies";
import { edgeVertexMap } from "./studyScene";
import { baseLines, cometBuffers, GAP, TRAIL, writeComets } from "./streamlines";

const line = (n: number, speed: (i: number) => number) => ({
  points: Array.from({ length: n }, (_, i) => [i, 0, 0] as [number, number, number]),
  speed: Array.from({ length: n }, (_, i) => speed(i)),
});
const set = (lines: StudyStreamlines["lines"]): StudyStreamlines => ({ lines, units: "m/s", dt_s: 1e-3, speed_max: 10, inflow: 10 });
const ramp = (t: number): [number, number, number] => [t, t, t];

describe("streamlines", () => {
  it("draws every segment, colored by the solver's speed", () => {
    const b = baseLines(set([line(3, (i) => (i === 2 ? 10 : 5))]), ramp, 1);
    expect(b.positions.length).toBe(2 * 6);
    expect(Array.from(b.positions.slice(0, 6))).toEqual([0, 0, 0, 1, 0, 0]);
    expect(b.colors[0]).toBeCloseTo(0.5);
    expect(b.colors[9]).toBeCloseTo(1);
  });
  it("moves comets one sample per clock tick along the line, so spacing is the real speed", () => {
    const s = set([line(GAP * 2, () => 10)]);
    const c = cometBuffers(s, ramp);
    expect(c.positions.length).toBe(2 * TRAIL * 6);
    writeComets(s, c, 20);
    const x0 = c.positions[0];
    writeComets(s, c, 25);
    expect(c.positions[0] - x0).toBe(5);
  });
  it("wraps a comet back to its seed", () => {
    const s = set([line(GAP, () => 10)]);
    const c = cometBuffers(s, ramp);
    writeComets(s, c, GAP + 3);
    expect(c.positions[0]).toBe(3);
  });
  it("fades the trail to nothing and gives a comet at the seed no tail", () => {
    const s = set([line(GAP, () => 10)]);
    const c = cometBuffers(s, ramp);
    writeComets(s, c, 30);
    expect(c.colors[0]).toBeCloseTo(1);
    expect(c.colors[(TRAIL - 1) * 6 + 3]).toBeCloseTo(0);
    writeComets(s, c, 0);
    expect(c.colors[0]).toBe(0);
  });
});

describe("edgeVertexMap", () => {
  it("finds each edge vertex's mesh vertex, -1 when none", () => {
    const m = edgeVertexMap([1, 0, 0, 0, 0, 0, 9, 9, 9], [0, 0, 0, 1, 0, 0]);
    expect(Array.from(m)).toEqual([3, 0, -1]);
  });
});
