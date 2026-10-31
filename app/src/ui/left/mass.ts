// Mass in the parts tree (Plan 9 D9 left, D8 display): every row shows its mass, a group the sum
// of its children, and the tree a total at the bottom. B2's core sends `mass_g` (+ source and a
// reason when unknown) on every row; a core without it falls back to the built part's
// metrics.mass_g, and a group sums its children here.
import type { PartSummary } from "../../api/types";
import { childrenMap } from "./tree";

export interface Mass { g: number | null; note: string | null }

/** The row's own mass: B2's mass_g, else the built metrics' (null with a reason when unknown). */
export function ownMass(p: PartSummary): Mass {
  if (typeof p.mass_g === "number") return { g: p.mass_g, note: null };
  if (p.metrics) return { g: p.metrics.mass_g, note: null };
  if (p.mass_note) return { g: null, note: p.mass_note };
  return { g: null, note: p.kind === "group" ? null : "not built yet" };
}

/** Every row's mass by id. A group with no mass of its own is the sum of its children (unknown
 *  children leave it out and are counted in `unknown`). The skeleton has none. */
export function masses(list: readonly PartSummary[]): Map<string, Mass & { unknown: number }> {
  const kids = childrenMap(list);
  const out = new Map<string, Mass & { unknown: number }>();
  const visit = (p: PartSummary): Mass & { unknown: number } => {
    const hit = out.get(p.part_id);
    if (hit) return hit;
    const own = ownMass(p);
    let res: Mass & { unknown: number };
    if (p.kind === "skeleton") res = { g: null, note: null, unknown: 0 };
    else if (own.g !== null || p.kind !== "group") res = { ...own, unknown: own.g === null ? 1 : 0 };
    else {
      let g = 0; let unknown = 0; let any = false;
      for (const c of kids.get(p.part_id) ?? []) {
        const m = visit(c);
        if (m.g !== null) { g += m.g; any = true; }
        unknown += m.unknown;
      }
      res = { g: any ? g : null, note: any ? null : "no parts with a mass yet", unknown };
    }
    out.set(p.part_id, res);
    return res;
  };
  for (const p of list) visit(p);
  return out;
}

/** The whole tree: the roots' masses added up, and how many parts have none yet. */
export function totalMass(list: readonly PartSummary[]): { g: number; unknown: number } {
  const m = masses(list);
  let g = 0; let unknown = 0;
  for (const p of childrenMap(list).get(null) ?? []) {
    const x = m.get(p.part_id);
    if (!x) continue;
    if (x.g !== null) g += x.g;
    unknown += x.unknown;
  }
  return { g, unknown };
}

/** "180 g", "1.24 kg", "0.8 g": grams under a kilogram (three significant figures), else kg. */
export function fmtMass(g: number): string {
  if (g >= 1000) return `${(g / 1000).toFixed(g >= 10_000 ? 1 : 2)} kg`;
  if (g >= 100) return `${Math.round(g)} g`;
  if (g >= 10) return `${g.toFixed(1)} g`;
  return `${g.toFixed(g >= 1 ? 1 : 2)} g`;
}
