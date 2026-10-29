// Sketch v2 pure modules: snapping + inference guides, the typed-length parser, the constraint
// solver and closed-profile detection.
import { describe, expect, it } from "vitest";
import type { SketchStroke } from "../../../api/types";
import { circumcircle, dist, fillet, outline, signedArea, strokeSegments, type Pt } from "./geom";
import { parseTyped, isTypedChar } from "./input";
import { circleStroke, lineStroke, rectStroke } from "./model";
import { closedProfiles, exportProfiles } from "./profiles";
import { snap, snapTargets, type SnapOptions } from "./snap";
import { checkConstraint, solve, type Constraint } from "./solver";

const close = (a: Pt, b: Pt, d = 1e-6) => {
  expect(a[0]).toBeCloseTo(b[0], -Math.log10(d));
  expect(a[1]).toBeCloseTo(b[1], -Math.log10(d));
};
const polyline = (id: string, pts: Pt[], closed = false): SketchStroke => ({ id, kind: "polyline", points: pts, closed, label: "" });
const arc = (id: string, a: Pt, m: Pt, b: Pt): SketchStroke => ({ id, kind: "arc", points: [a, m, b], closed: false, label: "" });

function opts(strokes: SketchStroke[], over: Partial<SnapOptions> = {}): SnapOptions {
  return { targets: snapTargets(strokes), radius: 1.5, grid: null, ...over };
}

describe("snapping: points", () => {
  const strokes = [lineStroke("a", [0, 0], [40, 0]), lineStroke("b", [20, -10], [20, 10]), circleStroke("c", [80, 50], 10)];
  it("endpoint", () => {
    const s = snap([39.4, 0.6], opts(strokes));
    expect(s).toMatchObject({ point: [40, 0], kind: "endpoint", label: "Endpoint", ref: { stroke_id: "a", vertex: 1 } });
  });
  it("midpoint of an edge that crosses nothing", () => {
    expect(snap([10.5, 0.8], opts([lineStroke("a", [0, 0], [20, 0])]))).toMatchObject({ point: [10, 0], kind: "midpoint" });
  });
  it("center", () => expect(snap([80.8, 49.5], opts(strokes))).toMatchObject({ point: [80, 50], kind: "center", ref: { stroke_id: "c", vertex: 0 } }));
  it("intersection", () => {
    const s = snap([20.9, -0.7], opts([lineStroke("a", [0, 0], [40, 0]), lineStroke("b", [20, -10], [20, 10])]));
    // the crossing (20,0) is also the midpoint of both lines; intersections win over midpoints
    expect(s.kind).toBe("intersection");
    close(s.point, [20, 0]);
  });
  it("on a circle", () => {
    const s = snap([91.2, 50], opts([circleStroke("c", [80, 50], 10)], { guides: false }));
    expect(s.kind).toBe("on");
    close(s.point, [90, 50]);
  });
  it("grid when nothing is near", () => expect(snap([12.4, 7.6], opts([], { grid: 5 }))).toMatchObject({ point: [10, 10], kind: "grid", guides: [] }));
});

describe("snapping: inference guides", () => {
  it("horizontal and vertical from the start point", () => {
    const h = snap([30, 21], opts([], { from: [0, 20] }));
    expect(h).toMatchObject({ point: [30, 20], kind: "guide", label: "Horizontal" });
    expect(h.guides).toEqual([{ kind: "horizontal", a: [0, 20], b: [30, 20], label: "Horizontal" }]);
    expect(snap([1, 33], opts([], { from: [0, 0] }))).toMatchObject({ point: [0, 33], label: "Vertical" });
  });
  it("lengths along a guide snap to the grid step", () => {
    expect(snap([40.9, 10.7], opts([], { from: [10, 10], grid: 5 }))).toMatchObject({ point: [40, 10], label: "Horizontal" });
  });
  it("45 degrees", () => {
    const s = snap([20, 21], opts([], { from: [0, 0] }));
    expect(s.label).toBe("45°");
    close(s.point, [20.5, 20.5]);
  });
  it("parallel to an existing edge", () => {
    const strokes = [lineStroke("a", [0, 0], [30, 10])];
    const s = snap([60 + 30, 50 + 10.6], opts(strokes, { from: [60, 50] }));
    expect(s.label).toBe("Parallel");
    expect(s.guides[0].kind).toBe("parallel");
    const d = [s.point[0] - 60, s.point[1] - 50];
    expect(d[1] / d[0]).toBeCloseTo(1 / 3, 6);
  });
  it("perpendicular to an existing edge", () => {
    const strokes = [lineStroke("a", [0, 0], [30, 10])];
    const s = snap([60 - 10.5, 50 + 30], opts(strokes, { from: [60, 50] }));
    expect(s.label).toBe("Perpendicular");
    const d = [s.point[0] - 60, s.point[1] - 50];
    expect(d[0] * 30 + d[1] * 10).toBeCloseTo(0, 6);
  });
  it("tangent to a circle", () => {
    const strokes = [circleStroke("c", [50, 0], 10)];
    // from (0,0): the tangent touches at angle acos(10/50) from the centre's view
    const s = snap([60, 12.6], opts(strokes, { from: [0, 0] }));
    expect(s.label).toBe("Tangent");
    const [g] = s.guides;
    expect(g.kind).toBe("tangent");
    const u = [s.point[0], s.point[1]];
    const l = Math.hypot(u[0], u[1]);
    // the distance from the centre to the guide line is the radius
    expect(Math.abs(u[0] * 0 - u[1] * 50) / l).toBeCloseTo(10, 6);
  });
  it("aligned with an existing point, and crossing two guides", () => {
    const strokes = [lineStroke("a", [40, 60], [40, 80])];
    expect(snap([40.8, 20], opts(strokes))).toMatchObject({ point: [40, 20], label: "Aligned" });
    // horizontal from (0,10) × vertical through (40,60): snap to (40,10)
    const s = snap([40.9, 10.8], opts(strokes, { from: [0, 10] }));
    expect(s.point).toEqual([40, 10]);
    expect(s.label).toBe("Horizontal · Aligned");
    expect(s.guides).toHaveLength(2);
  });
  it("near-parallel edges add no extra guide, and the last guide is sticky (no flicker)", () => {
    // an edge 1° off horizontal: only "Horizontal" is offered from the start point
    const tilted = [lineStroke("t", [0, 50], [Math.cos(Math.PI / 180) * 40, 50 + Math.sin(Math.PI / 180) * 40])];
    expect(snap([60, 10.3], opts(tilted, { from: [0, 10] })).label).toBe("Horizontal");
    // a point that would pick the 45° guide by a hair keeps showing Horizontal when that was shown last
    const prev = snap([40, 10.5], opts([], { from: [0, 10] })).guides[0];
    expect(prev.kind).toBe("horizontal");
    const r = 1.5;
    const near = snap([2, 3.1], { targets: snapTargets([]), radius: r, grid: null, from: [0, 0] });
    expect(near.label).toBe("45°");
    const held = snap([3.4, 1.7], { targets: snapTargets([]), radius: r, grid: null, from: [0, 0], prev: { kind: "horizontal", a: [0, 0], b: [3, 0], label: "Horizontal" } });
    const fresh = snap([3.4, 1.7], { targets: snapTargets([]), radius: r, grid: null, from: [0, 0] });
    expect(fresh.label).toBe("45°");
    expect(held.label).toBe("Horizontal");
  });

  it("points win over guides, guides off skips them", () => {
    const strokes = [lineStroke("a", [30, 20.5], [30, 40])];
    expect(snap([30.4, 20.9], opts(strokes, { from: [0, 20] })).kind).toBe("endpoint");
    expect(snap([25, 21], opts([], { from: [0, 20], guides: false })).kind).toBeNull();
  });
});

describe("typed values", () => {
  it.each([
    ["40", { kind: "length", mm: 40 }],
    ["12.5mm", { kind: "length", mm: 12.5 }],
    ["4 cm", { kind: "length", mm: 40 }],
    ['1"', { kind: "length", mm: 25.4 }],
    ["40,20", { kind: "pair", a: 40, b: 20 }],
    ["40x20", { kind: "pair", a: 40, b: 20 }],
    ["-40, 20", { kind: "pair", a: -40, b: 20 }],
    ["<30", { kind: "angle", deg: 30 }],
    ["40<30", { kind: "polar", mm: 40, deg: 30 }],
    ["Ø4", { kind: "diameter", mm: 4 }],
    ["d6", { kind: "diameter", mm: 6 }],
    ["R2.5", { kind: "radius", mm: 2.5 }],
  ])("%s", (text, want) => expect(parseTyped(text)).toEqual(want));
  it.each(["", "abc", "0", "-5", "40,,20", "R", "1e5"])("rejects %j", (t) => expect(parseTyped(t)).toBeNull());
  it("0,0 is a point (the origin)", () => expect(parseTyped("0,0")).toEqual({ kind: "pair", a: 0, b: 0 }));
  it("which keys go to the box", () => {
    expect(isTypedChar("4", "")).toBe(true);
    expect(isTypedChar("<", "")).toBe(true);
    expect(isTypedChar("l", "")).toBe(false);            // a tool key
    expect(isTypedChar("m", "40")).toBe(true);           // units after a number
    expect(isTypedChar("r", "")).toBe(false);
    expect(isTypedChar("r", "", true)).toBe(true);       // a radius where the step takes one
    expect(isTypedChar("Enter", "4")).toBe(false);
  });
});

describe("constraint solver", () => {
  const rectEdges = (s: SketchStroke) => [0, 1, 2, 3].map((e) => {
    const p = s.points[e];
    const q = s.points[(e + 1) % 4];
    return Math.hypot(q[0] - p[0], q[1] - p[1]);
  });

  it("rectangle with a dimension and an equal constraint becomes a square", () => {
    const r = rectStroke("r", [10, 10], [50, 30]);
    const cs: Constraint[] = [
      { id: "k1", kind: "distance", refs: [{ stroke_id: "r", edge: 0 }], value_mm: 30 },
      { id: "k2", kind: "equal", refs: [{ stroke_id: "r", edge: 0 }, { stroke_id: "r", edge: 1 }] },
    ];
    const res = solve([r], cs, new Set(["r:v0"]));
    expect(res.ok).toBe(true);
    const [s] = res.strokes;
    for (const l of rectEdges(s)) expect(l).toBeCloseTo(30, 4);
    expect(s.points[0]).toEqual([10, 10]);                               // the fixed corner stays
    expect(s.points[1][1]).toBeCloseTo(s.points[0][1], 6);               // still axis-aligned
    expect(s.points[2][0]).toBeCloseTo(s.points[1][0], 6);
  });

  it("horizontal, vertical, coincident and distance on lines", () => {
    const a = lineStroke("a", [0, 0], [30, 4]);
    const b = lineStroke("b", [31, 1], [33, 20]);
    const res = solve([a, b], [
      { id: "1", kind: "horizontal", refs: [{ stroke_id: "a", edge: 0 }] },
      { id: "2", kind: "vertical", refs: [{ stroke_id: "b", edge: 0 }] },
      { id: "3", kind: "coincident", refs: [{ stroke_id: "a", vertex: 1 }, { stroke_id: "b", vertex: 0 }] },
      { id: "4", kind: "distance", refs: [{ stroke_id: "b", edge: 0 }], value_mm: 15 },
    ], new Set(["a:v0"]));
    expect(res.ok).toBe(true);
    const [sa, sb] = res.strokes;
    expect(sa.points[0]).toEqual([0, 0]);
    expect(sa.points[1][1]).toBeCloseTo(0, 5);
    close(sa.points[1] as Pt, sb.points[0] as Pt, 1e-5);
    expect(sb.points[1][0]).toBeCloseTo(sb.points[0][0], 5);
    expect(Math.abs(sb.points[1][1] - sb.points[0][1])).toBeCloseTo(15, 5);
  });

  it("diameter and equal circles; arc radius", () => {
    const res = solve([circleStroke("c1", [0, 0], 3), circleStroke("c2", [20, 0], 7), arc("a", [40, 0], [45, 4], [50, 0])], [
      { id: "1", kind: "diameter", refs: [{ stroke_id: "c1" }], value_mm: 8 },
      { id: "2", kind: "equal", refs: [{ stroke_id: "c1" }, { stroke_id: "c2" }] },
      { id: "3", kind: "radius", refs: [{ stroke_id: "a" }], value_mm: 6 },
    ], new Set(["a:v0", "a:v2"]));
    expect(res.ok).toBe(true);
    expect(res.strokes[0].radius_mm).toBeCloseTo(4, 5);
    expect(res.strokes[1].radius_mm).toBeCloseTo(4, 5);
    expect(res.strokes[1].points).toHaveLength(64);
    const p = res.strokes[2].points as Pt[];
    expect(circumcircle(p[0], p[1], p[2])?.r).toBeCloseTo(6, 5);
    expect(p[0]).toEqual([40, 0]);
    expect(p[1][1]).toBeGreaterThan(0);                                  // same bulge side
  });

  it("conflicts are reported, not hidden", () => {
    const res = solve([lineStroke("a", [0, 0], [10, 0])], [
      { id: "1", kind: "distance", refs: [{ stroke_id: "a", edge: 0 }], value_mm: 10 },
      { id: "2", kind: "distance", refs: [{ stroke_id: "a", edge: 0 }], value_mm: 20 },
    ]);
    expect(res.ok).toBe(false);
    expect(res.status).toBe("conflict");
    expect(["1", "2"]).toContain(res.worst);
    // a square that must also be 40 × 20: names a constraint, not just "no"
    const sq = solve([rectStroke("r", [0, 0], [40, 20])], [
      { id: "eq", kind: "equal", refs: [{ stroke_id: "r", edge: 0 }, { stroke_id: "r", edge: 1 }] },
      { id: "w", kind: "distance", refs: [{ stroke_id: "r", edge: 0 }], value_mm: 40 },
      { id: "h", kind: "distance", refs: [{ stroke_id: "r", edge: 1 }], value_mm: 20 },
    ]);
    expect(sq.status).toBe("conflict");
    expect(sq.worst).not.toBeNull();
  });

  // reviewer regressions: long solvable chains converge (the old projection solver called them conflicts)
  function chain(n: number) {
    const strokes: SketchStroke[] = [];
    const cs: Constraint[] = [];
    for (let i = 0; i < n; i++) {
      strokes.push(lineStroke(`l${i}`, [i * 10, 0], [(i + 1) * 10, 0]));
      cs.push({ id: `d${i}`, kind: "distance", refs: [{ stroke_id: `l${i}`, edge: 0 }], value_mm: i ? 10 : 15 });
      cs.push({ id: `h${i}`, kind: "horizontal", refs: [{ stroke_id: `l${i}`, edge: 0 }] });
      if (i) cs.push({ id: `c${i}`, kind: "coincident", refs: [{ stroke_id: `l${i}`, vertex: 0 }, { stroke_id: `l${i - 1}`, vertex: 1 }] });
    }
    return { strokes, cs };
  }

  it("8 chained lines with lengths, horizontals and coincident ends: one length changes, the rest shift", () => {
    const { strokes, cs } = chain(8);
    const res = solve(strokes, cs, new Set(["l0:v0"]));
    expect(res.status).toBe("ok");
    expect(res.strokes[0].points[1][0]).toBeCloseTo(15, 6);
    expect(res.strokes[7].points[1][0]).toBeCloseTo(85, 6);
    for (const s of res.strokes) expect(s.points[1][1]).toBeCloseTo(0, 6);
  });

  it("a 30-edge staircase polyline with H/V and a length on every edge", () => {
    const pts: Pt[] = [[0, 0]];
    const cs: Constraint[] = [];
    for (let i = 0; i < 30; i++) {
      const [x, y] = pts[i];
      pts.push(i % 2 ? [x, y + 5] : [x + 10, y]);
      cs.push({ id: `d${i}`, kind: "distance", refs: [{ stroke_id: "p", edge: i }], value_mm: i === 0 ? 14 : i % 2 ? 5 : 10 });
      cs.push({ id: `o${i}`, kind: i % 2 ? "vertical" : "horizontal", refs: [{ stroke_id: "p", edge: i }] });
    }
    const res = solve([polyline("p", pts)], cs, new Set(["p:v0"]));
    expect(res.status).toBe("ok");
    expect(res.residual).toBeLessThan(1e-6);
    expect(res.strokes[0].points[30][0]).toBeCloseTo(154, 5);
  });

  it("an arc keeps its radius when its middle handle is the pinned point (the ends move instead)", () => {
    const res = solve([arc("a", [0, 0], [5, 3], [10, 0])], [{ id: "r", kind: "radius", refs: [{ stroke_id: "a" }], value_mm: 20 }], new Set(["a:v1"]));
    expect(res.status).toBe("ok");
    const p = res.strokes[0].points as Pt[];
    expect(p[1]).toEqual([5, 3]);
    expect(circumcircle(p[0], p[1], p[2])?.r).toBeCloseTo(20, 5);
    // and when nothing can move, it is reported, not hidden
    const stuck = solve([arc("a", [0, 0], [5, 3], [10, 0])], [{ id: "r", kind: "radius", refs: [{ stroke_id: "a" }], value_mm: 20 }], new Set(["a"]));
    expect(stuck.ok).toBe(false);
  });

  it("offsets along x / y and a point on a circle", () => {
    const res = solve([rectStroke("r", [0, 0], [40, 20]), circleStroke("h", [9, 7], 2), circleStroke("pcd", [100, 100], 30), circleStroke("b", [125, 110], 3)], [
      { id: "x", kind: "distance_x", refs: [{ stroke_id: "r", vertex: 0 }, { stroke_id: "h", vertex: 0 }], value_mm: 8 },
      { id: "y", kind: "distance_y", refs: [{ stroke_id: "r", vertex: 0 }, { stroke_id: "h", vertex: 0 }], value_mm: 8 },
      { id: "o", kind: "point_on_circle", refs: [{ stroke_id: "b", vertex: 0 }, { stroke_id: "pcd" }] },
    ], new Set(["r", "pcd"]));
    expect(res.status).toBe("ok");
    expect(res.strokes[1].center?.[0]).toBeCloseTo(8, 6);
    expect(res.strokes[1].center?.[1]).toBeCloseTo(8, 6);
    expect(dist(res.strokes[3].center as Pt, [100, 100])).toBeCloseTo(30, 5);
  });

  it("checks what a constraint can apply to", () => {
    const st = [lineStroke("a", [0, 0], [10, 0]), circleStroke("c", [0, 0], 2), { ...lineStroke("f", [0, 0], [1, 1]), kind: "freehand" as const }];
    expect(checkConstraint("horizontal", [{ stroke_id: "a", edge: 0 }], st)).toBeNull();
    expect(checkConstraint("horizontal", [{ stroke_id: "c", edge: 0 }], st)).not.toBeNull();
    expect(checkConstraint("equal", [{ stroke_id: "a", edge: 0 }, { stroke_id: "c" }], st)).not.toBeNull();
    expect(checkConstraint("diameter", [{ stroke_id: "c" }], st)).toBeNull();
    expect(checkConstraint("coincident", [{ stroke_id: "a", vertex: 0 }, { stroke_id: "f", vertex: 0 }], st)).toMatch(/Freehand/);
    // degenerate: the same point twice, or two corners of one shape (it would collapse)
    const r = rectStroke("r", [0, 0], [10, 10]);
    expect(checkConstraint("distance", [{ stroke_id: "a", vertex: 0 }, { stroke_id: "a", vertex: 0 }], st)).not.toBeNull();
    expect(checkConstraint("horizontal", [{ stroke_id: "a", vertex: 1 }, { stroke_id: "a", vertex: 1 }], st)).not.toBeNull();
    expect(checkConstraint("coincident", [{ stroke_id: "r", vertex: 0 }, { stroke_id: "r", vertex: 2 }], [r])).toMatch(/collapse/);
    expect(checkConstraint("coincident", [{ stroke_id: "a", vertex: 0 }, { stroke_id: "a", vertex: 1 }], st)).toMatch(/collapse/);
    const open = polyline("p", [[0, 0], [10, 0], [10, 10]]);
    expect(checkConstraint("coincident", [{ stroke_id: "p", vertex: 0 }, { stroke_id: "p", vertex: 2 }], [open])).toBeNull();   // closing a chain is fine
    expect(checkConstraint("diameter", [{ stroke_id: "a" }], st)).not.toBeNull();
  });
});

describe("closed profiles", () => {
  it("a rectangle with a hole", () => {
    const [p, ...rest] = closedProfiles([rectStroke("r", [0, 0], [40, 20]), circleStroke("h", [10, 10], 2)]);
    expect(rest).toEqual([]);
    expect(p.outer.strokes).toEqual(["r"]);
    expect(p.holes.map((h) => h.strokes)).toEqual([["h"]]);
    expect(p.area).toBeCloseTo(800 - Math.PI * 4, 6);
  });

  it("lines chained end to end (any direction), with an arc", () => {
    const [p] = closedProfiles([
      lineStroke("a", [0, 0], [20, 0]), lineStroke("b", [20, 0], [20, 10]), arc("c", [20, 10], [10, 20], [0, 10]), lineStroke("d", [0, 0], [0, 10]),
    ]);
    expect([...p.outer.strokes].sort()).toEqual(["a", "b", "c", "d"]);
    expect(p.area).toBeCloseTo(200 + (Math.PI * 100) / 2, 6);
  });

  it("open chains, branches, construction and freehand-open strokes are not profiles", () => {
    expect(closedProfiles([lineStroke("a", [0, 0], [20, 0]), lineStroke("b", [20, 0], [20, 10])])).toEqual([]);
    expect(closedProfiles([{ ...rectStroke("r", [0, 0], [5, 5]), construction: true }])).toEqual([]);
    // a T junction: three ends meet at one point, so the triangle isn't taken as a loop
    expect(closedProfiles([
      lineStroke("a", [0, 0], [10, 0]), lineStroke("b", [10, 0], [5, 8]), lineStroke("c", [5, 8], [0, 0]), lineStroke("t", [10, 0], [20, 0]),
    ])).toEqual([]);
  });

  it("an island inside a hole is its own profile", () => {
    const ps = closedProfiles([rectStroke("o", [0, 0], [100, 100]), rectStroke("h", [10, 10], [90, 90]), circleStroke("i", [50, 50], 10)]);
    expect(ps.map((p) => p.outer.strokes[0]).sort()).toEqual(["i", "o"]);
  });

  it("fillets and slots are exact arcs; export matches the core's shape", () => {
    const r: SketchStroke = { ...rectStroke("r", [10, 10], [50, 30]), corner_r: [0, 5, 0, 0] };
    const slot: SketchStroke = { id: "s", kind: "slot", points: [[100, 50], [130, 50]], radius_mm: 4, closed: true, label: "" };
    const [a, b] = exportProfiles(closedProfiles([r, slot]));
    expect(a.outer.strokes).toEqual(["r"]);
    expect(a.outer.segments).toContainEqual({ arc: { center: [45, 15], r: 5, start: [45, 10], end: [50, 15], ccw: true } });
    expect(a.area_mm2).toBeCloseTo(800 - 25 + (Math.PI * 25) / 4, 2);
    expect(b.area_mm2).toBeCloseTo(240 + Math.PI * 16, 2);
    expect(exportProfiles(closedProfiles([rectStroke("q", [10, 10], [50, 30])]))[0].outer.segments)
      .toEqual([{ polyline: [[10, 10], [50, 10], [50, 30], [10, 30], [10, 10]] }]);
  });
});

describe("geometry", () => {
  it("fillet tangent points and centre", () => {
    const f = fillet([0, 0], [10, 0], [10, 10], 2);
    expect(f).not.toBeNull();
    close(f!.t1, [8, 0]);
    close(f!.t2, [10, 2]);
    close(f!.c, [8, 2]);
  });
  it("arc outline passes through its 3 points; slot area", () => {
    const pts = outline(arc("a", [0, 0], [5, 5], [10, 0]));
    close(pts[0], [0, 0]);
    close(pts[pts.length - 1], [10, 0]);
    const slot: SketchStroke = { id: "s", kind: "slot", points: [[0, 0], [10, 0]], radius_mm: 1, closed: true, label: "" };
    expect(signedArea(strokeSegments(slot))).toBeCloseTo(20 + Math.PI, 6);
    expect(polyline("p", [[0, 0], [1, 0]]).kind).toBe("polyline");
  });
});
