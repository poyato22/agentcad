// The build view's own state beside the controller: its view modes (section, X-ray, explode: the
// part viewer's ui/viewer/modes, kept apart from the part sheet's designModes), what Play's
// full-window view shows, and the latest agent animation per build (what Play plays).
import { signal, type Signal } from "@preact/signals";
import { onCoreEvent } from "../../api/events";
import type { AnimateSpec, AnimateStep } from "../../api/types";
import { parseAnimateSpec } from "../../state/assemblyAnimation";
import type { ViewSweep } from "../viewer/modes/viewSpec";
import { MODES_OFF, type ModesState } from "../viewer/modes/state";

export const assemblyModes: Signal<ModesState> = signal<ModesState>(MODES_OFF);

/** The assembly full window (D11 / D12 / D18): "tour" an exploded orbit (Play with no agent
 *  animation); "animation" a motion the user plays, scrubs, loops and speeds up (Play's latest
 *  agent animation, or an agent's presented one); "sweep" a section plane sweeping through once,
 *  then the user's slider (an agent's presented section sweep). */
export type Immersive =
  | { mode: "tour" }
  | { mode: "animation"; steps: readonly AnimateStep[]; loop: boolean; title: string; caption: string }
  | { mode: "sweep"; sweep: ViewSweep; title: string; caption: string };

/** Play's (or a presented card's) full-window view of the assembly. null: closed. */
export const immersive: Signal<Immersive | null> = signal<Immersive | null>(null);

/** A card asks for the build view full window: the Design page shows the assembly (not a part)
 *  and opens it. The assembly view registers this (it owns the page switch). */
let presenter: ((what: Exclude<Immersive, { mode: "tour" }>) => void) | null = null;
export function setPresenter(p: typeof presenter): void { presenter = p; }
export function presentOnAssembly(what: Exclude<Immersive, { mode: "tour" }>): void {
  if (presenter) presenter(what);
  else immersive.value = what;
}

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
