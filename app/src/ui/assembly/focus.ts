// Plan 9 D7: the Design page always shows the build's assembly. Clicking a part in the left tree
// opens that part alone in the part viewer; "← Assembly" (or Esc) goes back. This replaces the
// Part | Assembly switch (and its ⌥⌘A): there is no mode to remember, only which part is open.
import { signal, type Signal } from "@preact/signals";
import { actions } from "../../state/store";
import { onBuildSwitch } from "../../state/builds";

/** The part open on its own, or null: the assembly. */
export const openPartId: Signal<string | null> = signal<string | null>(null);

/** Open one part alone (it becomes the selected part, which the part viewer shows). */
export function openPart(id: string): void {
  actions.selectPart(id);
  openPartId.value = id;
}

/** Back to the assembly. */
export function closePart(): void { openPartId.value = null; }

// another build: its assembly, never a part of the last one
onBuildSwitch(() => { openPartId.value = null; });
