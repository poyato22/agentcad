// Mass in the parts tree (Plan 9 D9 left, D8 display): every row shows its mass, a group the sum
// of its children, and the tree a total at the bottom. The numbers come from lib/mass.ts (B2's
// one reader of a part's mass): the core's `mass_g` (+ source and a reason when unknown) on every
// row, or, from a core without it, the built part's metrics.mass_g with groups summed here.
import type { PartSummary } from "../../api/types";
import { fmtMass as fmtMassLib, partMassG, partMassNote } from "../../lib/mass";
import { childrenMap } from "./tree";

export interface Mass { g: number | null; note: string | null }
export type TreeMass = Mass & { unknown: number; sum: boolean };

/** The row's own mass (lib/mass partMassG), with the reason when it is unknown. */
export function ownMass(p: PartSummary): Mass {
  const g = partMassG(p);
  return { g, note: g === null ? partMassNote(p) ?? (p.kind === "group" ? null : "not built yet") : null };
}

/** Every row's mass by id. The core's mass wins (its groups and parents already carry their
 *  subtree's sum); a group without one is the sum of its children here (unknown children leave
 *  it out and are counted in `unknown`). The skeleton has none. `note` is the hover text. */
export function masses(list: readonly PartSummary[]): Map<string, TreeMass> {
  const kids = childrenMap(list);
  const out = new Map<string, TreeMass>();
  const visit = (p: PartSummary): TreeMass => {
    const hit = out.get(p.part_id);
    if (hit) return hit;
    out.set(p.part_id, { g: null, note: null, unknown: 0, sum: false });   // a cycle ends here
    const own = ownMass(p);
    let unknownBelow = 0;
    let g = 0; let any = false;
    for (const c of kids.get(p.part_id) ?? []) {
      const m = visit(c);
      if (m.g !== null) { g += m.g; any = true; }
      unknownBelow += m.unknown;
    }
    let res: TreeMass;
    if (p.kind === "skeleton") res = { g: null, note: null, unknown: 0, sum: false };
    else if (own.g !== null || p.kind !== "group") {
      res = { g: own.g, note: partMassNote(p) ?? own.note, unknown: (own.g === null ? 1 : 0) + unknownBelow, sum: p.mass_source === "sum" };
    } else {
      res = { g: any ? g : null, note: any ? partMassNote(p) : (partMassNote(p) ?? "no parts with a mass yet"), unknown: unknownBelow, sum: any };
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

/** A tree row's mass text: "Σ" in front of a sum, "—" when unknown, nothing for the skeleton. */
export function rowMassText(p: PartSummary, m: TreeMass | undefined): string {
  if (p.kind === "skeleton" || !m) return "";
  if (m.g === null) return "—";
  return `${m.sum ? "Σ " : ""}${fmtMass(m.g)}`;
}

/** "180 g", "1.24 kg", "0.8 g" (lib/mass fmtMass). */
export function fmtMass(g: number): string { return fmtMassLib(g); }
