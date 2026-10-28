import { describe, expect, it } from "vitest";
import {
  circleStroke, clientToMm, closeIfNear, emptyDoc, endpoints, freehandStroke, hitStroke, History, labelAnchor, lineStroke, measuredLength,
  MAX_TOTAL_POINTS, parseDimension, rectStroke, SHEET, simplify, snapPoint, totalPoints, toUpload, withinPointBudget, type SketchDoc, type Stroke,
} from "./model";

const RECT = { left: 100, top: 50, width: 400, height: 300 }; // exactly 2 px per mm for a 200 x 150 mm sheet

describe("clientToMm", () => {
  it("converts screen px to mm with the origin bottom-left and +y up", () => {
    expect(SHEET).toEqual({ width_mm: 200, height_mm: 150 });
    expect(clientToMm(100, 350, RECT)).toEqual([0, 0]);
    expect(clientToMm(500, 50, RECT)).toEqual([200, 150]);
    expect(clientToMm(300, 200, RECT)).toEqual([100, 75]);
    expect(clientToMm(120, 330, RECT)).toEqual([10, 10]);
  });

  it("handles the letterbox of a non-matching aspect (xMidYMid meet)", () => {
    // 600 x 300 box: scale is min(3, 2) = 2 px/mm, sheet 400 px wide, centred → 100 px bars
    const wide = { left: 0, top: 0, width: 600, height: 300 };
    expect(clientToMm(100, 300, wide)).toEqual([0, 0]);
    expect(clientToMm(500, 0, wide)).toEqual([200, 150]);
  });

  it("top-aligns the sheet in a tall box (xMidYMin meet)", () => {
    // 400 x 600 box: scale 2, the sheet is 300 px tall at the top; the rest is margin below
    const tall = { left: 0, top: 0, width: 400, height: 600 };
    expect(clientToMm(0, 300, tall)).toEqual([0, 0]);
    expect(clientToMm(400, 0, tall)).toEqual([200, 150]);
  });

  it("clamps to the sheet", () => {
    expect(clientToMm(0, 0, RECT)).toEqual([0, 150]);
    expect(clientToMm(9999, 9999, RECT)).toEqual([200, 0]);
  });
});

describe("snapPoint", () => {
  const ctx = { grid: 5, endpoints: [[12.2, 30.4]] as [number, number][], radius: 2 };

  it("snaps to a nearby endpoint before the grid", () => {
    expect(snapPoint([13, 31], ctx)).toEqual({ point: [12.2, 30.4], kind: "endpoint" });
  });

  it("falls back to the grid", () => {
    expect(snapPoint([41.2, 18.9], ctx)).toEqual({ point: [40, 20], kind: "grid" });
  });

  it("does nothing when snapping is off", () => {
    expect(snapPoint([41.2, 18.9], { grid: null, endpoints: [], radius: 2 })).toEqual({ point: [41.2, 18.9], kind: null });
  });

  it("snaps a line to 0/45/90 degrees from its start", () => {
    const s = snapPoint([50, 11.5], { ...ctx, endpoints: [], from: [10, 10], grid: null });
    expect(s.kind).toBe("angle");
    expect(s.point[1]).toBeCloseTo(10);
    expect(s.point[0]).toBeCloseTo(Math.hypot(40, 1.5) + 10, 5);
    const diag = snapPoint([30.5, 29.5], { grid: null, endpoints: [], radius: 2, from: [10, 10] });
    expect(diag.kind).toBe("angle");
    expect(diag.point[0] - 10).toBeCloseTo(diag.point[1] - 10, 6);
    // far from any snap angle: free
    expect(snapPoint([30, 20], { grid: null, endpoints: [], radius: 2, from: [10, 10] }).kind).toBe(null);
  });

  it("grid then angle: a near-horizontal line ends on the grid and stays horizontal", () => {
    const s = snapPoint([41.2, 11.1], { grid: 5, endpoints: [], radius: 2, from: [10, 10] });
    expect(s.point).toEqual([40, 10]);
  });
});

describe("strokes", () => {
  it("line, rect and circle carry mm geometry", () => {
    expect(lineStroke("a", [0, 0], [30, 40]).points).toEqual([[0, 0], [30, 40]]);
    const r = rectStroke("b", [10, 40], [50, 10]);
    expect(r.points).toEqual([[10, 10], [50, 10], [50, 40], [10, 40]]);
    expect(r.closed).toBe(true);
    const c = circleStroke("c", [30, 30], 5);
    expect(c.center).toEqual([30, 30]);
    expect(c.radius_mm).toBe(5);
    expect(c.points.length).toBe(64);
    for (const [x, y] of c.points) expect(Math.hypot(x - 30, y - 30)).toBeCloseTo(5, 6);
  });

  it("measures polylines (closed adds the last edge)", () => {
    expect(measuredLength(lineStroke("a", [0, 0], [30, 40]))).toBe(50);
    expect(measuredLength(rectStroke("b", [0, 0], [10, 20]))).toBe(60);
    expect(measuredLength(circleStroke("c", [0, 0], 10))).toBeCloseTo(2 * Math.PI * 10, 0);
  });

  it("simplifies freehand input and caps its size", () => {
    const pts: [number, number][] = [];
    for (let i = 0; i <= 100; i++) pts.push([i * 0.5, 0.01 * Math.sin(i)]);
    const s = simplify(pts, 0.3);
    expect(s).toEqual([pts[0], pts[100]]);
    const big: [number, number][] = Array.from({ length: 5000 }, (_, i) => [i * 0.04, Math.sin(i / 10) * 20]);
    expect(freehandStroke("f", big).points.length).toBeLessThanOrEqual(2000);
  });

  it("closes a freehand stroke that ends near its start", () => {
    const loop = freehandStroke("f", [[0, 0], [20, 0], [20, 20], [0, 20], [0.8, 0.6]]);
    expect(closeIfNear(loop, 2).closed).toBe(true);
    expect(closeIfNear(loop, 2).points.at(-1)).toEqual([0, 20]);
    const open = freehandStroke("g", [[0, 0], [20, 0], [20, 20]]);
    expect(closeIfNear(open, 2).closed).toBe(false);
  });

  it("endpoints exclude circles and include rect corners", () => {
    const doc: SketchDoc = { ...emptyDoc(), strokes: [lineStroke("a", [0, 0], [1, 1]), rectStroke("b", [5, 5], [6, 6]), circleStroke("c", [9, 9], 1)] };
    expect(endpoints(doc.strokes)).toEqual([[0, 0], [1, 1], [5, 5], [6, 5], [6, 6], [5, 6], [9, 9]]);
  });

  it("hit-tests a stroke by distance to its segments", () => {
    const strokes = [lineStroke("a", [0, 0], [100, 0]), circleStroke("c", [50, 50], 10)];
    expect(hitStroke(strokes, [50, 1], 2)?.id).toBe("a");
    expect(hitStroke(strokes, [60, 50], 2)?.id).toBe("c");
    expect(hitStroke(strokes, [50, 25], 2)).toBeNull();
  });

  it("labels sit at the middle of a line and on top of a circle", () => {
    expect(labelAnchor(lineStroke("a", [0, 0], [40, 0]))).toEqual([20, 0]);
    expect(labelAnchor(circleStroke("c", [10, 10], 5))).toEqual([10, 15]);
  });
});

describe("parseDimension", () => {
  it.each([
    ["40", "line", { value_mm: 40, kind: "length" }],
    ["40 mm", "line", { value_mm: 40, kind: "length" }],
    ["12,5", "line", { value_mm: 12.5, kind: "length" }],
    ["4 cm", "line", { value_mm: 40, kind: "length" }],
    ["2in", "line", { value_mm: 50.8, kind: "length" }],
    ['1.5"', "line", { value_mm: 38.1, kind: "length" }],
    ["Ø20", "line", { value_mm: 20, kind: "diameter" }],
    ["dia 8", "line", { value_mm: 8, kind: "diameter" }],
    ["R5", "line", { value_mm: 5, kind: "radius" }],
    ["20", "circle", { value_mm: 20, kind: "diameter" }],
    ["45°", "line", { value_mm: null, kind: "angle" }],
    ["clamps here", "line", { value_mm: null, kind: "note" }],
    ["0", "line", { value_mm: null, kind: "note" }],
  ] as const)("%s on a %s", (text, kind, want) => {
    expect(parseDimension(text, kind)).toEqual({ text, ...want });
  });

  it("empty means no dimension", () => {
    expect(parseDimension("   ", "line")).toBeNull();
  });
});

describe("History", () => {
  it("undo / redo / clear, and a new edit drops the redo branch", () => {
    const h = new History(emptyDoc());
    const a = { ...h.doc, strokes: [lineStroke("a", [0, 0], [1, 0])] };
    h.push(a);
    const b = { ...a, strokes: [...a.strokes, lineStroke("b", [0, 0], [0, 1])] };
    h.push(b);
    expect(h.canUndo && !h.canRedo).toBe(true);
    h.undo();
    expect(h.doc).toBe(a);
    h.redo();
    expect(h.doc).toBe(b);
    h.push(emptyDoc());              // clear is undoable
    h.undo();
    expect(h.doc).toBe(b);
    h.undo();
    h.push({ ...a, notes: "x" });
    expect(h.canRedo).toBe(false);
  });

  it("is bounded", () => {
    const h = new History(emptyDoc(), 3);
    for (let i = 0; i < 10; i++) h.push({ ...h.doc, notes: String(i) });
    let n = 0;
    while (h.canUndo) { h.undo(); n++; }
    expect(n).toBe(3);
  });
});

describe("point budget", () => {
  it("re-simplifies freehand strokes so the sketch stays within the core's 5,000 points", () => {
    expect(MAX_TOTAL_POINTS).toBe(5000);
    const wiggle = (id: string, dy: number): Stroke => ({
      id, kind: "freehand", closed: false, label: "",
      points: Array.from({ length: 2000 }, (_, i) => [i * 0.1, dy + Math.sin(i / 7) * 3] as [number, number]),
    });
    const doc: SketchDoc = { ...emptyDoc(), strokes: [wiggle("a", 20), wiggle("b", 60), wiggle("c", 100), lineStroke("l", [0, 0], [9, 9])] };
    expect(totalPoints(doc.strokes)).toBe(6002);
    const up = toUpload(doc);
    expect(up.strokes.reduce((n, s) => n + s.points.length, 0)).toBeLessThanOrEqual(5000);
    expect(up.strokes[3].points).toEqual([[0, 0], [9, 9]]);            // lines are never touched
  });

  it("reports when even simplified it can't fit (too many circles)", () => {
    const many = Array.from({ length: 80 }, (_, i) => circleStroke(`c${i}`, [i, i], 2));
    expect(withinPointBudget({ ...emptyDoc(), strokes: many })).toBe(false);
    expect(withinPointBudget({ ...emptyDoc(), strokes: many.slice(0, 70) })).toBe(true);
  });
});

describe("toUpload", () => {
  it("rounds to 0.01 mm and keeps only dimensions on existing strokes", () => {
    const doc: SketchDoc = {
      ...emptyDoc(),
      strokes: [lineStroke("a", [0.123456, 1], [30.0049, 40])],
      dimensions: [
        { stroke_id: "a", text: "50", value_mm: 50, kind: "length" },
        { stroke_id: "gone", text: "1", value_mm: 1, kind: "length" },
      ],
      notes: "  plate  ",
    };
    expect(toUpload(doc)).toEqual({
      units: "mm", width_mm: 200, height_mm: 150,
      strokes: [{ id: "a", kind: "line", points: [[0.12, 1], [30, 40]], closed: false, label: "" }],
      dimensions: [{ stroke_id: "a", text: "50", value_mm: 50, kind: "length" }],
      notes: "plate",
    });
  });
});
