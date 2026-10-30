// Part-tree writes: move (POST /parts/reorder), color, appearance and rename (PATCH /parts/{id}),
// and the part menu's duplicate / delete / export / add-to-assembly. Edits show at once
// (optimistic) and are put back, with the core's reason, if the core refuses them.
import { signal, type Signal } from "@preact/signals";
import { CoreError } from "../../api/client";
import type { PartSummary } from "../../api/types";
import { fromPreset, withColor, type Appearance, type PresetId } from "../../lib/appearance";
import { partName } from "../../lib/names";
import { activeBuildId, isActiveBuild, loadParts } from "../../state/builds";
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

export async function deleteParts(ids: readonly string[]): Promise<boolean> {
  const build = activeBuildId.peek();
  treeError.value = null;
  const names = ids.map(nameOf);
  let done = 0;
  try {
    for (const id of ids) { await actions.client().deletePart(id); done++; }
  } catch (e) {
    treeError.value = `Couldn't delete ${names[done]}: ${reason(e)}`;
  }
  if (done && isActiveBuild(build)) {
    await loadParts(build);
    note(done === 1 ? `Deleted ${names[0]}` : `Deleted ${done} parts`);
  }
  return done === ids.length;
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
