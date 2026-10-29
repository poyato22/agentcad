// Pure part-tree model: GET /parts rows (parent_id, order) → nested rows, and every move (drag,
// Alt+arrows, the Move menu) → the one POST /parts/reorder {parent_id, ids} that does it. A move
// that would put a part inside itself or its own descendant is never offered (the core refuses
// it too: 400 "that move would create a cycle").
import type { PartSummary } from "../../api/types";
import { partName } from "../../lib/names";

export interface TreeRow { part: PartSummary; depth: number; parentId: string | null; hasChildren: boolean }

export type DropPos = "before" | "after" | "inside";

/** One reorder call: under `parentId` the siblings take this order (re-parenting them there). */
export interface Move { parentId: string | null; ids: string[] }

const idOf = (p: PartSummary): string => p.part_id;

function parentOf(p: PartSummary, ids: ReadonlySet<string>): string | null {
  const pid = p.parent_id ?? null;
  return pid !== null && ids.has(pid) && pid !== p.part_id ? pid : null;
}

function byOrder(a: PartSummary, b: PartSummary): number {
  return (a.order ?? 0) - (b.order ?? 0) || partName(a).localeCompare(partName(b)) || a.part_id.localeCompare(b.part_id);
}

/** Children per parent (null = root), each list in tree order. A row whose parent is missing (or
 *  that sits in a parent loop the core should never produce) shows at the root. */
export function childrenMap(list: readonly PartSummary[]): Map<string | null, PartSummary[]> {
  const ids = new Set(list.map(idOf));
  const out = new Map<string | null, PartSummary[]>();
  const byId = new Map(list.map((p) => [p.part_id, p]));
  for (const p of list) {
    let parent = parentOf(p, ids);
    // break loops: walk up; if we come back to p, show p at the root
    const seen = new Set<string>([p.part_id]);
    for (let cur = parent; cur !== null;) {
      if (seen.has(cur)) { parent = null; break; }
      seen.add(cur);
      const up = byId.get(cur);
      cur = up ? parentOf(up, ids) : null;
    }
    const sibs = out.get(parent) ?? [];
    sibs.push(p);
    out.set(parent, sibs);
  }
  for (const sibs of out.values()) sibs.sort(byOrder);
  return out;
}

/** Depth-first rows; children of a collapsed row are left out. */
export function flatten(list: readonly PartSummary[], collapsed: ReadonlySet<string> = new Set()): TreeRow[] {
  const kids = childrenMap(list);
  const rows: TreeRow[] = [];
  const walk = (parent: string | null, depth: number) => {
    for (const p of kids.get(parent) ?? []) {
      const has = (kids.get(p.part_id)?.length ?? 0) > 0;
      rows.push({ part: p, depth, parentId: parent, hasChildren: has });
      if (has && !collapsed.has(p.part_id)) walk(p.part_id, depth + 1);
    }
  };
  walk(null, 0);
  return rows;
}

/** Is `id` the part `ancestor` or somewhere below it? */
export function isWithin(list: readonly PartSummary[], ancestor: string, id: string): boolean {
  const kids = childrenMap(list);
  const stack = [ancestor];
  while (stack.length) {
    const cur = stack.pop() as string;
    if (cur === id) return true;
    for (const k of kids.get(cur) ?? []) stack.push(k.part_id);
  }
  return false;
}

function siblingIds(kids: Map<string | null, PartSummary[]>, parent: string | null): string[] {
  return (kids.get(parent) ?? []).map(idOf);
}

/** Dropping `dragId` before / after / inside `targetId`; null when it is no move or would nest a
 *  part in itself or its own descendant. */
export function planDrop(list: readonly PartSummary[], dragId: string, targetId: string, pos: DropPos): Move | null {
  if (dragId === targetId) return null;
  if (isWithin(list, dragId, targetId)) return null;          // into itself / its own subtree
  const kids = childrenMap(list);
  const target = list.find((p) => p.part_id === targetId);
  const drag = list.find((p) => p.part_id === dragId);
  if (!target || !drag) return null;
  const ids = new Set(list.map(idOf));
  const parentId = pos === "inside" ? targetId : parentOf(target, ids);
  const sibs = siblingIds(kids, parentId).filter((x) => x !== dragId);
  if (pos === "inside") sibs.push(dragId);
  else {
    const at = sibs.indexOf(targetId);
    sibs.splice(pos === "before" ? at : at + 1, 0, dragId);
  }
  const before = siblingIds(kids, parentOf(drag, ids));
  if (parentId === parentOf(drag, ids) && before.join() === sibs.join()) return null;
  return { parentId, ids: sibs };
}

/** Alt+↑ / Alt+↓ (Move up / Move down): swap with the neighbouring sibling. */
export function planStep(list: readonly PartSummary[], id: string, delta: -1 | 1): Move | null {
  const ids = new Set(list.map(idOf));
  const p = list.find((x) => x.part_id === id);
  if (!p) return null;
  const parent = parentOf(p, ids);
  const sibs = siblingIds(childrenMap(list), parent);
  const i = sibs.indexOf(id);
  const j = i + delta;
  if (i < 0 || j < 0 || j >= sibs.length) return null;
  [sibs[i], sibs[j]] = [sibs[j], sibs[i]];
  return { parentId: parent, ids: sibs };
}

/** Alt+→ (Move into the part above): the last child of the previous sibling. */
export function planIndent(list: readonly PartSummary[], id: string): Move | null {
  const ids = new Set(list.map(idOf));
  const p = list.find((x) => x.part_id === id);
  if (!p) return null;
  const sibs = siblingIds(childrenMap(list), parentOf(p, ids));
  const i = sibs.indexOf(id);
  if (i <= 0) return null;
  return planDrop(list, id, sibs[i - 1], "inside");
}

/** Alt+← (Move out): right after its parent, at the parent's level. */
export function planOutdent(list: readonly PartSummary[], id: string): Move | null {
  const ids = new Set(list.map(idOf));
  const p = list.find((x) => x.part_id === id);
  const parent = p ? parentOf(p, ids) : null;
  if (!p || parent === null) return null;
  return planDrop(list, id, parent, "after");
}

/** The list after a move (optimistic, until the core's answer replaces it). */
export function applyMove(list: readonly PartSummary[], m: Move): PartSummary[] {
  const at = new Map(m.ids.map((id, i) => [id, i]));
  return list.map((p) => (at.has(p.part_id) ? { ...p, parent_id: m.parentId, order: at.get(p.part_id) } : p));
}

/** Where a pointer at `y` within a row of height `h` drops: top quarter before, bottom quarter
 *  after, the middle inside. */
export function dropPosAt(y: number, h: number): DropPos {
  if (y < h * 0.25) return "before";
  if (y > h * 0.75) return "after";
  return "inside";
}
