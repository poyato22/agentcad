// Part-tree writes: move (POST /parts/reorder), color, appearance and rename (PATCH /parts/{id}),
// and the part menu's duplicate / delete / export / add-to-assembly. Edits show at once
// (optimistic) and are put back, with the core's reason, if the core refuses them.
import { signal, type Signal } from "@preact/signals";
import { CoreError, type AssemblyUndo } from "../../api/client";
import { onCoreEvent } from "../../api/events";
import type { MisesEvent, PartSummary } from "../../api/types";
import { fromPreset, withColor, type Appearance, type PresetId } from "../../lib/appearance";
import { partName } from "../../lib/names";
import { activeBuildId, builds, isActiveBuild, loadParts } from "../../state/builds";
import { actions, parts } from "../../state/store";
import { applyMove, type Move } from "./tree";

/** The last refused tree change, in plain words (cleared by the next one). */
export const treeError: Signal<string | null> = signal<string | null>(null);

function reason(e: unknown): string {
  return e instanceof CoreError ? e.message : e instanceof Error ? e.message : String(e);
}

function patchLocal(id: string, patch: Partial<PartSummary>): readonly PartSummary[] {
  const before = parts.peek();
  parts.value = before.map((p) => (p.part_id === id ? { ...p, ...patch } : p));
  return before;
}

/** Moves are sent one at a time, in the order made (two quick Alt+↑ presses never race). */
let moveChain: Promise<unknown> = Promise.resolve();
let moveSeq = 0;

export async function commitMove(m: Move): Promise<boolean> {
  const build = activeBuildId.peek();
  if (!build) return false;
  treeError.value = null;
  parts.value = applyMove(parts.peek(), m);              // shown at once
  const mine = ++moveSeq;
  const run = moveChain.then(async () => {
    try {
      const list = await actions.client().reorderParts(build, m.parentId, m.ids);
      // a later move is already on screen: its answer is the one to keep
      if (mine === moveSeq && isActiveBuild(build)) parts.value = list;
      return true;
    } catch (e) {
      treeError.value = `Couldn't move the part: ${reason(e)}`;
      // the optimistic state may hold later moves too: take the core's tree as it is
      if (isActiveBuild(build)) await loadParts(build);
      return false;
    }
  });
  moveChain = run.catch(() => false);
  return run;
}

/** Take the core's answer for a part's look (color + appearance) without touching the rest. */
function takeLook(row: PartSummary): void {
  parts.value = parts.peek().map((p) => (p.part_id === row.part_id ? { ...p, color: row.color ?? null, appearance: row.appearance ?? null } : p));
}

/** Recolor parts; each keeps its finish (the core recolors the appearance with the color). */
export async function setColor(id: string | readonly string[], color: string | null): Promise<boolean> {
  const ids = typeof id === "string" ? [id] : id;
  return patchLooks(ids, () => ({ color }), (p) => ({ color, appearance: withColor(p.appearance, color) }), "set the color");
}

/** Give parts a material: a preset (an untinted one keeps each part's color), or null for the
 *  plain drafting look. */
export async function setFinish(ids: readonly string[], preset: PresetId | null): Promise<boolean> {
  const next = (p: PartSummary): Appearance | null => (preset === null ? null : fromPreset(preset, p.color ?? null));
  return patchLooks(ids, (p) => ({ appearance: next(p) }), (p) => ({ appearance: next(p), color: next(p)?.color ?? p.color ?? null }), "set the appearance");
}

/** One PATCH per part, shown at once. Each part keeps its own outcome: one the core refused goes
 *  back to how it was (named in the error), the ones it accepted keep the core's answer. */
async function patchLooks(ids: readonly string[], body: (p: PartSummary) => { color?: string | null; appearance?: Appearance | null },
  local: (p: PartSummary) => Partial<PartSummary>, what: string): Promise<boolean> {
  treeError.value = null;
  const before = new Map(parts.peek().filter((p) => ids.includes(p.part_id)).map((p) => [p.part_id, p]));
  parts.value = parts.peek().map((p) => (before.has(p.part_id) ? { ...p, ...local(p) } : p));
  const results = await Promise.allSettled([...before.values()].map((p) => actions.client().patchPart(p.part_id, body(p))));
  const failed: string[] = [];
  let firstError: unknown = null;
  results.forEach((r, i) => {
    const id = [...before.keys()][i];
    if (r.status === "fulfilled") { takeLook(r.value); return; }
    failed.push(id);
    firstError ??= r.reason;
    const old = before.get(id) as PartSummary;
    parts.value = parts.peek().map((p) => (p.part_id === id ? { ...p, color: old.color ?? null, appearance: old.appearance ?? null } : p));
  });
  if (!failed.length) return true;
  const who = failed.length === ids.length ? "" : ` for ${failed.map(nameOf).join(", ")}`;
  treeError.value = `Couldn't ${what}${who}: ${reason(firstError)}`;
  return false;
}

/** A plain-words note after a menu action ("Exported Arm.step to Downloads"); cleared by the next one. */
export const treeNote: Signal<string | null> = signal<string | null>(null);

function note(text: string): void { treeNote.value = text; treeError.value = null; }

export async function duplicateParts(ids: readonly string[]): Promise<string[]> {
  const build = activeBuildId.peek();
  treeError.value = null;
  const made: string[] = [];
  try {
    for (const id of ids) made.push((await actions.client().duplicatePart(id)).part_id);
  } catch (e) {
    treeError.value = `Couldn't duplicate the part: ${reason(e)}`;
  }
  if (made.length && isActiveBuild(build)) {
    await loadParts(build);
    actions.selectPart(made[made.length - 1]);
    note(made.length === 1 ? `Made ${nameOf(made[0])}` : `Made ${made.length} copies`);
  }
  return made;
}

/** Plan 9 D29: the last delete, for its Undo toast (null once undone, replaced or timed out). */
export interface DeletedParts {
  text: string; build: string | null; items: { id: string; undo: string }[];
  /** "Remove from assembly": the instances and joints to put back (no part was deleted). */
  assembly?: { build: string; undo: AssemblyUndo; count: number };
}
export const lastDeleted: Signal<DeletedParts | null> = signal<DeletedParts | null>(null);

/** Deletes parts with their assembly instances (DELETE /parts/{id}?cascade=true); the Undo toast
 *  (lastDeleted) puts them all back. */
export async function deleteParts(ids: readonly string[]): Promise<boolean> {
  const build = activeBuildId.peek();
  treeError.value = null;
  const names = ids.map(nameOf);
  const items: { id: string; undo: string }[] = [];
  let done = 0;
  let instances = 0;
  try {
    for (const id of ids) {
      const r = await actions.client().deletePart(id, { cascade: true });
      if (r.undo) items.push({ id, undo: r.undo });
      instances += r.removed_instances.length;
      done++;
    }
  } catch (e) {
    treeError.value = `Couldn't delete ${names[done]}: ${reason(e)}`;
  }
  if (done && isActiveBuild(build)) {
    await loadParts(build);
    const what = done === 1 ? `Deleted ${names[0]}` : `Deleted ${done} parts`;
    const text = instances ? `${what} and ${instances === 1 ? "its instance" : `${instances} instances`}` : what;
    lastDeleted.value = items.length ? { text, build, items } : null;
    if (!items.length) note(text);
  }
  return done === ids.length;
}

/** An agent's part_delete (always approved by the user first): the tree re-reads and the same
 *  Undo toast offers it back (coordinator ruling, U-C fix round 1). */
export function onAgentDeleted(ev: MisesEvent): void {
  if (ev.type !== "part_deleted") return;
  const d = ev.data;
  const id = typeof d["part_id"] === "string" ? d["part_id"] : null;
  const undo = typeof d["undo"] === "string" ? d["undo"] : null;
  const build = typeof d["project_id"] === "string" ? d["project_id"] : null;
  if (!id) return;
  const n = Array.isArray(d["removed_instances"]) ? d["removed_instances"].length : 0;
  const name = typeof d["label"] === "string" && d["label"] ? d["label"] : nameOf(id);
  const here = isActiveBuild(build);
  if (here) void loadParts(build);
  // another build's delete keeps its Undo too, naming the build (re-review N4)
  const where = here ? "" : ` in ${builds.peek().find((b) => b.id === build)?.name ?? "another build"}`;
  if (undo) lastDeleted.value = {
    text: `An agent deleted ${name}${where}${n ? (n === 1 ? " and its instance" : ` and ${n} instances`) : ""}`, build, items: [{ id, undo }],
  };
}
onCoreEvent(onAgentDeleted);

/** Undo the last delete: each part comes back (files, tree row, assembly instances). */
export async function undoDelete(): Promise<boolean> {
  const d = lastDeleted.peek();
  if (!d) return false;
  lastDeleted.value = null;
  treeError.value = null;
  let ok = true;
  if (d.assembly) {
    try {
      await actions.client().restoreAssembly(d.assembly.build, d.assembly.undo);
      note(d.assembly.count === 1 ? "Put it back in the assembly" : `Put ${d.assembly.count} instances back in the assembly`);
      return true;
    } catch (e) {
      treeError.value = `Couldn't put it back in the assembly: ${reason(e)}`;
      return false;
    }
  }
  // newest first, so a parent comes back before its re-parented children look for it
  for (const it of [...d.items].reverse()) {
    try { await actions.client().restorePart(it.id, it.undo); } catch (e) {
      ok = false;
      treeError.value = `Couldn't undo the delete of ${it.id}: ${reason(e)}`;
    }
  }
  if (isActiveBuild(d.build)) await loadParts(d.build);
  if (ok) note(d.items.length === 1 ? "Restored the part" : `Restored ${d.items.length} parts`);
  return ok;
}

/** How many assembly instances these parts have in the build view's assembly (for the confirm). */
export function instanceCount(ids: readonly string[]): number {
  const asm = assemblyOf.value;
  return asm ? asm.instances.filter((i) => ids.includes(i.part_id)).length : 0;
}
/** The build view's assembly (set by ui/assembly, so this module needs no Three.js). */
export const assemblyOf: Signal<{
  instances: readonly { id: string; part_id: string; name?: string }[];
  joints?: readonly { a: { instance_id: string }; b: { instance_id: string } }[];
  /** Plan 9 B2: instances x their part's mass (the left surface's one total, FW-app M7). */
  total_mass_g?: number | null;
  mass_unknown?: readonly { part_id: string; count: number; reason: string }[];
} | null> = signal(null);

/** What "Remove from assembly" takes with it: the instances, and the joints that come loose with
 *  the names of the parts on their other end (for the confirm). */
export function removalOf(ids: readonly string[]): { instances: number; joints: number; loose: string[] } {
  const asm = assemblyOf.value;
  if (!asm) return { instances: 0, joints: 0, loose: [] };
  const gone = new Set(asm.instances.filter((i) => ids.includes(i.part_id)).map((i) => i.id));
  const js = (asm.joints ?? []).filter((j) => gone.has(j.a.instance_id) || gone.has(j.b.instance_id));
  const other = new Set(js.flatMap((j) => [j.a.instance_id, j.b.instance_id]).filter((x) => !gone.has(x)));
  const loose = asm.instances.filter((i) => other.has(i.id)).map((i) => i.name ?? nameOf(i.part_id));
  return { instances: gone.size, joints: js.length, loose };
}

/** "Remove from assembly": takes these parts' instances (and the joints that come loose) out of
 *  the build's assembly and keeps the parts; the Undo toast puts them back exactly (re-review N1). */
export async function removeFromAssembly(ids: readonly string[]): Promise<boolean> {
  const build = activeBuildId.peek();
  if (!build) return false;
  treeError.value = null;
  try {
    const r = await actions.client().removePartsFromAssembly(build, [...ids]);
    const n = r.removed_instances.length;
    const j = r.removed_joints.length;
    const what = n === 1 ? `Removed ${nameOf(ids[0])} from the assembly` : `Removed ${n} instances from the assembly`;
    lastDeleted.value = n ? { text: j ? `${what} (${j} joint${j === 1 ? "" : "s"} came loose)` : what, build, items: [],
      assembly: { build, undo: r.undo, count: n } } : null;
    return true;
  } catch (e) {
    treeError.value = `Couldn't remove it from the assembly: ${reason(e)}`;
    return false;
  }
}

export async function exportPart(id: string, format: "step" | "stl"): Promise<boolean> {
  treeError.value = null;
  try {
    const r = await actions.client().exportPart(id, format);
    note(`Exported ${r.name} to ${folderOf(r.path)}`);
    return true;
  } catch (e) {
    treeError.value = `Couldn't export ${format.toUpperCase()}: ${reason(e)}`;
    return false;
  }
}

/** Adds an instance of each part to the build's assembly; the first one in an empty assembly is
 *  fixed (it grounds the assembly). */
export async function addToAssembly(ids: readonly string[]): Promise<boolean> {
  const build = activeBuildId.peek();
  if (!build) return false;
  treeError.value = null;
  try {
    const c = actions.client();
    const asm = await c.assembly(build);
    const names = new Set(asm.instances.map((i) => i.name));
    let empty = asm.instances.length === 0;
    for (const id of ids) {
      const base = nameOf(id);
      let name = base;
      for (let n = 2; names.has(name); n++) name = `${base} ${n}`;
      names.add(name);
      await c.addInstance(build, { part_id: id, name, fixed: empty });
      empty = false;
    }
    note(ids.length === 1 ? `Added ${nameOf(ids[0])} to the assembly` : `Added ${ids.length} parts to the assembly`);
    return true;
  } catch (e) {
    treeError.value = `Couldn't add to the assembly: ${reason(e)}`;
    return false;
  }
}

function nameOf(id: string): string {
  const p = parts.peek().find((x) => x.part_id === id);
  return p ? partName(p) : id;
}

/** "/Users/me/Downloads/arm.step" → "Downloads". */
function folderOf(path: string): string {
  const bits = path.split("/").filter(Boolean);
  return bits.length > 1 ? bits[bits.length - 2] : path;
}

export async function renamePart(id: string, label: string): Promise<boolean> {
  const name = label.trim();
  if (!name) return false;
  treeError.value = null;
  const before = patchLocal(id, { label: name });
  try {
    const row = await actions.client().patchPart(id, { label: name });
    parts.value = parts.peek().map((p) => (p.part_id === id ? { ...p, label: row.label ?? name } : p));
    return true;
  } catch (e) {
    parts.value = before;
    treeError.value = `Couldn't rename the part: ${reason(e)}`;
    return false;
  }
}
