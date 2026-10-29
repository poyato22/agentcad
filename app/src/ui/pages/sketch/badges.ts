// Where constraint badges go (pure): "H" / "V" / "=" next to an edge, a dot on a coincident
// point, and driving dimensions as a dimension line with its value (lengths) or a leader with
// Ø / R (circles, arcs, slots). Sizes are in mm; `u` is mm per screen px so badges keep their
// on-screen size at any zoom.
import type { SketchConstraint, SketchStroke } from "../../../api/types";
import { add, circumcircle, mid, mul, perp, sub, unit, type Pt } from "./geom";

export interface Badge {
  id: string;
  kind: SketchConstraint["kind"];
  /** Where the text (or dot) goes. */
  at: Pt;
  text: string;
  /** A dimension line (lengths) or leader (radii). */
  line?: [Pt, Pt];
  /** Extension lines from the geometry to the dimension line. */
  ext?: [Pt, Pt][];
}

function centroid(s: SketchStroke): Pt {
  const n = s.points.length || 1;
  return [s.points.reduce((a, p) => a + p[0], 0) / n, s.points.reduce((a, p) => a + p[1], 0) / n];
}

function handle(s: SketchStroke, v: number): Pt | null {
  if (s.kind === "circle") return s.center ?? null;
  const p = s.points[v];
  return p ? [p[0], p[1]] : null;
}

function edge(s: SketchStroke, e: number): [Pt, Pt] | null {
  const n = s.points.length;
  const a = s.points[e];
  const b = s.points[(e + 1) % n];
  return a && b ? [[a[0], a[1]], [b[0], b[1]]] : null;
}

function radial(s: SketchStroke): { c: Pt; r: number } | null {
  if (s.kind === "circle" && s.center && s.radius_mm) return { c: s.center, r: s.radius_mm };
  if (s.kind === "slot" && s.radius_mm && s.points[1]) return { c: [s.points[1][0], s.points[1][1]], r: s.radius_mm };
  if (s.kind === "arc" && s.points.length === 3) return circumcircle(s.points[0] as Pt, s.points[1] as Pt, s.points[2] as Pt);
  return null;
}

const fmt = (v: number) => String(Math.round(v * 100) / 100);

/** Which side of an edge points away from the stroke's middle (for open lines: the left). */
function outward(s: SketchStroke, a: Pt, b: Pt): Pt {
  const n = perp(unit(sub(b, a)));
  if (s.kind === "line") return n;
  const c = centroid(s);
  const m = mid(a, b);
  return (m[0] - c[0]) * n[0] + (m[1] - c[1]) * n[1] >= 0 ? n : mul(n, -1);
}

export function constraintBadges(strokes: readonly SketchStroke[], constraints: readonly SketchConstraint[], u: number): Badge[] {
  const by = new Map(strokes.map((s) => [s.id, s]));
  const out: Badge[] = [];
  const gap = 14 * u;
  for (const c of constraints) {
    const ss = c.refs.map((r) => by.get(r.stroke_id));
    if (ss.some((s) => !s)) continue;
    const s0 = ss[0] as SketchStroke;
    const r0 = c.refs[0];
    switch (c.kind) {
      case "horizontal": case "vertical": {
        const e = r0.edge !== undefined ? edge(s0, r0.edge) : null;
        const pts = e ?? c.refs.map((r, i) => handle(ss[i] as SketchStroke, r.vertex ?? 0)).filter((p): p is Pt => !!p);
        if (pts.length < 2) break;
        const m = mid(pts[0], pts[1]);
        const n = e ? outward(s0, e[0], e[1]) : ([0, 1] as Pt);
        out.push({ id: c.id, kind: c.kind, at: add(m, mul(n, -gap * 0.7)), text: c.kind === "horizontal" ? "H" : "V" });
        break;
      }
      case "equal":
        c.refs.forEach((r, i) => {
          const s = ss[i] as SketchStroke;
          const e = r.edge !== undefined ? edge(s, r.edge) : null;
          if (e) out.push({ id: c.id, kind: c.kind, at: add(mid(e[0], e[1]), mul(outward(s, e[0], e[1]), -gap * 0.7)), text: "=" });
          else {
            const rr = radial(s);
            if (rr) out.push({ id: c.id, kind: c.kind, at: add(rr.c, [-rr.r * Math.SQRT1_2 - gap * 0.5, -rr.r * Math.SQRT1_2 - gap * 0.5]), text: "=" });
          }
        });
        break;
      case "coincident": {
        const p = handle(s0, r0.vertex ?? 0);
        if (p) out.push({ id: c.id, kind: c.kind, at: p, text: "" });
        break;
      }
      case "distance": {
        let a: Pt | null = null;
        let b: Pt | null = null;
        let n: Pt = [0, 1];
        if (r0.edge !== undefined) {
          const e = edge(s0, r0.edge);
          if (e) { [a, b] = e; n = outward(s0, a, b); }
        } else if (c.refs.length === 2) {
          a = handle(s0, r0.vertex ?? 0);
          b = handle(ss[1] as SketchStroke, c.refs[1].vertex ?? 0);
          if (a && b) n = perp(unit(sub(b, a)));
        }
        if (!a || !b) break;
        const off = mul(n, gap * 1.4);
        const la = add(a, off);
        const lb = add(b, off);
        out.push({ id: c.id, kind: c.kind, at: add(mid(la, lb), mul(n, gap * 0.45)), text: fmt(c.value_mm ?? 0), line: [la, lb], ext: [[a, add(la, mul(n, gap * 0.3))], [b, add(lb, mul(n, gap * 0.3))]] });
        break;
      }
      case "distance_x": case "distance_y": {
        const a = handle(s0, r0.vertex ?? 0);
        const b = c.refs[1] ? handle(ss[1] as SketchStroke, c.refs[1].vertex ?? 0) : null;
        if (!a || !b) break;
        // a straight run along x (or y) at the level of the second point, with an extension from the first
        const along = c.kind === "distance_x";
        const la: Pt = along ? [a[0], b[1]] : [b[0], a[1]];
        out.push({ id: c.id, kind: c.kind, at: add(mid(la, b), along ? [0, gap * 0.45] : [gap * 0.45, 0]), text: fmt(c.value_mm ?? 0), line: [la, b], ext: [[a, la]] });
        break;
      }
      case "point_on_circle": break;               // shown by the point sitting on the circle
      case "diameter": case "radius": {
        const rr = radial(s0);
        if (!rr) break;
        const d: Pt = [Math.SQRT1_2, Math.SQRT1_2];
        const onCurve = add(rr.c, mul(d, rr.r));
        const tip = add(onCurve, mul(d, gap * 1.3));
        out.push({ id: c.id, kind: c.kind, at: add(tip, [gap * 0.2, gap * 0.2]), text: `${c.kind === "diameter" ? "Ø" : "R"}${fmt(c.value_mm ?? 0)}`, line: [c.kind === "diameter" ? onCurve : rr.c, tip] });
        break;
      }
    }
  }
  return out;
}
