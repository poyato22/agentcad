import { describe, expect, it } from "vitest";
import { hitStroke } from "./edit";
import {
  circleStroke, closeIfNear, emptyDoc, freehandStroke, History, labelAnchor, lineStroke, measuredLength,
  MAX_TOTAL_POINTS, parseDimension, rectStroke, SHEET, simplify, totalPoints, toUpload, withinPointBudget, type SketchDoc, type Stroke,
} from "./model";
import { snapTargets } from "./snap";
import { DEFAULT_VIEW, fitBox, gridLines, gridSpacing, mmToScreen, rulerTicks, screenToMm, visible, zoomAt, type View } from "./view";

const RECT = { left: 100, top: 50, width: 400, height: 300 }; // exactly 2 px per mm for a 200 x 150 mm view
const SHEET_VIEW: View = { x: 0, y: 0, w: 200, h: 150 };

describe("view", () => {
  it("converts screen px to mm with the origin bottom-left and +y up", () => {
    expect(SHEET).toEqual({ width_mm: 200, height_mm: 150 });
    expect(screenToMm(100, 350, RECT, SHEET_VIEW)).toEqual([0, 0]);
    expect(screenToMm(500, 50, RECT, SHEET_VIEW)).toEqual([200, 150]);
    expect(screenToMm(300, 200, RECT, SHEET_VIEW)).toEqual([100, 75]);
    expect(mmToScreen([10, 10], RECT, SHEET_VIEW)).toEqual([120, 330]);
  });

  it("handles the letterbox (centred across, top-aligned: xMidYMin meet)", () => {
    const wide = { left: 0, top: 0, width: 600, height: 300 };
    expect(screenToMm(100, 300, wide, SHEET_VIEW)).toEqual([0, 0]);
    const tall = { left: 0, top: 0, width: 400, height: 600 };
    expect(screenToMm(0, 300, tall, SHEET_VIEW)).toEqual([0, 0]);
    // what is visible past the view's own aspect
    expect(visible(wide, SHEET_VIEW)).toEqual({ x: -50, y: 0, w: 300, h: 150 });
  });

  it("the default view shows the sheet with a margin for the origin and rulers", () => {
    expect(DEFAULT_VIEW.x).toBeLessThan(0);
    expect(DEFAULT_VIEW.y).toBeLessThan(0);
    expect(DEFAULT_VIEW.x + DEFAULT_VIEW.w).toBeGreaterThan(200);
    expect(DEFAULT_VIEW.y + DEFAULT_VIEW.h).toBeGreaterThan(150);
  });

  it("zooms about the pointer and fits a box", () => {
    const z = zoomAt(SHEET_VIEW, [50, 50], 2);
    expect(z).toEqual({ x: 25, y: 25, w: 100, h: 75 });
    expect(screenToMm(...mmToScreen([50, 50], RECT, SHEET_VIEW), RECT, z)).toEqual([50, 50]);
    const f = fitBox([20, 20, 100, 70], SHEET_VIEW);
    expect(f.w / f.h).toBeCloseTo(200 / 150, 6);
    expect(f.x).toBeLessThan(20);
    expect(f.x + f.w).toBeGreaterThan(100);
  });

  it("adaptive grid: minor lines at least 8 px apart, major on a power of ten", () => {
    expect(gridSpacing(4)).toEqual({ minor: 2, major: 10 });
    expect(gridSpacing(1)).toEqual({ minor: 10, major: 100 });
    expect(gridSpacing(40)).toEqual({ minor: 0.2, major: 1 });
    expect(gridSpacing(8)).toEqual({ minor: 1, major: 10 });
    expect(gridLines(-3, 11, 5)).toEqual([0, 5, 10]);
    expect(rulerTicks(0, 100, 2).map((t) => t.label)).toEqual(["0", "50", "100"]);
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

  it("snap targets: line ends, rect corners, circle centres (not circle samples)", () => {
    const t = snapTargets([lineStroke("a", [0, 0], [1, 1]), rectStroke("b", [5, 5], [6, 6]), circleStroke("c", [9, 9], 1)]);
    expect(t.endpoints.map((e) => e.p)).toEqual([[0, 0], [1, 1], [5, 5], [6, 5], [6, 6], [5, 6]]);
    expect(t.centers.map((e) => e.p)).toEqual([[9, 9]]);
  });

  it("hit-tests a stroke by distance to what it draws", () => {
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
