// What a click on the model picked, in words and as a measure ref (Plan 7 U2a). The label chip
// ("Face · planar · 1,200 mm²", "Edge · circle Ø4.2") says what was clicked; measure sends the
// picks to the core (exact B-rep) and shows the metrics' own values meanwhile.
import type { PartEdge } from "../../api/builds";
import type { MeasureRef, MeasureValue } from "../../api/measure";
import type { PartFace, PartHole, PartMetrics, Vec3 } from "../../api/types";
import type { ViewerHit } from "./scene";
import { edgeEnds, matchFace, nearestEdge } from "./pick";

export type Picked =
  | { kind: "face"; face: PartFace }
  | { kind: "edge"; edge: PartEdge }
  | { kind: "vertex"; point: Vec3 }
  | { kind: "point"; point: Vec3; face: PartFace | null };

/** A pick within this many px of a line edge's end is that vertex; of an edge, that edge. */
export const VERTEX_PX = 8;
export const EDGE_PX = 10;

const nf = (digits: number) => new Intl.NumberFormat("en-US", { maximumFractionDigits: digits });
const n2 = nf(2);
const n3 = nf(3);
/** "1,200" · "4.2" · "−20" (a real minus sign). */
function num(v: number, digits = 2): string {
  const s = (digits === 3 ? n3 : digits === 0 ? nf(0) : n2).format(Math.abs(v) < 1e-9 ? 0 : v);
  return s.replace(/^-/, "−");
}
const area = (a: number) => `${num(a, a >= 100 ? 0 : 2)} mm²`;
const xyz = (p: Vec3) => p.map((c) => num(c)).join(", ");

export function labelFor(p: Picked): string {
  switch (p.kind) {
    case "face": {
      const f = p.face;
      if (f.type === "plane") return `Face · planar · ${area(f.area)}`;
      if (f.radius !== undefined) return `Face · ${f.type} Ø${num(2 * f.radius, 3)}`;
      return `Face · ${f.type} · ${area(f.area)}`;
    }
    case "edge": {
      const e = p.edge;
      if (e.radius !== undefined) return `Edge · ${e.type} Ø${num(2 * e.radius, 3)}`;
      return `Edge · ${e.type} · ${num(e.length)} mm`;
    }
    case "vertex": return `Vertex · ${xyz(p.point)}`;
    case "point": return `Point · ${xyz(p.point)}`;
  }
}

/** The click → a vertex (a line edge's end), else an edge, else the face under it. With
 *  `asPoint` (comment mode) the click itself, with the face it is on. */
export function pickTarget(m: PartMetrics | null, hit: ViewerHit, toScreen: (p: Vec3) => [number, number], opts: { asPoint?: boolean } = {}): Picked | null {
  const size = m?.bbox.size ?? [1, 1, 1];
  const diag = Math.hypot(size[0], size[1], size[2]);
  const face = m?.face_list ? matchFace(m.face_list, hit.point, hit.normal, Math.max(0.05, diag * 0.01)) : null;
  if (opts.asPoint) return { kind: "point", point: hit.point, face };
  const edges = m?.edge_list ?? [];
  const [hx, hy] = toScreen(hit.point);
  let vertex: Vec3 | null = null;
  let best = VERTEX_PX;
  for (const e of edges) {
    for (const end of edgeEnds(e) ?? []) {
      const [x, y] = toScreen(end);
      const d = Math.hypot(x - hx, y - hy);
      if (d <= best) { best = d; vertex = end.map((c) => Number(c.toFixed(4)) + 0) as Vec3; }
    }
  }
  if (vertex) return { kind: "vertex", point: vertex };
  const edge = nearestEdge(edges, hit.point, toScreen, EDGE_PX);
  if (edge) return { kind: "edge", edge };
  return face ? { kind: "face", face } : null;
}

export function refOf(p: Picked): MeasureRef {
  if (p.kind === "face") return { face_id: p.face.id };
  if (p.kind === "edge") return { edge_id: p.edge.id };
  return { point: p.point };
}

const keyOf = (p: Picked): string => JSON.stringify(refOf(p));

/** Measure's selection after a click: a plain click replaces it, Shift adds (a pick made twice
 *  is dropped again). */
export function togglePick(list: readonly Picked[], p: Picked, add: boolean): Picked[] {
  if (!add) return [p];
  const k = keyOf(p);
  return list.some((x) => keyOf(x) === k) ? list.filter((x) => keyOf(x) !== k) : [...list, p];
}

const val = (key: string, label: string, value: number, unit: string): MeasureValue => ({ key, label, value, unit });

/** One pick's values from its metrics row (B-rep numbers rounded by the core), shown until the
 *  core's exact answer arrives; two picks or more wait for the core. */
export function provisionalValues(list: readonly Picked[]): MeasureValue[] {
  if (list.length !== 1) return [];
  const p = list[0];
  const out: MeasureValue[] = [];
  if (p.kind === "edge" || p.kind === "face") {
    const r = p.kind === "edge" ? p.edge.radius : p.face.radius;
    if (r !== undefined) out.push(val("diameter", "Diameter", 2 * r, "mm"), val("radius", "Radius", r, "mm"));
    out.push(p.kind === "edge" ? val("length", "Length", p.edge.length, "mm") : val("area", "Area", p.face.area, "mm²"));
  } else {
    out.push(val("x", "X", p.point[0], "mm"), val("y", "Y", p.point[1], "mm"), val("z", "Z", p.point[2], "mm"));
  }
  return out;
}

/** The values worth showing: a normal distance equal to the distance says nothing new. */
export function shownValues(list: readonly MeasureValue[]): MeasureValue[] {
  const d = list.find((v) => v.key === "distance");
  return list.filter((v) => !(v.key === "normal_distance" && d && Math.abs(d.value - v.value) < 1e-6));
}

/** "Diameter 4.2 mm" · "Area 1,200 mm²" · "Angle 90°". */
export function fmtMeasure(v: MeasureValue): string {
  if (v.unit === "°") return `${v.label} ${num(v.value, 2)}°`;
  if (v.unit === "mm²") return `${v.label} ${area(v.value)}`;
  return `${v.label} ${num(v.value, 3)} ${v.unit}`;
}

/** The value alone ("4.2 mm"), for the panel's value column. */
export function fmtValueOnly(v: MeasureValue): string {
  return fmtMeasure(v).slice(v.label.length + 1);
}

/** A circular edge around a hole: its ring (centre on the hole's axis), for the blue marker. */
export function circleOf(e: PartEdge, holes: readonly PartHole[] | undefined): { center: Vec3; axis: Vec3; radius: number } | null {
  if (e.radius === undefined || !e.center) return null;
  for (const h of holes ?? []) {
    if (Math.abs(h.diameter_mm - 2 * e.radius) > 1e-3) continue;
    const a = h.axis;
    const w: Vec3 = [e.center[0] - h.center[0], e.center[1] - h.center[1], e.center[2] - h.center[2]];
    const t = w[0] * a[0] + w[1] * a[1] + w[2] * a[2];
    const c: Vec3 = [h.center[0] + a[0] * t, h.center[1] + a[1] * t, h.center[2] + a[2] * t];
    const radial = Math.hypot(e.center[0] - c[0], e.center[1] - c[1], e.center[2] - c[2]);
    if (Math.abs(radial - e.radius) > 0.01) continue;
    const r = (v: number) => Number(v.toFixed(4)) + 0;
    return { center: [r(c[0]), r(c[1]), r(c[2])], axis: [a[0], a[1], a[2]], radius: e.radius };
  }
  return null;
}
