// An agent-driven assembly animation (R19). When a lead or sub-agent asks the viewer to move the
// assembly — a joint sweep or a named motion sequence — the core emits `assembly_animate`
// {project_id, joint_id|sequence, from, to, duration_s, loop}. This module parses that event,
// tracks the one active animation, and drives whatever playback engine the viewer registers, so a
// "Agent is animating…" chip with a Stop can show while it plays. Untrusted values are numbers only.
import { signal, type Signal } from "@preact/signals";
import { onCoreEvent } from "../api/events";
import type { AnimateSpec, MisesEvent } from "../api/types";

export interface ActiveAnimation { spec: AnimateSpec; runId: string | null; startedAt: number }

export const agentAnimation: Signal<ActiveAnimation | null> = signal<ActiveAnimation | null>(null);

/** The viewer's playback engine (registered by the assembly view). Kept out of this module so the
 *  state has no dependency on Three.js. */
export interface AnimationPlayer { play(spec: AnimateSpec): void; stop(): void }
let player: AnimationPlayer | null = null;
export function setAnimationPlayer(p: AnimationPlayer | null): void { player = p; }

const numOrList = (v: unknown): number | number[] | null =>
  typeof v === "number" ? v : Array.isArray(v) && v.every((x) => typeof x === "number") ? (v as number[]) : null;
const str = (v: unknown): string | null => (typeof v === "string" && v.trim() !== "" ? v : null);

/** An `assembly_animate` event → a spec, or null when it names neither a joint nor a sequence. */
export function parseAnimateSpec(ev: MisesEvent): AnimateSpec | null {
  if (ev.type !== "assembly_animate") return null;
  const d = ev.data;
  const jointId = str(d.joint_id);
  const sequence = str(d.sequence);
  if (!jointId && !sequence) return null;
  const dur = typeof d.duration_s === "number" && d.duration_s > 0 ? d.duration_s : 2;
  return {
    project_id: str(d.project_id), joint_id: jointId, sequence,
    from: numOrList(d.from), to: numOrList(d.to), duration_s: dur, loop: d.loop === true,
  };
}

export function recordAssemblyAnimation(ev: MisesEvent): void {
  if ((ev.type === "run_finished" || ev.type === "run_deleted") && ev.run_id && agentAnimation.value?.runId === ev.run_id) {
    stopAgentAnimation();
    return;
  }
  const spec = parseAnimateSpec(ev);
  if (!spec) return;
  agentAnimation.value = { spec, runId: ev.run_id, startedAt: Date.now() / 1000 };
  player?.play(spec);
}

/** Stop the active animation: clear the chip and tell the viewer to put the assembly back. */
export function stopAgentAnimation(): void {
  if (agentAnimation.value === null) return;
  agentAnimation.value = null;
  player?.stop();
}

export function resetAssemblyAnimation(): void { agentAnimation.value = null; }

/** Subscribe to the event stream (default onCoreEvent). Call once at boot; returns an unsubscribe. */
export function startAssemblyAnimation(subscribe: (cb: (e: MisesEvent) => void) => () => void = onCoreEvent): () => void {
  return subscribe(recordAssemblyAnimation);
}
