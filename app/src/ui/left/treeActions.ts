// Part-tree writes: move (POST /parts/reorder), color and rename (PATCH /parts/{id}). Each shows
// at once (optimistic) and is put back, with the core's reason, if the core refuses it.
import { signal, type Signal } from "@preact/signals";
import { CoreError } from "../../api/client";
import type { PartSummary } from "../../api/types";
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

export async function setColor(id: string, color: string | null): Promise<boolean> {
  treeError.value = null;
  const before = patchLocal(id, { color });
  try {
    const row = await actions.client().patchPart(id, { color });
    parts.value = parts.peek().map((p) => (p.part_id === id ? { ...p, color: row.color ?? null } : p));
    return true;
  } catch (e) {
    parts.value = before;
    treeError.value = `Couldn't set the color: ${reason(e)}`;
    return false;
  }
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
