// Hooks other features plug into the part menu with, so the menu stays the one place a part's
// actions live. U2a's measure tool registers `measure` (its absence falls back to Properties);
// U2a's "Modify…" (the ops form) and later features add items with addPartMenuItem.
import { signal, type Signal } from "@preact/signals";

export interface PartMenuExtra {
  label: string;
  /** Shown only for one part (not a multi-selection). */
  single?: boolean;
  run(partIds: string[]): void;
}

/** Starts measuring on these parts (selects them in the viewer's measure mode). */
export const measureHook: Signal<((partIds: string[]) => void) | null> = signal(null);

export const extraPartMenuItems: Signal<readonly PartMenuExtra[]> = signal<readonly PartMenuExtra[]>([]);

/** Adds an item to every part's menu; returns its removal. */
export function addPartMenuItem(item: PartMenuExtra): () => void {
  extraPartMenuItems.value = [...extraPartMenuItems.peek(), item];
  return () => { extraPartMenuItems.value = extraPartMenuItems.peek().filter((x) => x !== item); };
}
