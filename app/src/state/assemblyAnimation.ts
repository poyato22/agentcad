// An agent-driven assembly animation (R19). When an agent shows a motion, the core emits
// `assembly_animate` (U3c): the single-joint form {project_id, joint_id, from, to, duration_s,
// loop, loops} or a sequence {project_id, sequence: [{joint_id, from, to, duration_s}, …], loop,
// loops}. This module normalises it to steps, keeps the one active animation for the "Agent is
// animating…" chip, and drives the playback engine the assembly view registers. An animation for
// another build than the one on screen is ignored. Event values are untrusted: numbers only.
import { signal, type Signal } from "@preact/signals";
import { onCoreEvent } from "../api/events";
import type { AnimateFollow, AnimateSpec, AnimateStep, MisesEvent } from "../api/types";
import { activeBuildId } from "./builds";

/** `playing`: the viewer shows it now; false: the assembly isn't on screen — the chip offers Watch. */
export interface ActiveAnimation { spec: AnimateSpec; runId: string | null; startedAt: number; playing: boolean }

export const agentAnimation: Signal<ActiveAnimation | null> = signal<ActiveAnimation | null>(null);

/** The viewer's playback engine (registered by the assembly view, so this module needs no
 *  Three.js). `play` starts only when the build's assembly is on screen; `watch` brings it on
 *  screen first. Both call `onEnd` when a playback finishes by itself. */
export interface AnimationPlayer {
  play(spec: AnimateSpec, onEnd: () => void): boolean;
  watch(spec: AnimateSpec, onEnd: () => void): Promise<boolean>;
  stop(): void;
}
let player: AnimationPlayer | null = null;
export function setAnimationPlayer(p: AnimationPlayer | null): void { player = p; }

/** U3c assembly_animate MAX_LOOPS: a looping animation with no count plays this many times. */
export const MAX_LOOPS = 10;
const MAX_STEPS = 12;

const value = (v: unknown): number | number[] | null =>
  typeof v === "number" && Number.isFinite(v) ? v
    : Array.isArray(v) && v.length > 0 && v.length <= 3 && v.every((x) => typeof x === "number" && Number.isFinite(x)) ? (v as number[]) : null;
const str = (v: unknown): string | null => (typeof v === "string" && v.trim() !== "" ? v : null);

/** A presented animation's joint steps (Plan 9 G-D; at most 12), dropping what doesn't parse. */
export function parseAnimateSteps(raw: unknown): AnimateStep[] {
  return Array.isArray(raw) ? raw.slice(0, MAX_STEPS).map(step).filter((s): s is AnimateStep => s !== null) : [];
}

function step(raw: unknown): AnimateStep | null {
  if (typeof raw !== "object" || raw === null) return null;
  const d = raw as Record<string, unknown>;
  const joint = str(d.joint_id);
  const from = value(d.from);
  const to = value(d.to);
  if (!joint || to === null) return null;
  const dur = typeof d.duration_s === "number" && d.duration_s > 0 ? Math.min(60, d.duration_s) : 2;
  const follow = followOf(d.follow, joint);
  return { joint_id: joint, from: from ?? to, to, duration_s: dur, ...(follow.length ? { follow } : {}) };
}

/** G4: the joints a step carries along (gear trains, closed linkages), each with ≥ 2 samples;
 *  anything that doesn't parse is dropped (untrusted), the driver itself never follows. */
const MAX_FOLLOW = 64;
const MAX_SAMPLES = 129;
function followOf(raw: unknown, driver: string): AnimateFollow[] {
  if (!Array.isArray(raw)) return [];
  const out: AnimateFollow[] = [];
  for (const f of raw.slice(0, MAX_FOLLOW)) {
    if (typeof f !== "object" || f === null) continue;
    const r = f as Record<string, unknown>;
    const id = str(r.joint_id);
    if (!id || id === driver || !Array.isArray(r.values) || r.values.length < 2 || r.values.length > MAX_SAMPLES) continue;
    const values = r.values.map(value);
    if (values.some((v) => v === null)) continue;
    out.push({ joint_id: id, values: values as (number | number[])[] });
  }
  return out;
}

/** An `assembly_animate` event → steps and a loop count, or null when it names no playable step. */
export function parseAnimateSpec(ev: MisesEvent): AnimateSpec | null {
  if (ev.type !== "assembly_animate") return null;
  const d = ev.data;
  const steps = (Array.isArray(d.sequence) ? d.sequence.slice(0, MAX_STEPS).map(step) : [step(d)])
    .filter((s): s is AnimateStep => s !== null);
  if (steps.length === 0) return null;
  const loops = typeof d.loops === "number" && d.loops >= 1 ? Math.min(MAX_LOOPS, Math.floor(d.loops)) : d.loop === true ? MAX_LOOPS : 1;
  return { project_id: str(d.project_id), steps, loops };
}

/** The motion finished by itself: the chip goes. */
function ended(started: ActiveAnimation): () => void {
  return () => { if (agentAnimation.peek()?.spec === started.spec) agentAnimation.value = null; };
}

export function recordAssemblyAnimation(ev: MisesEvent): void {
  if ((ev.type === "run_finished" || ev.type === "run_deleted") && ev.run_id && agentAnimation.value?.runId === ev.run_id) {
    stopAgentAnimation();
    return;
  }
  const spec = parseAnimateSpec(ev);
  if (!spec) return;
  // another build's motion: not this screen's (it would move the wrong assembly)
  const shown = activeBuildId.peek();
  if (spec.project_id && shown && spec.project_id !== shown) return;
  const active: ActiveAnimation = { spec, runId: ev.run_id, startedAt: Date.now() / 1000, playing: false };
  agentAnimation.value = active;
  // a repeated event replaces the running playback (never toggles it off)
  const playing = player?.play(spec, ended(active)) ?? false;
  if (playing && agentAnimation.peek()?.spec === spec) agentAnimation.value = { ...active, playing: true };
}

/** The chip's Watch: bring the build's assembly on screen and play the motion there. */
export async function watchAgentAnimation(): Promise<void> {
  const cur = agentAnimation.peek();
  if (!cur || !player) return;
  const ok = await player.watch(cur.spec, ended(cur));
  const now = agentAnimation.peek();
  if (ok && now && now.spec === cur.spec) agentAnimation.value = { ...now, playing: true };
}

/** Stop the active animation: clear the chip and put the joints back. */
export function stopAgentAnimation(): void {
  if (agentAnimation.value === null) return;
  agentAnimation.value = null;
  player?.stop();
}

/** The View tab's Play on a saved motion: show it on the build's assembly (switching to it). */
export async function playAnimation(spec: AnimateSpec, runId: string | null): Promise<boolean> {
  if (!player) return false;
  const active: ActiveAnimation = { spec, runId, startedAt: Date.now() / 1000, playing: false };
  agentAnimation.value = active;
  const ok = await player.watch(spec, ended(active));
  const now = agentAnimation.peek();
  if (ok && now && now.spec === spec) agentAnimation.value = { ...now, playing: true };
  return ok;
}

export function resetAssemblyAnimation(): void { agentAnimation.value = null; }

/** Subscribe to the event stream (default onCoreEvent). Call once at boot; returns an unsubscribe. */
export function startAssemblyAnimation(subscribe: (cb: (e: MisesEvent) => void) => () => void = onCoreEvent): () => void {
  return subscribe(recordAssemblyAnimation);
}
