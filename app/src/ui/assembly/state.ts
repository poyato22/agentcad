// The build view's own state beside the controller: its view modes (section, X-ray, explode: the
// part viewer's ui/viewer/modes, kept apart from the part sheet's designModes), whether Play's
// full-window view is open, and the latest agent animation per build (what Play plays).
import { signal, type Signal } from "@preact/signals";
import { onCoreEvent } from "../../api/events";
import type { AnimateSpec } from "../../api/types";
import { parseAnimateSpec } from "../../state/assemblyAnimation";
import { MODES_OFF, type ModesState } from "../viewer/modes/state";

export const assemblyModes: Signal<ModesState> = signal<ModesState>(MODES_OFF);

/** Play's full-window view of the assembly (D12): "animation" plays the agent's latest motion,
 *  "tour" an exploded orbit. null: closed. */
export const immersive: Signal<"animation" | "tour" | null> = signal<"animation" | "tour" | null>(null);

const latest = new Map<string, AnimateSpec>();
let latestAny: AnimateSpec | null = null;

/** The newest `assembly_animate` an agent sent for this build (any build when it named none). */
export function latestAnimation(projectId: string | null): AnimateSpec | null {
  return (projectId ? latest.get(projectId) : null) ?? (latestAny?.project_id ? null : latestAny);
}

export function recordAnimation(spec: AnimateSpec): void {
  if (spec.project_id) latest.set(spec.project_id, spec);
  else latestAny = spec;
}

onCoreEvent((ev) => { const s = parseAnimateSpec(ev); if (s) recordAnimation(s); });

/** Tests: forget everything. */
export function resetAssemblyState(): void {
  assemblyModes.value = MODES_OFF;
  immersive.value = null;
  latest.clear();
  latestAny = null;
}
