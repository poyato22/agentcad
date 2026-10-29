// Closed-profile detection (pure): closed strokes, plus chains of open strokes joined end to end
// where every joint meets exactly two ends. Loops nest by containment: even depth = an outer
// boundary, odd = a hole of its parent. Same algorithm and output shape as core
// sketch.closed_profiles (the core derives its own copy for the agent; this one shades the canvas
// and feeds the extrude preview).
import type { SketchStroke } from "../../../api/types";
import {
  dist, isClosedKind, pointInPolygon, reverseSegs, sampleSegs, segEnds, signedArea, strokeSegments, type Pt, type Seg,
} from "./geom";

export const MAX_PROFILES = 100;
export const JOIN_TOL_MM = 0.01;

export interface Loop { strokes: string[]; segs: Seg[]; area: number; poly: Pt[] }
export interface Profile { outer: Loop; holes: Loop[]; area: number }

function loops(strokes: readonly SketchStroke[]): { strokes: string[]; segs: Seg[] }[] {
  const out: { strokes: string[]; segs: Seg[] }[] = [];
  const pieces: { id: string; segs: Seg[] }[] = [];
  for (const s of strokes) {
    if (s.construction) continue;
    const segs = strokeSegments(s);
    if (!segs.length) continue;
    if (isClosedKind(s)) out.push({ strokes: [s.id], segs });
    else pieces.push({ id: s.id, segs });
  }
  const nodes: Pt[] = [];
  const node = (p: Pt): number => {
    const i = nodes.findIndex((q) => dist(p, q) <= JOIN_TOL_MM);
    if (i >= 0) return i;
    nodes.push(p);
    return nodes.length - 1;
  };
  const ends = pieces.map((pc) => [node(segEnds(pc.segs[0])[0]), node(segEnds(pc.segs[pc.segs.length - 1])[1])] as const);
  const at = new Map<number, number[]>();
  ends.forEach(([a, b], k) => {
    at.set(a, [...(at.get(a) ?? []), k]);
    at.set(b, [...(at.get(b) ?? []), k]);
  });
  const used = new Set<number>();
  for (let k0 = 0; k0 < pieces.length; k0++) {
    if (used.has(k0)) continue;
    const chain: [number, boolean][] = [[k0, false]];
    const seen = new Set([k0]);
    const start = ends[k0][0];
    let cur = ends[k0][1];
    let ok = true;
    while (cur !== start) {
      const here = at.get(cur) ?? [];
      const next = here.filter((k) => !seen.has(k));
      if (here.length !== 2 || next.length !== 1) { ok = false; break; }
      const k = next[0];
      const rev = ends[k][1] === cur && ends[k][0] !== cur;
      chain.push([k, rev]);
      seen.add(k);
      cur = rev ? ends[k][0] : ends[k][1];
    }
    if (!ok || (at.get(start) ?? []).length !== 2) continue;
    for (const k of seen) used.add(k);
    out.push({ strokes: chain.map(([k]) => pieces[k].id), segs: chain.flatMap(([k, rev]) => (rev ? reverseSegs(pieces[k].segs) : pieces[k].segs)) });
  }
  return out;
}

/** The sketch's closed regions: outer loops counter-clockwise, holes clockwise; at most MAX_PROFILES. */
export function closedProfiles(strokes: readonly SketchStroke[]): Profile[] {
  const all: (Loop & { parent: number | null; depth: number })[] = [];
  for (const lp of loops(strokes)) {
    let segs = lp.segs;
    let area = signedArea(segs);
    if (Math.abs(area) < 1e-9) continue;
    if (area < 0) { segs = reverseSegs(segs); area = -area; }
    all.push({ strokes: lp.strokes, segs, area, poly: sampleSegs(segs), parent: null, depth: 0 });
  }
  all.sort((a, b) => b.area - a.area);
  all.forEach((lp, i) => {
    const probe = lp.poly[0];
    for (let j = i - 1; j >= 0; j--) {
      if (all[j].area > lp.area && pointInPolygon(probe, all[j].poly)) { lp.parent = j; break; }
    }
    lp.depth = lp.parent === null ? 0 : all[lp.parent].depth + 1;
  });
  const out: Profile[] = [];
  const plain = ({ strokes: s, segs, area, poly }: Loop): Loop => ({ strokes: s, segs, area, poly });
  for (let i = 0; i < all.length && out.length < MAX_PROFILES; i++) {
    const lp = all[i];
    if (lp.depth % 2) continue;
    const holes = all.filter((h) => h.parent === i && h.depth % 2).map((h) => ({ ...plain(h), segs: reverseSegs(h.segs), poly: [...h.poly].reverse() }));
    out.push({ outer: plain(lp), holes, area: lp.area - holes.reduce((n, h) => n + h.area, 0) });
  }
  return out;
}

/** Strokes that are part of some closed profile. */
export function profileStrokeIds(profiles: readonly Profile[]): Set<string> {
  return new Set(profiles.flatMap((p) => [p.outer, ...p.holes].flatMap((l) => l.strokes)));
}

/** SVG path data (sheet mm → SVG y-down) for shading a profile with fill-rule evenodd. */
export function profilePath(p: Profile, toSvg: (q: Pt) => Pt): string {
  const f = (v: number) => String(Math.round(v * 100) / 100);
  return [p.outer, ...p.holes].map((l) => "M" + l.poly.map((q) => toSvg(q).map(f).join(",")).join("L") + "Z").join("");
}

// ---- export (the core's context shape: polylines, arcs and circles, 2 decimals)

const num = (v: number) => Math.round(v * 100) / 100 + 0;
const pt = (p: Pt) => [num(p[0]), num(p[1])];

export type ExportSeg =
  | { polyline: number[][] }
  | { arc: { center: number[]; r: number; start: number[]; end: number[]; ccw: boolean } }
  | { circle: { center: number[]; r: number } };

export function exportSegments(segs: readonly Seg[]): ExportSeg[] {
  const out: ExportSeg[] = [];
  let run: Pt[] = [];
  const flush = () => { if (run.length) out.push({ polyline: run.map(pt) }); run = []; };
  for (const s of segs) {
    if (s.t === "line") {
      if (run.length && dist(run[run.length - 1], s.a) > 1e-9) flush();
      if (!run.length) run.push(s.a);
      run.push(s.b);
      continue;
    }
    flush();
    if (s.t === "circle") out.push({ circle: { center: pt(s.c), r: num(s.r) } });
    else out.push({ arc: { center: pt(s.c), r: num(s.r), start: pt(s.a), end: pt(s.b), ccw: s.ccw } });
  }
  flush();
  return out;
}

export function exportProfiles(profiles: readonly Profile[]) {
  const loop = (l: Loop) => ({ strokes: l.strokes, segments: exportSegments(l.segs), area_mm2: num(l.area) });
  return profiles.map((p) => ({ outer: loop(p.outer), holes: p.holes.map(loop), area_mm2: num(p.area) }));
}
