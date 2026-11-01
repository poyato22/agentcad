// G8 (Plan 11 F8): what the composer's Think control holds. `thinkChoice` is the active build's
// pick (Auto · Low · Medium · High · Max), saved as that build's default (core PUT
// /projects/{id}/effort) so it persists; every message carries it until it is changed.
import { effect, signal, type Signal } from "@preact/signals";
import type { ThinkChoice } from "../../../api/think";
import { activeBuildId } from "../../../state/builds";
import { actions } from "../../../state/store";

export const thinkChoice: Signal<ThinkChoice> = signal<ThinkChoice>("auto");
/** Each build's saved pick ("auto" when none); a build not loaded yet is missing. */
export const buildThink: Signal<ReadonlyMap<string, ThinkChoice>> = signal<ReadonlyMap<string, ThinkChoice>>(new Map());
/** false once the core answered that it has no effort routes (an older core): the control hides. */
export const thinkSupported: Signal<boolean> = signal(true);

// the control shows the active build's pick (a build switch shows that build's)
effect(() => {
  const b = activeBuildId.value;
  const saved = b ? buildThink.value.get(b) : undefined;
  if (saved !== undefined) thinkChoice.value = saved;
});

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

/** Save `choice` as the build's pick (Auto clears it). */
export async function saveBuildThink(buildId: string, choice: ThinkChoice): Promise<void> {
  setBuild(buildId, choice);
  const v = await actions.client().setEffortDefault(buildId, choice);
  setBuild(buildId, v.default);
}

/** The pick the next message carries (null: Auto). It stays: the build's pick persists. */
export function takeThink(): ThinkChoice | null {
  const c = thinkChoice.peek();
  return c === "auto" ? null : c;
}

/** Tests. */
export function resetThink(): void {
  thinkChoice.value = "auto";
  buildThink.value = new Map();
  thinkSupported.value = true;
}
