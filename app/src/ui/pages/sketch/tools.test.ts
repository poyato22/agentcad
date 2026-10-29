// Sketch v2: tools (clicks, typed steps, hints), editing (mirror, offset, fillet), templates,
// constraint badges, and the page state's drawing / constraint flows.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { SketchStroke } from "../../../api/types";
import { constraintBadges } from "./badges";
import { mirror, offset, setCorner, toggleConstruction } from "./edit";
import { circumcircle, dist, type Pt } from "./geom";
import { parseTyped, type Typed } from "./input";
import { circleStroke, lineStroke, rectStroke } from "./model";
import { closedProfiles } from "./profiles";
import { solve } from "./solver";
import {
  doc, draft, extrudeMm, filletR, message, picks, preview, resetSketch, selectedConstraint, sketchActions, tool,
} from "./state";
import { template, TEMPLATES } from "./templates";
import { clicksFor, draftShape, hint, TOOLS, typedStep, type Tool } from "./tools";

const typedOf = (t: string) => parseTyped(t) as Typed;

describe("tools", () => {
  it("every tool has a key and a hint of at most 8 words per step", () => {
    expect(new Set(TOOLS.map((t) => t.key)).size).toBe(TOOLS.length);
    for (const t of TOOLS) {
      for (let n = 0; n < 4; n++) {
        const words = hint(t.id as Tool, n).split(/\s+/).filter((w) => w !== "·");
        expect(words.length, `${t.id} step ${n}: ${hint(t.id, n)}`).toBeLessThanOrEqual(8);
      }
    }
  });

  it("clicks per tool, and the drafted shapes", () => {
    expect([clicksFor("line"), clicksFor("rect"), clicksFor("arc"), clicksFor("slot"), clicksFor("polyline")]).toEqual([2, 2, 3, 3, Infinity]);
    expect(draftShape("crect", [[50, 50]], [60, 55])?.points).toEqual([[40, 45], [60, 45], [60, 55], [40, 55]]);
    const arc = draftShape("arc", [[0, 0], [10, 0]], [5, 5]);
    expect(arc?.kind).toBe("arc");
    expect(circumcircle(...(arc?.points as [Pt, Pt, Pt]))?.r).toBeCloseTo(5, 6);
    const slot = draftShape("slot", [[0, 0], [20, 0]], [10, 3]);
    expect(slot).toMatchObject({ kind: "slot", radius_mm: 3, points: [[0, 0], [20, 0]] });
    expect(draftShape("circle", [[0, 0]], [3.2, 0], "d", 5)?.radius_mm).toBe(5);
  });

  it("typed values: lengths along the pointer, pairs, angles, sizes", () => {
    expect(typedStep("line", [[10, 10]], [20, 10], typedOf("40"))).toEqual({ click: [50, 10], drive: [{ kind: "distance", edge: 0, value: 40 }] });
    const polar = typedStep("line", [[0, 0]], [5, 5], typedOf("10<90"));
    expect(polar?.click[0]).toBeCloseTo(0, 9);
    expect(polar?.click[1]).toBeCloseTo(10, 9);
    expect(typedStep("line", [[0, 0]], [5, 5], typedOf("3,4"))?.click).toEqual([3, 4]);
    // a rectangle goes the way the pointer is
    expect(typedStep("rect", [[20, 20]], [10, 30], typedOf("40,20"))).toEqual({
      click: [-20, 40], drive: [{ kind: "distance", edge: 0, value: 40 }, { kind: "distance", edge: 1, value: 20 }],
    });
    expect(typedStep("crect", [[0, 0]], [1, 1], typedOf("40,20"))?.click).toEqual([20, 10]);
    expect(typedStep("circle", [[0, 0]], [1, 0], typedOf("Ø4"))).toEqual({ click: [2, 0], drive: [{ kind: "diameter", value: 4 }] });
    expect(typedStep("circle", [[0, 0]], [1, 0], typedOf("4"))?.click).toEqual([2, 0]);              // a plain number is the diameter
    expect(typedStep("circle", [[0, 0]], [0, 1], typedOf("R3"))?.click).toEqual([0, 3]);
    expect(typedStep("circle", [[0, 0]], [0, 1], typedOf("<30"))).toBeNull();
    const arc = typedStep("arc", [[0, 0], [10, 0]], [5, 3], typedOf("R5"));
    expect(arc?.click[0]).toBeCloseTo(5, 9);
    expect(arc?.click[1]).toBeCloseTo(5, 9);                                                       // a half circle, on the pointer's side
    expect(typedStep("slot", [[0, 0], [20, 0]], [10, 2], typedOf("8"))).toEqual({ click: [0, 4], drive: [{ kind: "radius", value: 4 }] });
  });
});

describe("editing", () => {
  it("mirror about a vertical line keeps a rectangle; a slanted line turns it into a polyline", () => {
    const r = rectStroke("r", [10, 10], [20, 30]);
    const m = mirror(r, [50, 0], [50, 100], "m");
    expect(m).toMatchObject({ id: "m", kind: "rect", points: [[90, 10], [80, 10], [80, 30], [90, 30]] });
    // fillets stay on the same corners
    expect(mirror({ ...r, corner_r: [0, 2, 0, 0] }, [50, 0], [50, 100], "f").corner_r).toEqual([0, 2, 0, 0]);
    const d = mirror(r, [0, 0], [1, 1], "d");
    expect(d.kind).toBe("polyline");
    expect(d.points[1][0]).toBeCloseTo(10, 9);                   // (20,10) → (10,20)
    expect(d.points[1][1]).toBeCloseTo(20, 9);
    const c = mirror(circleStroke("c", [10, 0], 2), [0, 0], [0, 1], "c2");
    expect(c.center).toEqual([-10, 0]);
  });

  it("offset outward: rectangles grow on every side, circles get bigger, lines move left", () => {
    expect(offset(rectStroke("r", [0, 0], [10, 10]), 2, "o")?.points).toEqual([[-2, -2], [12, -2], [12, 12], [-2, 12]]);
    expect(offset(rectStroke("r", [0, 0], [10, 10]), -2, "o")?.points).toEqual([[2, 2], [8, 2], [8, 8], [2, 8]]);
    expect(offset(circleStroke("c", [0, 0], 5), 1, "o")?.radius_mm).toBe(6);
    expect(offset(circleStroke("c", [0, 0], 5), -6, "o")).toBeNull();
    expect(offset(lineStroke("l", [0, 0], [10, 0]), 3, "o")?.points).toEqual([[0, 3], [10, 3]]);
  });

  it("fillet corners only where there is a corner; construction toggles", () => {
    const r = setCorner(rectStroke("r", [0, 0], [10, 10]), 2, 3);
    expect(r?.corner_r).toEqual([0, 0, 3, 0]);
    const p: SketchStroke = { id: "p", kind: "polyline", points: [[0, 0], [10, 0], [10, 10]], closed: false, label: "" };
    expect(setCorner(p, 0, 3)).toBeNull();                       // an open end is not a corner
    expect(setCorner(p, 1, 3)?.corner_r).toEqual([0, 3, 0]);
    expect(setCorner(lineStroke("l", [0, 0], [1, 0]), 0, 1)).toBeNull();
    expect(toggleConstruction(lineStroke("l", [0, 0], [1, 0])).construction).toBe(true);
    expect("construction" in toggleConstruction(toggleConstruction(lineStroke("l", [0, 0], [1, 0])))).toBe(false);
  });
});

describe("templates", () => {
  it.each(TEMPLATES.map((t) => t.id))("%s is consistent: its constraints already hold and it has a closed profile", (id) => {
    let n = 0;
    let k = 0;
    const t = template(id, () => `s${++n}`, () => `k${++k}`);
    const res = solve(t.strokes, t.constraints);
    expect(res.ok).toBe(true);
    expect(res.residual).toBeLessThan(1e-6);
    const profiles = closedProfiles(t.strokes);
    expect(profiles.length).toBe(1);
    expect(t.extrude_mm).toBeGreaterThan(0);
  });

  it("templates are fully dimensioned: holes follow the plate's size and the bolt circle's diameter", () => {
    let n = 0;
    let k = 0;
    const plate = template("plate", () => `s${++n}`, () => `k${++k}`);
    const wide = plate.constraints.map((c) => (c.kind === "distance" && c.value_mm === 80 ? { ...c, value_mm: 120 } : c));
    const p = solve(plate.strokes, wide, new Set([`${plate.strokes[0].id}:v0`]));
    expect(p.status).toBe("ok");
    expect(p.strokes[0].points[1][0]).toBeCloseTo(140, 5);
    expect(p.strokes[2].center?.[0]).toBeCloseTo(132, 5);             // still 8 mm in from the moved corner
    const fl = template("flange", () => `s${++n}`, () => `k${++k}`);
    const pcdId = fl.strokes[2].id;
    const big = fl.constraints.map((c) => (c.kind === "diameter" && c.refs[0].stroke_id === pcdId ? { ...c, value_mm: 70 } : c));
    const f = solve(fl.strokes, big, new Set([`${fl.strokes[0].id}:v0`]));
    expect(f.status).toBe("ok");
    for (const h of f.strokes.slice(3, 7)) expect(dist(h.center as Pt, [100, 75])).toBeCloseTo(35, 4);
  });

  it("plate has 4 holes, flange has a bore and 4 bolt holes (the bolt circle is construction)", () => {
    let n = 0;
    const plate = closedProfiles(template("plate", () => `s${++n}`, () => "k").strokes)[0];
    expect(plate.holes).toHaveLength(4);
    const fl = template("flange", () => `s${++n}`, () => "k");
    expect(fl.strokes.filter((s) => s.construction)).toHaveLength(2);          // the bolt circle and the square through the bolts
    expect(closedProfiles(fl.strokes)[0].holes).toHaveLength(5);
  });
});

describe("badges", () => {
  it("dimension values, H / V / =, a dot on coincident points", () => {
    const r = rectStroke("r", [0, 0], [40, 20]);
    const l = lineStroke("l", [40, 20], [60, 40]);
    const c = circleStroke("c", [10, 10], 2);
    const b = constraintBadges([r, l, c], [
      { id: "1", kind: "distance", refs: [{ stroke_id: "r", edge: 0 }], value_mm: 40 },
      { id: "2", kind: "horizontal", refs: [{ stroke_id: "r", edge: 2 }] },
      { id: "3", kind: "equal", refs: [{ stroke_id: "r", edge: 1 }, { stroke_id: "r", edge: 3 }] },
      { id: "4", kind: "coincident", refs: [{ stroke_id: "l", vertex: 0 }, { stroke_id: "r", vertex: 2 }] },
      { id: "5", kind: "diameter", refs: [{ stroke_id: "c" }], value_mm: 4 },
      { id: "6", kind: "distance", refs: [{ stroke_id: "gone", edge: 0 }], value_mm: 1 },
    ], 0.25);
    expect(b.map((x) => [x.id, x.text])).toEqual([["1", "40"], ["2", "H"], ["3", "="], ["3", "="], ["4", ""], ["5", "Ø4"]]);
    // the 40 sits below the bottom edge (outside the rectangle), on a dimension line
    expect(b[0].at[1]).toBeLessThan(0);
    expect(b[0].line?.[0][0]).toBeCloseTo(0, 6);
    expect(b[0].line?.[1][0]).toBeCloseTo(40, 6);
    expect(b[4].at).toEqual([40, 20]);
  });
});

describe("state: drawing and constraints", () => {
  beforeEach(() => resetSketch());
  afterEach(() => resetSketch());

  it("click-click draws; typed values become driving dimensions", () => {
    sketchActions.setTool("rect");
    sketchActions.drawClick([20, 20]);
    expect(draft.value?.clicks).toEqual([[20, 20]]);
    sketchActions.setTyped("40,20");
    expect(sketchActions.commitTyped()).toBe(true);
    const [r] = doc.value.strokes;
    expect(r.points).toEqual([[20, 20], [60, 20], [60, 40], [20, 40]]);
    expect(doc.value.constraints.map((c) => [c.kind, c.value_mm])).toEqual([["distance", 40], ["distance", 20]]);
    expect(draft.value).toBeNull();
    expect(picks.value).toEqual([{ stroke_id: r.id, edge: 0 }]);
  });

  it("draws by keyboard alone: x,y for the first point, then a size", () => {
    sketchActions.setTool("circle");
    sketchActions.setTyped("30,40");
    expect(sketchActions.commitTyped()).toBe(true);
    expect(draft.value?.clicks).toEqual([[30, 40]]);
    sketchActions.setTyped("Ø10");
    expect(sketchActions.commitTyped()).toBe(true);
    expect(doc.value.strokes[0]).toMatchObject({ kind: "circle", center: [30, 40], radius_mm: 5 });
    sketchActions.setTyped("12");
    sketchActions.setTool("line");
    expect(sketchActions.commitTyped()).toBe(false);          // a single number is not a point
  });

  it("a polyline closes on its first point, and lines starting on an end are coincident", () => {
    sketchActions.setTool("polyline");
    for (const p of [[0, 0], [30, 0], [30, 20]] as Pt[]) sketchActions.drawClick(p);
    sketchActions.drawClick([0, 0]);
    const [p] = doc.value.strokes;
    expect(p).toMatchObject({ kind: "polyline", closed: true, points: [[0, 0], [30, 0], [30, 20]] });
    expect(closedProfiles(doc.value.strokes)).toHaveLength(1);
    sketchActions.setTool("line");
    sketchActions.drawClick([30, 20], { ref: { stroke_id: p.id, vertex: 2 } });
    sketchActions.drawClick([60, 20], { guide: "horizontal" });
    expect(doc.value.constraints.map((c) => c.kind)).toEqual(["coincident", "horizontal"]);
  });

  it("dimension labels drive lines and circles; a conflicting one stays a label", () => {
    sketchActions.addStroke(lineStroke(sketchActions.newStrokeId(), [0, 0], [30, 0]));
    const id = doc.value.strokes[0].id;
    sketchActions.setDimension(id, "50");
    expect(dist(doc.value.strokes[0].points[0] as Pt, doc.value.strokes[0].points[1] as Pt)).toBeCloseTo(50, 5);
    expect(doc.value.constraints).toEqual([{ id: `dim-${id}`, kind: "distance", refs: [{ stroke_id: id, edge: 0 }], value_mm: 50 }]);
    // a second, different length on the same edge can't hold: refused, with a message
    expect(sketchActions.addConstraint("distance", [{ stroke_id: id, edge: 0 }], 20)).toMatch(/conflicts/);
    expect(doc.value.constraints).toHaveLength(1);
    sketchActions.addStroke(circleStroke(sketchActions.newStrokeId(), [50, 50], 3));
    const cid = doc.value.strokes[1].id;
    sketchActions.setDimension(cid, "Ø12");
    expect(doc.value.strokes[1].radius_mm).toBeCloseTo(6, 5);
  });

  it("equal + dimension on a rectangle (by picks), then remove the constraint", () => {
    sketchActions.addStroke(rectStroke(sketchActions.newStrokeId(), [0, 0], [40, 20]));
    const id = doc.value.strokes[0].id;
    sketchActions.setEdgeLength(id, 0, 30);
    sketchActions.select(id, false, { stroke_id: id, edge: 0 });
    sketchActions.select(id, true, { stroke_id: id, edge: 1 });
    expect(sketchActions.addConstraint("equal")).toBeNull();
    const pts = doc.value.strokes[0].points;
    expect(dist(pts[0] as Pt, pts[1] as Pt)).toBeCloseTo(30, 4);
    expect(dist(pts[1] as Pt, pts[2] as Pt)).toBeCloseTo(30, 4);
    const eq = selectedConstraint.value as string;
    expect(doc.value.constraints.find((c) => c.id === eq)?.kind).toBe("equal");
    expect(sketchActions.deleteSelection()).toBe(true);
    expect(doc.value.constraints.map((c) => c.kind)).toEqual(["distance"]);
  });

  it("a drag whose solve fails keeps the last good shape, says why, and commits nothing", () => {
    sketchActions.addStroke(lineStroke(sketchActions.newStrokeId(), [0, 0], [10, 0]));
    const id = doc.value.strokes[0].id;
    // a good drag first: the preview holds the solved shape
    expect(sketchActions.dragVertex({ stroke_id: id, vertex: 1 }, [20, 0])).toBe(true);
    const good = preview.value;
    // now the sketch holds two lengths that can't both be true (as if loaded so): the next move fails
    doc.value = { ...doc.value, constraints: [
      { id: "a", kind: "distance", refs: [{ stroke_id: id, edge: 0 }], value_mm: 10 },
      { id: "b", kind: "distance", refs: [{ stroke_id: id, edge: 0 }], value_mm: 20 },
    ] };
    expect(sketchActions.dragVertex({ stroke_id: id, vertex: 1 }, [30, 0])).toBe(false);
    expect(preview.value).toBe(good);                                  // the last good shape stays shown
    expect(message.value).toMatch(/conflicts with the (10|20) mm length constraint/);
    sketchActions.endDrag(false);
    const before = JSON.stringify(doc.value.strokes);
    expect(sketchActions.dragVertex({ stroke_id: id, vertex: 1 }, [30, 0])).toBe(false);
    sketchActions.endDrag(true);                                       // nothing good to commit
    expect(JSON.stringify(doc.value.strokes)).toBe(before);
  });

  it("a vertex drag on a 500-rectangle chain stays near its 60 ms budget", () => {
    const strokes = Array.from({ length: 500 }, (_, i) => rectStroke(`r${i}`, [i * 10, 0], [i * 10 + 10, 8]));
    const constraints = strokes.slice(1).map((s, i) => ({ id: `c${i}`, kind: "coincident" as const, refs: [{ stroke_id: strokes[i].id, vertex: 1 }, { stroke_id: s.id, vertex: 0 }] }));
    doc.value = { ...doc.value, strokes, constraints };
    sketchActions.dragVertex({ stroke_id: "r0", vertex: 1 }, [11, 0]);     // warm up
    sketchActions.endDrag(false);
    const t0 = performance.now();
    sketchActions.dragVertex({ stroke_id: "r0", vertex: 1 }, [12, 0]);
    expect(performance.now() - t0).toBeLessThan(90);
    sketchActions.endDrag(false);
  });

  it("Backspace drops the last click; typed points out of range are refused; arrows nudge", () => {
    sketchActions.setTool("polyline");
    for (const p of [[0, 0], [10, 0], [10, 10]] as Pt[]) sketchActions.drawClick(p);
    sketchActions.undoClick();
    expect(draft.value?.clicks).toEqual([[0, 0], [10, 0]]);
    sketchActions.undoClick();
    sketchActions.undoClick();
    expect(draft.value).toBeNull();
    sketchActions.setTool("line");
    sketchActions.setTyped("999999,0");
    expect(sketchActions.commitTyped()).toBe(false);
    expect(message.value).toMatch(/outside the drawing range/);
    sketchActions.setTyped("0,0");
    expect(sketchActions.commitTyped()).toBe(true);                     // the origin is a fine first point
    sketchActions.setTyped("4000m");
    expect(sketchActions.commitTyped()).toBe(false);
    sketchActions.cancelDraft();
    sketchActions.addStroke(rectStroke(sketchActions.newStrokeId(), [0, 0], [40, 20]));
    const id = doc.value.strokes[0].id;
    sketchActions.select(id, false, { stroke_id: id, vertex: 2 });
    expect(sketchActions.nudge(10, 0)).toBe(true);
    expect(doc.value.strokes[0].points[2]).toEqual([50, 20]);
    expect(doc.value.strokes[0].points[1][0]).toBeCloseTo(50, 6);      // still a rectangle
    sketchActions.select(id);
    expect(sketchActions.nudge(0, 1)).toBe(true);
    expect(doc.value.strokes[0].points[0]).toEqual([0, 1]);
  });

  it("vertex drags preview, then commit as one undo step", () => {
    sketchActions.addStroke(rectStroke(sketchActions.newStrokeId(), [0, 0], [40, 20]));
    const id = doc.value.strokes[0].id;
    sketchActions.dragVertex({ stroke_id: id, vertex: 2 }, [50, 30]);
    sketchActions.dragVertex({ stroke_id: id, vertex: 2 }, [60, 35]);
    expect(doc.value.strokes[0].points[2]).toEqual([40, 20]);              // not committed yet
    sketchActions.endDrag(true);
    expect(doc.value.strokes[0].points[2]).toEqual([60, 35]);
    expect(doc.value.strokes[0].points[1][0]).toBeCloseTo(60, 6);         // still a rectangle
    sketchActions.undo();
    expect(doc.value.strokes[0].points[2]).toEqual([40, 20]);
  });

  it("mirror, offset, fillet, construction and templates through the actions", () => {
    sketchActions.addStroke(rectStroke(sketchActions.newStrokeId(), [10, 10], [20, 20]));
    sketchActions.addStroke(lineStroke(sketchActions.newStrokeId(), [50, 0], [50, 100]));
    const [r, axis] = doc.value.strokes;
    sketchActions.select(axis.id);
    sketchActions.toggleConstruction();
    sketchActions.select(r.id, true);
    expect(sketchActions.mirrorSelection()).toBeNull();
    expect(doc.value.strokes).toHaveLength(3);
    expect(doc.value.strokes[2].points[1]).toEqual([80, 10]);
    sketchActions.select(r.id);
    expect(sketchActions.offsetSelection(1)).toBeNull();
    expect(doc.value.strokes[3].points[0]).toEqual([9, 9]);
    filletR.value = 2;
    expect(sketchActions.filletCorner({ stroke_id: r.id, vertex: 1 })).toBe(true);
    expect(doc.value.strokes[0].corner_r).toEqual([0, 2, 0, 0]);
    expect(sketchActions.filletCorner({ stroke_id: axis.id, vertex: 0 })).toBe(false);
    expect(message.value).toMatch(/corner/);
    sketchActions.insertTemplate("flange");
    expect(extrudeMm.value).toBe(8);
    // placed clear of what was already drawn
    const flange = doc.value.strokes.slice(4);
    const minX = Math.min(...flange.flatMap((s) => s.points.map((p) => p[0])));
    const maxOld = Math.max(...doc.value.strokes.slice(0, 4).flatMap((s) => s.points.map((p) => p[0])));
    expect(minX).toBeGreaterThan(maxOld);
    expect(doc.value.strokes.length).toBe(4 + 8);
    expect(tool.value).toBe("pen");
  });
});
