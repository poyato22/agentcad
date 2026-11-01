// Plan 11 F8: what the composer's Think control holds. `thinkChoice` is the next message's pick
// (Auto · Normal · Hard · Max); a pick other than Auto goes with one message, then the control is
// back on Auto. "Every message in this build" saves the pick as the build's default instead
// (core PUT /projects/{id}/effort); with Auto chosen the core then uses that default.
import { signal, type Signal } from "@preact/signals";
import type { ThinkChoice } from "../../../api/think";
import { actions } from "../../../state/store";

export const thinkChoice: Signal<ThinkChoice> = signal<ThinkChoice>("auto");
/** Each build's saved default ("auto" when none); a build not loaded yet is missing. */
export const buildThink: Signal<ReadonlyMap<string, ThinkChoice>> = signal<ReadonlyMap<string, ThinkChoice>>(new Map());
/** false once the core answered that it has no effort routes (an older core): the control hides. */
export const thinkSupported: Signal<boolean> = signal(true);

function setBuild(buildId: string, choice: ThinkChoice): void {
  const next = new Map(buildThink.peek());
  next.set(buildId, choice);
  buildThink.value = next;
}

export async function loadBuildThink(buildId: string): Promise<void> {
  try {
    const v = await actions.client().effortDefault(buildId);
    if (v === null) { thinkSupported.value = false; return; }
    setBuild(buildId, v.default);
  } catch { /* the control keeps Auto */ }
}

/** Save `choice` as the build's default (Auto clears it). */
export async function saveBuildThink(buildId: string, choice: ThinkChoice): Promise<void> {
  const v = await actions.client().setEffortDefault(buildId, choice);
  setBuild(buildId, v.default);
}

/** The pick the next message carries (null: Auto); the one-shot pick goes back to Auto. */
export function takeThink(): ThinkChoice | null {
  const c = thinkChoice.peek();
  if (c !== "auto") thinkChoice.value = "auto";
  return c === "auto" ? null : c;
}

/** Tests. */
export function resetThink(): void {
  thinkChoice.value = "auto";
  buildThink.value = new Map();
  thinkSupported.value = true;
}
