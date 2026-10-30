import { describe, expect, it } from "vitest";
import type { PartEdge } from "../../api/builds";
import type { PartFace, PartMetrics } from "../../api/types";
import { circleOf, fmtMeasure, labelFor, pickTarget, provisionalValues, refOf, shownValues, togglePick, type Picked } from "./interact";

const top: PartFace = { id: "f2", type: "plane", center: [0, 0, 2.5], normal: [0, 0, 1], area: 1200 };
const wall: PartFace = { id: "f6", type: "cylinder", center: [10, 0, 0], normal: null, area: 65.973, radius: 2.1 };
const edge40: PartEdge = { id: "e3", type: "line", length: 40, center: [20, 0, 2.5], direction: [0, 1, 0], adjacent: [] };
const ring: PartEdge = { id: "e9", type: "circle", length: 13.195, center: [12.1, 0, 2.5], direction: [0, 1, 0], adjacent: [], radius: 2.1 };
const metrics = {
  bbox: { min: [-20, -20, -2.5], max: [20, 20, 2.5], size: [40, 40, 5] }, face_list: [top, wall], edge_list: [edge40, ring],
  holes: [{ diameter_mm: 4.2, axis: [0, 0, 1], center: [10, 0, 0], depth_mm: 5 }],
} as unknown as PartMetrics;
const screen = (p: readonly number[]): [number, number] => [p[0] * 10, p[1] * 10];   // top view, 10 px/mm

describe("selection labels", () => {
  it("say what was clicked, Onshape style", () => {
    expect(labelFor({ kind: "face", face: top })).toBe("Face · planar · 1,200 mm²");
    expect(labelFor({ kind: "face", face: wall })).toBe("Face · cylinder Ø4.2");
    expect(labelFor({ kind: "face", face: { ...wall, radius: undefined } })).toBe("Face · cylinder · 65.97 mm²");
    expect(labelFor({ kind: "edge", edge: edge40 })).toBe("Edge · line · 40 mm");
    expect(labelFor({ kind: "edge", edge: ring })).toBe("Edge · circle Ø4.2");
    expect(labelFor({ kind: "vertex", point: [20, -20, 2.5] })).toBe("Vertex · 20, −20, 2.5");
    expect(labelFor({ kind: "point", point: [1.234, 0, 2.5], face: top })).toBe("Point · 1.23, 0, 2.5");
  });
});

describe("pickTarget", () => {
  const hit = (point: [number, number, number]) => ({ partId: "p", point, normal: [0, 0, 1] as [number, number, number] });
  it("prefers a vertex, then an edge, then the face under the click", () => {
    expect(pickTarget(metrics, hit([20, 19.5, 2.5]), screen)).toEqual({ kind: "vertex", point: [20, 20, 2.5] });
    expect(pickTarget(metrics, hit([19.6, 3, 2.5]), screen)).toEqual({ kind: "edge", edge: edge40 });
    expect(pickTarget(metrics, hit([0, 0, 2.5]), screen)).toEqual({ kind: "face", face: top });
  });
  it("as a point (comment mode) keeps the click and the face under it", () => {
    expect(pickTarget(metrics, hit([20, 19.5, 2.5]), screen, { asPoint: true })).toEqual({ kind: "point", point: [20, 19.5, 2.5], face: top });
  });
});

describe("measure picks", () => {
  const a: Picked = { kind: "edge", edge: edge40 };
  const b: Picked = { kind: "face", face: top };
  it("refs name the metrics ids, vertices as points", () => {
    expect(refOf(a)).toEqual({ edge_id: "e3" });
    expect(refOf(b)).toEqual({ face_id: "f2" });
    expect(refOf({ kind: "vertex", point: [1, 2, 3] })).toEqual({ point: [1, 2, 3] });
  });
  it("a click replaces, a shift-click adds (or drops a pick made twice)", () => {
    expect(togglePick([a], b, false)).toEqual([b]);
    expect(togglePick([a], b, true)).toEqual([a, b]);
    expect(togglePick([a, b], { kind: "edge", edge: edge40 }, true)).toEqual([b]);
  });
  it("one pick shows its metrics values at once (the core's exact values replace them)", () => {
    expect(provisionalValues([a]).map((v) => fmtMeasure(v))).toEqual(["Length 40 mm"]);
    expect(provisionalValues([{ kind: "edge", edge: ring }]).map((v) => fmtMeasure(v))).toEqual(["Diameter 4.2 mm", "Radius 2.1 mm", "Length 13.195 mm"]);
    expect(provisionalValues([b]).map((v) => fmtMeasure(v))).toEqual(["Area 1,200 mm²"]);
    expect(provisionalValues([a, b])).toEqual([]);
  });
  it("formats and drops a normal distance equal to the distance", () => {
    const vals = [
      { key: "normal_distance", label: "Normal distance", value: 5, unit: "mm" }, { key: "distance", label: "Distance", value: 5, unit: "mm" },
      { key: "angle", label: "Angle", value: 0, unit: "°" }, { key: "dx", label: "ΔX", value: 72.1110255, unit: "mm" },
    ];
    expect(shownValues(vals).map(fmtMeasure)).toEqual(["Distance 5 mm", "Angle 0°", "ΔX 72.111 mm"]);
  });
});

describe("circleOf", () => {
  it("a hole's edge is a ring around the hole's axis", () => {
    expect(circleOf(ring, metrics.holes)).toEqual({ center: [10, 0, 2.5], axis: [0, 0, 1], radius: 2.1 });
    expect(circleOf(edge40, metrics.holes)).toBeNull();
  });
});
