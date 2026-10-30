// Multi-select in the part list (Plan 7 U8, R20): a click selects one part, ⌘-click adds or
// removes one, Shift-click selects the range from the last plain click. The store's
// selectedPartId stays the one "primary" part every other pane follows (the viewer, the title
// block); the set is the list's own and only the menu acts on it (hide, isolate, appearance, delete).
import { computed, signal, type ReadonlySignal, type Signal } from "@preact/signals";
import { actions, selectedPartId } from "../../state/store";

/** Extra selected part ids (the primary is always in the effective selection). */
export const multiSel: Signal<ReadonlySet<string>> = signal<ReadonlySet<string>>(new Set());
let anchor: string | null = null;

/** The selection the list shows: the set when it holds the primary part, else just the primary
 *  (a selection made elsewhere — a click in the viewer — replaces the set). */
export const selectedIds: ReadonlySignal<ReadonlySet<string>> = computed(() => {
  const p = selectedPartId.value;
  const m = multiSel.value;
  if (p !== null && m.has(p)) return m;
  return p === null ? new Set<string>() : new Set([p]);
});

export interface ClickMods { shift?: boolean; toggle?: boolean }

/** A click on a row: `order` is the list's visible row order (for Shift ranges). */
export function clickSelect(id: string, mods: ClickMods, order: readonly string[]): void {
  if (mods.shift && anchor !== null && order.includes(anchor)) {
    const a = order.indexOf(anchor);
    const b = order.indexOf(id);
    const range = order.slice(Math.min(a, b), Math.max(a, b) + 1);
    multiSel.value = new Set(range);
    actions.selectPart(id);
    return;
  }
  if (mods.toggle) {
    const cur = new Set(selectedIds.peek());
    if (cur.has(id) && cur.size > 1) {
      cur.delete(id);
      multiSel.value = cur;
      // the primary moves to another selected part
      if (selectedPartId.peek() === id) actions.selectPart([...cur][cur.size - 1]);
      return;
    }
    cur.add(id);
    multiSel.value = cur;
    anchor = id;
    actions.selectPart(id);
    return;
  }
  anchor = id;
  multiSel.value = new Set([id]);
  actions.selectPart(id);
}

/** The parts a menu opened on `id` acts on: the selection when `id` is in it, else just `id`. */
export function targetsFor(id: string): string[] {
  const s = selectedIds.peek();
  return s.has(id) ? [...s] : [id];
}

/** Back to one selected part (or none). */
export function resetSelection(): void { multiSel.value = new Set(); anchor = null; }
