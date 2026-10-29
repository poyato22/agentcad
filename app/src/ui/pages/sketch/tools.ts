// Drawing tools (pure): which clicks each tool takes, the stroke a tool previews from its clicks
// and the pointer, what a typed value means at each step, and the hint bar's next step.
import { add, circumcircle, dist, mul, nearestOnSeg, sub, unit, type Pt } from "./geom";
import type { Typed } from "./input";
import { circleStroke, lineStroke, rectStroke, type Stroke } from "./model";

export type Tool = "select" | "pen" | "line" | "polyline" | "rect" | "crect" | "circle" | "arc" | "slot" | "fillet";
export type DrawTool = Exclude<Tool, "select" | "pen" | "fillet">;

export const TOOLS: readonly { id: Tool; label: string; key: string }[] = [
  { id: "select", label: "Select", key: "v" },
  { id: "pen", label: "Pen", key: "p" },
  { id: "line", label: "Line", key: "l" },
  { id: "polyline", label: "Polyline", key: "y" },
  { id: "rect", label: "Rectangle", key: "r" },
  { id: "crect", label: "Center rectangle", key: "t" },
  { id: "circle", label: "Circle", key: "c" },
  { id: "arc", label: "Arc", key: "a" },
  { id: "slot", label: "Slot", key: "s" },
  { id: "fillet", label: "Fillet", key: "f" },
];

export function isDrawTool(t: Tool): t is DrawTool {
  return t !== "select" && t !== "pen" && t !== "fillet";
}

/** Clicks a tool takes (a polyline goes on until finished). */
export function clicksFor(t: DrawTool): number {
  return t === "arc" || t === "slot" ? 3 : t === "polyline" ? Infinity : 2;
}

/** Which tools draw from the last click (enables the from-point inference guides). */
export function drawsFrom(t: DrawTool, clicks: number): boolean {
  return t === "line" || t === "polyline" || (t === "arc" && clicks === 1) || (t === "slot" && clicks === 1);
}

const MIN_MM = 0.5;

/** The stroke a tool shows for its clicks so far plus the pointer (null while too small). */
export function draftShape(t: DrawTool, clicks: readonly Pt[], cursor: Pt, id = "draft", gridR: number | null = null): Stroke | null {
  const [a, b] = clicks;
  if (!a) return null;
  switch (t) {
    case "line": return dist(a, cursor) >= MIN_MM ? lineStroke(id, a, cursor) : null;
    case "polyline": {
      const pts = [...clicks, cursor];
      if (dist(pts[pts.length - 2], cursor) < MIN_MM) pts.pop();
      return pts.length >= 2 ? { id, kind: "polyline", points: pts.map((p) => [p[0], p[1]]), closed: false, label: "" } : null;
    }
    case "rect": return Math.abs(cursor[0] - a[0]) >= MIN_MM && Math.abs(cursor[1] - a[1]) >= MIN_MM ? rectStroke(id, a, cursor) : null;
    case "crect": {
      const d: Pt = [Math.abs(cursor[0] - a[0]), Math.abs(cursor[1] - a[1])];
      return d[0] >= MIN_MM / 2 && d[1] >= MIN_MM / 2 ? rectStroke(id, sub(a, d), add(a, d)) : null;
    }
    case "circle": {
      let r = dist(a, cursor);
      if (gridR) r = Math.round(r / gridR) * gridR;
      return r >= MIN_MM ? circleStroke(id, a, r) : null;
    }
    case "arc": {
      if (!b) return dist(a, cursor) >= MIN_MM ? lineStroke(id, a, cursor) : null;
      return circumcircle(a, cursor, b) ? { id, kind: "arc", points: [[a[0], a[1]], [cursor[0], cursor[1]], [b[0], b[1]]], closed: false, label: "" } : null;
    }
    case "slot": {
      if (!b) return dist(a, cursor) >= MIN_MM ? lineStroke(id, a, cursor) : null;
      const r = dist(cursor, nearestOnSeg(cursor, a, b).point);
      return r >= MIN_MM / 2 ? slotStroke(id, a, b, r) : null;
    }
  }
}

export function slotStroke(id: string, a: Pt, b: Pt, r: number): Stroke {
  return { id, kind: "slot", points: [[a[0], a[1]], [b[0], b[1]]], radius_mm: r, closed: true, label: "" };
}

/** A dimension to add to the stroke a typed value made (driving). */
export type Drive =
  | { kind: "distance"; edge: number; value: number }
  | { kind: "distance"; vertices: [number, number]; value: number }
  | { kind: "diameter" | "radius"; value: number };

export interface TypedStep { click: Pt; drive: Drive[] }

function dirTo(from: Pt, cursor: Pt): Pt {
  return dist(from, cursor) < 1e-9 ? [1, 0] : unit(sub(cursor, from));
}
const sgn = (v: number) => (v < 0 ? -1 : 1);
const polar = (from: Pt, l: number, deg: number): Pt => [from[0] + l * Math.cos((deg * Math.PI) / 180), from[1] + l * Math.sin((deg * Math.PI) / 180)];

/** What a typed value does at this step: the click it stands for, and the dimensions it sets. Null
 *  when the value doesn't fit the step (e.g. an angle for a circle). */
export function typedStep(t: DrawTool, clicks: readonly Pt[], cursor: Pt, v: Typed): TypedStep | null {
  const last = clicks[clicks.length - 1];
  if (!last) return null;
  const n = clicks.length;
  if (t === "line" || t === "polyline" || (t === "arc" && n === 1) || (t === "slot" && n === 1)) {
    const cur = Math.max(dist(last, cursor), 10);
    const drive = (value: number): Drive[] => (t === "line" ? [{ kind: "distance", edge: 0, value }]
      : t === "polyline" ? [{ kind: "distance", edge: n - 1, value }]
        : t === "slot" ? [{ kind: "distance", vertices: [0, 1], value }] : []);
    switch (v.kind) {
      case "length": return { click: add(last, mul(dirTo(last, cursor), v.mm)), drive: drive(v.mm) };
      case "polar": return { click: polar(last, v.mm, v.deg), drive: drive(v.mm) };
      case "angle": return { click: polar(last, cur, v.deg), drive: [] };
      case "pair": return { click: add(last, [v.a, v.b]), drive: [] };
      default: return null;
    }
  }
  if (t === "rect" || t === "crect") {
    const [w, h] = v.kind === "pair" ? [v.a, v.b] : v.kind === "length" ? [v.mm, v.mm] : [NaN, NaN];
    if (!Number.isFinite(w) || Math.abs(w) < MIN_MM || Math.abs(h) < MIN_MM) return null;
    // a plain size goes the way the pointer is; a signed one goes the way it says
    const sx = v.kind === "pair" && w < 0 ? -1 : sgn(cursor[0] - last[0]);
    const sy = v.kind === "pair" && h < 0 ? -1 : sgn(cursor[1] - last[1]);
    const k = t === "crect" ? 0.5 : 1;
    return { click: [last[0] + sx * Math.abs(w) * k, last[1] + sy * Math.abs(h) * k], drive: [{ kind: "distance", edge: 0, value: Math.abs(w) }, { kind: "distance", edge: 1, value: Math.abs(h) }] };
  }
  if (t === "circle") {
    const r = v.kind === "radius" ? v.mm : v.kind === "diameter" || v.kind === "length" ? v.mm / 2 : NaN;
    if (!Number.isFinite(r)) return null;
    return { click: add(last, mul(dirTo(last, cursor), r)), drive: [{ kind: "diameter", value: r * 2 }] };
  }
  if (t === "arc" && n === 2) {
    const r = v.kind === "radius" || v.kind === "length" ? v.mm : v.kind === "diameter" ? v.mm / 2 : NaN;
    const [a, b] = clicks;
    const half = dist(a, b) / 2;
    if (!Number.isFinite(r) || r < half) return null;
    const m: Pt = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    const nrm = unit([-(b[1] - a[1]), b[0] - a[0]]);
    const side = sgn((cursor[0] - m[0]) * nrm[0] + (cursor[1] - m[1]) * nrm[1]);
    const sag = r - Math.sqrt(r * r - half * half);
    return { click: add(m, mul(nrm, sag * side)), drive: [{ kind: "radius", value: r }] };
  }
  if (t === "slot" && n === 2) {
    const r = v.kind === "radius" ? v.mm : v.kind === "length" || v.kind === "diameter" ? v.mm / 2 : NaN;
    if (!Number.isFinite(r)) return null;
    const [a, b] = clicks;
    const nrm = unit([-(b[1] - a[1]), b[0] - a[0]]);
    return { click: add(a, mul(nrm, r)), drive: [{ kind: "radius", value: r }] };
  }
  return null;
}

/** Whether R / D start a radius or diameter at this step. */
export function takesRadius(t: Tool, clicks: number): boolean {
  return t === "fillet" || (t === "circle" && clicks === 1) || (t === "arc" && clicks === 2) || (t === "slot" && clicks === 2);
}

/** The hint bar: the current tool's next step, in at most 8 words. */
export function hint(t: Tool, clicks: number): string {
  switch (t) {
    case "select": return "Click to select, drag to move";
    case "pen": return "Drag to draw freehand";
    case "line": return clicks ? "Click the end, or type a length" : "Click the start point";
    case "polyline": return clicks ? "Click the next point · Enter finishes" : "Click the first point";
    case "rect": return clicks ? "Click the opposite corner, or type 40,20" : "Click the first corner";
    case "crect": return clicks ? "Click a corner, or type 40,20" : "Click the center";
    case "circle": return clicks ? "Click the edge, or type a diameter" : "Click the center";
    case "arc": return clicks === 0 ? "Click where the arc starts" : clicks === 1 ? "Click where the arc ends" : "Click a point on the arc";
    case "slot": return clicks === 0 ? "Click the first end's center" : clicks === 1 ? "Click the second end's center" : "Click or type the slot width";
    case "fillet": return "Click a corner · type a radius";
  }
}
