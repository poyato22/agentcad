// Starting sketches (pure): a plate with holes, an L bracket profile and a bolted flange, each
// fully dimensioned so the user only edits numbers.
import type { SketchConstraint } from "../../../api/types";
import { circleStroke, rectStroke, type Stroke } from "./model";

export type TemplateId = "plate" | "bracket" | "flange";
export const TEMPLATES: readonly { id: TemplateId; label: string; detail: string }[] = [
  { id: "plate", label: "Plate with holes", detail: "80 × 50 mm, 4 × Ø5" },
  { id: "bracket", label: "Bracket (L)", detail: "80 × 80 mm, 6 mm wall" },
  { id: "flange", label: "Flange", detail: "Ø80, Ø30 bore, 4 × Ø6 on Ø60" },
];

export interface Template { strokes: Stroke[]; constraints: SketchConstraint[]; extrude_mm: number }

/** A template; `id()` gives fresh stroke ids, `cid()` fresh constraint ids. */
export function template(t: TemplateId, id: () => string, cid: () => string): Template {
  const c = (kind: SketchConstraint["kind"], refs: SketchConstraint["refs"], value_mm?: number): SketchConstraint =>
    (value_mm === undefined ? { id: cid(), kind, refs } : { id: cid(), kind, refs, value_mm });
  if (t === "plate") {
    const plate = rectStroke(id(), [20, 20], [100, 70]);
    const holes = ([[28, 28], [92, 28], [92, 62], [28, 62]] as const).map(([x, y]) => circleStroke(id(), [x, y], 2.5));
    return {
      strokes: [plate, ...holes],
      constraints: [
        c("distance", [{ stroke_id: plate.id, edge: 0 }], 80),
        c("distance", [{ stroke_id: plate.id, edge: 1 }], 50),
        c("diameter", [{ stroke_id: holes[0].id }], 5),
        ...holes.slice(1).map((h) => c("equal", [{ stroke_id: holes[0].id }, { stroke_id: h.id }])),
        // each hole 8 mm in from its corner, so the holes follow the plate's size
        ...holes.flatMap((h, i) => {
          const refs = [{ stroke_id: plate.id, vertex: i }, { stroke_id: h.id, vertex: 0 }];
          return [c("distance_x", refs, 8), c("distance_y", refs, 8)];
        }),
      ],
      extrude_mm: 5,
    };
  }
  if (t === "bracket") {
    const l: Stroke = {
      id: id(), kind: "polyline", closed: true, label: "",
      points: [[20, 20], [100, 20], [100, 26], [26, 26], [26, 100], [20, 100]],
      corner_r: [0, 0, 0, 4, 0, 0],
    };
    const e = (edge: number) => [{ stroke_id: l.id, edge }];
    return {
      strokes: [l],
      constraints: [
        ...[0, 2, 4].map((k) => c("horizontal", e(k))),
        ...[1, 3, 5].map((k) => c("vertical", e(k))),
        c("distance", e(0), 80), c("distance", e(5), 80), c("distance", e(1), 6),
        c("equal", [{ stroke_id: l.id, edge: 1 }, { stroke_id: l.id, edge: 4 }]),
      ],
      extrude_mm: 30,
    };
  }
  const ctr: [number, number] = [100, 75];
  const outer = circleStroke(id(), ctr, 40);
  const bore = circleStroke(id(), ctr, 15);
  const pcd: Stroke = { ...circleStroke(id(), ctr, 30), construction: true };
  const holes = [45, 135, 225, 315].map((deg) => circleStroke(id(), [ctr[0] + 30 * Math.cos((deg * Math.PI) / 180), ctr[1] + 30 * Math.sin((deg * Math.PI) / 180)], 3));
  const centre = (s: Stroke) => ({ stroke_id: s.id, vertex: 0 });
  // a construction square through the bolt holes keeps them evenly spaced on the bolt circle
  const square: Stroke = { id: id(), kind: "polyline", closed: true, label: "", construction: true, points: holes.map((h) => [...(h.center as [number, number])] as [number, number]) };
  const sq = (edge: number) => [{ stroke_id: square.id, edge }];
  return {
    strokes: [outer, bore, pcd, ...holes, square],
    constraints: [
      c("diameter", [{ stroke_id: outer.id }], 80), c("diameter", [{ stroke_id: bore.id }], 30), c("diameter", [{ stroke_id: pcd.id }], 60),
      c("coincident", [centre(outer), centre(bore)]), c("coincident", [centre(outer), centre(pcd)]),
      c("diameter", [{ stroke_id: holes[0].id }], 6),
      ...holes.slice(1).map((h) => c("equal", [{ stroke_id: holes[0].id }, { stroke_id: h.id }])),
      ...holes.map((h) => c("point_on_circle", [centre(h), { stroke_id: pcd.id }])),
      ...holes.map((h, i) => c("coincident", [{ stroke_id: square.id, vertex: i }, centre(h)])),
      c("horizontal", sq(0)), c("vertical", sq(1)), c("horizontal", sq(2)), c("vertical", sq(3)),
      c("equal", [{ stroke_id: square.id, edge: 0 }, { stroke_id: square.id, edge: 1 }]),
    ],
    extrude_mm: 8,
  };
}
