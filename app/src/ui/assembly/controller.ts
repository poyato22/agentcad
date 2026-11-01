// The build view's state (Plan 9: the assembly IS the Design page): the build's assembly as the
// core has it, the pose on screen, the selection, interference, and playback of an agent's
// animation. One controller per app (module level), like the Studies page.
//
// Joints, placement and checks are agent-driven (core tools); the view has no editing of its own
// (Plan 9 D5/D6 removed the panel and the gizmo). Agent edits arrive as `assembly_changed`: the
// controller re-reads the assembly and tweens every part from where it is to the new pose (blue
// edges while it moves). An agent's `assembly_animate` plays locally with the core's own solve
// (kinematics.ts) and puts every joint back afterwards, writing nothing. An event that lands while
// an animation plays waits until it ends.
import { activeBuildId } from "../../state/builds";
import { batch, computed, signal, type ReadonlySignal, type Signal } from "@preact/signals";
import { Matrix4, Quaternion, Vector3 } from "three";
import type { Assembly, AssemblyPart, Instance, Interference, Joint, JointValue, Mat4 } from "../../api/assembly";
import { CoreError, type CoreClient } from "../../api/client";
import type { MisesEvent } from "../../api/types";
import { jointWorldFrame, mobility, samePose, solve, type FaceResolver, type Mobility, type SolveResult, type Vec3 } from "./kinematics";
import type { ExplodeLink } from "../viewer/scene";

export type AssemblyClient = Pick<CoreClient, "projects" | "activeProjectId" | "assemblyParts" | "assembly" | "interference">;

export interface InterferenceState {
  status: "idle" | "checking" | "done" | "error";
  items: readonly Interference[];
  /** Instance ids named in `items` (the core names instances; names are unique in the UI). */
  ids: ReadonlySet<string>;
  error: string | null;
}

export interface Clock {
  now(): number;
  frame(cb: () => void): () => void;
  later(cb: () => void, ms: number): () => void;
}

const defaultClock: Clock = {
  now: () => (typeof performance !== "undefined" ? performance.now() : Date.now()),
  frame: (cb) => {
    if (typeof requestAnimationFrame === "function") { const id = requestAnimationFrame(() => cb()); return () => cancelAnimationFrame(id); }
    const id = setTimeout(cb, 16);
    return () => clearTimeout(id);
  },
  later: (cb, ms) => { const id = setTimeout(cb, ms); return () => clearTimeout(id); },
};

/** How long an agent's change takes to glide in. */
export const TWEEN_MS = 420;
/** Interference is checked this long after the assembly stops moving. */
export const INTERFERENCE_SETTLE_MS = 450;

const IDLE_INTERFERENCE: InterferenceState = { status: "idle", items: [], ids: new Set(), error: null };

const messageOf = (e: unknown): string => (e instanceof CoreError ? (e.hint ? `${e.message} (${e.hint})` : e.message) : e instanceof Error ? e.message : String(e));

function interp(a: Mat4, b: Mat4, t: number): Mat4 {
  const ma = new Matrix4().set(...(a.flat() as Parameters<Matrix4["set"]>));
  const mb = new Matrix4().set(...(b.flat() as Parameters<Matrix4["set"]>));
  const pa = new Vector3(); const qa = new Quaternion(); const sa = new Vector3();
  const pb = new Vector3(); const qb = new Quaternion(); const sb = new Vector3();
  ma.decompose(pa, qa, sa); mb.decompose(pb, qb, sb);
  const m = new Matrix4().compose(pa.lerp(pb, t), qa.slerp(qb, t), new Vector3(1, 1, 1));
  const e = m.elements; // column-major
  return [0, 1, 2, 3].map((r) => [0, 1, 2, 3].map((c) => e[c * 4 + r]));
}

const ease = (t: number) => 0.5 - 0.5 * Math.cos(Math.PI * Math.min(1, Math.max(0, t)));

/** One joint step of a motion: from → to over duration_s; `follow` (G4): the joints that move
 *  with it (gear trains, closed linkages) as the core solved them at evenly spaced fractions. */
export interface MotionStep { joint_id: string; from: JointValue; to: JointValue; duration_s: number; follow?: readonly { joint_id: string; values: readonly JointValue[] }[] }

/** A follower's value at fraction `f` of its step (piecewise linear between the samples). */
function sampleAt(values: readonly JointValue[], f: number): JointValue {
  const x = Math.min(1, Math.max(0, f)) * (values.length - 1);
  const k = Math.min(values.length - 2, Math.floor(x));
  return lerpValue(values[k], values[k + 1], x - k);
}

/** A revolute step through whole turns (a crank, a motor) plays at constant speed: eased, a
 *  looping drive would stop dead at every turn. */
function wholeTurns(s: MotionStep, kind: string | undefined): boolean {
  if (kind !== "revolute" || Array.isArray(s.from) || Array.isArray(s.to)) return false;
  const span = Math.abs(s.to - s.from);
  return span >= 359.999 && Math.abs(span / 360 - Math.round(span / 360)) < 1e-6;
}

/** A presented animation being shown (G-D): `t` seconds into a pass of `duration`. */
export interface MotionState { t: number; duration: number; playing: boolean; speed: number; loop: boolean }

export class AssemblyController {
  readonly projectId: Signal<string | null> = signal(null);
  readonly data: Signal<Assembly | null> = signal(null);
  readonly parts: Signal<readonly AssemblyPart[]> = signal([]);
  readonly loading: Signal<boolean> = signal(false);
  readonly error: Signal<string | null> = signal(null);
  /** Current value of every joint as shown (moves while an animation plays). */
  readonly values: Signal<Readonly<Record<string, JointValue>>> = signal({});
  readonly selected: Signal<string | null> = signal(null);
  readonly interference: Signal<InterferenceState> = signal(IDLE_INTERFERENCE);
  readonly animating: Signal<string | null> = signal(null);
  /** An agent's change is gliding in. */
  readonly arriving: Signal<boolean> = signal(false);
  /** The last local solve (who places whom). */
  readonly solved: ReadonlySignal<SolveResult | null>;
  readonly busy: ReadonlySignal<boolean>;

  private readonly client: () => AssemblyClient;
  private readonly clock: Clock;
  private readonly poseListeners = new Set<(pose: Readonly<Record<string, Mat4>>) => void>();
  private pose: Record<string, Mat4> = {};
  /** Joint values an animation is showing (over the core's). */
  private moving = new Map<string, JointValue>();
  private dirty = false;
  private loadSeq = 0;
  private interferenceSeq = 0;
  private cancelTween: (() => void) | null = null;
  private cancelSweep: (() => void) | null = null;
  private cancelSettle: (() => void) | null = null;
  private cancelRefresh: (() => void) | null = null;

  constructor(client: () => AssemblyClient, opts: { clock?: Clock } = {}) {
    this.client = client;
    this.clock = opts.clock ?? defaultClock;
    this.solved = computed(() => {
      const d = this.data.value;
      return d ? solve(d, this.resolver()) : null;
    });
    this.busy = computed(() => this.animating.value !== null);
  }

  // ---- reading

  /** Face id → centre/normal from the build's part metrics (for agent joints that name a face). */
  resolver(): FaceResolver {
    const parts = this.parts.value;
    return (partId, faceId) => {
      const f = parts.find((p) => p.part_id === partId)?.metrics?.face_list?.find((x) => x.id === faceId);
      return f && f.normal ? [f.center as Vec3, f.normal as Vec3] : null;
    };
  }

  instance(id: string | null): Instance | null {
    return id === null ? null : this.data.value?.instances.find((i) => i.id === id) ?? null;
  }

  joint(id: string): Joint | null { return this.data.value?.joints.find((j) => j.id === id) ?? null; }

  part(partId: string): AssemblyPart | null { return this.parts.value.find((p) => p.part_id === partId) ?? null; }

  mobilityOf(id: string): Mobility | null {
    const inst = this.instance(id);
    const s = this.solved.value;
    return inst && s ? mobility(inst, s.placedBy, s.blocked) : null;
  }

  currentPose(): Readonly<Record<string, Mat4>> { return this.pose; }

  /** Plan 10 E4: the joint tree for the explode, at `pose`: each placed instance → the instance
   *  that places it, with that joint's world axis and origin (a ball joint has no axis). */
  explodeLinks(pose: Readonly<Record<string, Mat4>> = this.pose): Map<string, ExplodeLink> {
    const d = this.data.peek(); const s = this.solved.peek();
    const out = new Map<string, ExplodeLink>();
    if (!d || !s) return out;
    const r = (v: number) => Math.round(v * 1e4) / 1e4;
    for (const inst of d.instances) {
      const p = s.placedBy[inst.id];
      const j = p ? this.joint(p.joint) : null;
      if (!p || !j) continue;
      const parent = p.from === "a" ? j.a.instance_id : j.b.instance_id;
      const f = jointWorldFrame(d, pose as Record<string, Mat4>, j, p.from, this.resolver());
      out.set(inst.id, {
        parent,
        axis: f && j.kind !== "ball" ? [r(f[0][2]), r(f[1][2]), r(f[2][2])] : null,
        origin: f ? [r(f[0][3]), r(f[1][3]), r(f[2][3])] : null,
      });
    }
    return out;
  }

  /** The scene listens here (every frame of a glide or an animation); returns unsubscribe. */
  onPose(cb: (pose: Readonly<Record<string, Mat4>>) => void): () => void {
    this.poseListeners.add(cb);
    cb(this.pose);
    return () => { this.poseListeners.delete(cb); };
  }

  // ---- loading

  /** Finds the build (profile's active build if live, else Default, else the oldest) and loads it. */
  async open(projectId?: string | null): Promise<void> {
    const seq = ++this.loadSeq;
    this.loading.value = true;
    this.error.value = null;
    try {
      // A3's build signal is the active build; findProject only before the builds have loaded
      const id = projectId ?? this.projectId.peek() ?? activeBuildId.peek() ?? await this.findProject();
      if (seq !== this.loadSeq) return;
      if (id === null) { this.error.value = "No build to assemble. Create a build first."; return; }
      if (id !== this.projectId.peek()) {
        batch(() => {
          this.projectId.value = id;
          this.data.value = null;
          this.selected.value = null;
          this.interference.value = IDLE_INTERFERENCE;
        });
      }
      const [parts, asm] = await Promise.all([this.client().assemblyParts(id), this.client().assembly(id)]);
      if (seq !== this.loadSeq) return;
      this.parts.value = parts;
      this.accept(asm, false);
      this.scheduleInterference(0);
    } catch (e) {
      if (seq === this.loadSeq) this.error.value = messageOf(e);
    } finally {
      if (seq === this.loadSeq) this.loading.value = false;
    }
  }

  private async findProject(): Promise<string | null> {
    const [live, active] = await Promise.all([this.client().projects(), this.client().activeProjectId().catch(() => null)]);
    if (active && live.some((p) => p.id === active)) return active;
    const def = live.find((p) => p.name === "Default");
    if (def) return def.id;
    return [...live].sort((a, b) => a.created_at - b.created_at)[0]?.id ?? null;
  }

  /** Part geometry changed (a rebuild): re-read the build's parts. */
  async reloadParts(): Promise<void> {
    const id = this.projectId.peek();
    if (!id) return;
    try { this.parts.value = await this.client().assemblyParts(id); } catch (e) { this.error.value = messageOf(e); }
  }

  /** Re-read the assembly; when `glide`, parts move to the new pose over TWEEN_MS. */
  async refresh(glide = true): Promise<void> {
    const id = this.projectId.peek();
    if (!id) return;
    const seq = ++this.loadSeq;
    try {
      const asm = await this.client().assembly(id);
      if (seq !== this.loadSeq || id !== this.projectId.peek()) return;
      if (this.busy.peek()) { this.dirty = true; return; }
      this.accept(asm, glide);
      // an agent's new part placed itself: read the build's parts too (its look, its metrics)
      if (asm.instances.some((i) => this.part(i.part_id) === null)) void this.reloadParts();
    } catch (e) {
      if (seq === this.loadSeq) this.error.value = messageOf(e);
    }
  }

  onEvent(ev: MisesEvent): void {
    if (ev.type !== "assembly_changed" || ev.data["project_id"] !== this.projectId.peek()) return;
    if (this.busy.peek()) { this.dirty = true; return; }
    this.cancelRefresh?.();
    this.cancelRefresh = this.clock.later(() => { this.cancelRefresh = null; void this.refresh(true); }, 60);
  }

  private accept(asm: Assembly, glide: boolean): void {
    const prev = this.data.peek();
    const values: Record<string, JointValue> = {};
    for (const j of asm.joints) values[j.id] = j.value;
    const removed = this.selected.peek() !== null && !asm.instances.some((i) => i.id === this.selected.peek());
    batch(() => {
      this.data.value = asm;
      this.values.value = values;
      if (removed) this.selected.value = null;
    });
    // GET /assembly carries the solved world transforms: show exactly those
    const target: Record<string, Mat4> = {};
    for (const i of asm.instances) target[i.id] = i.transform;
    const moved = prev !== null && asm.instances.some((i) => this.pose[i.id] && !samePose(this.pose[i.id], i.transform));
    if (glide && moved) this.glideTo(target);
    else this.setPose(target);
    if (prev !== null && changedShape(prev, asm)) this.scheduleInterference();
  }

  // ---- pose

  private setPose(p: Record<string, Mat4>): void {
    this.pose = p;
    for (const cb of [...this.poseListeners]) cb(p);
  }

  private glideTo(target: Record<string, Mat4>): void {
    this.cancelTween?.();
    if (typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
      this.setPose(target); // reduced motion: the change lands without gliding
      this.scheduleInterference();
      return;
    }
    const from = { ...this.pose };
    const t0 = this.clock.now();
    this.arriving.value = true;
    const step = () => {
      const t = ease((this.clock.now() - t0) / TWEEN_MS);
      const p: Record<string, Mat4> = {};
      for (const [id, m] of Object.entries(target)) p[id] = from[id] ? interp(from[id], m, t) : m;
      this.setPose(p);
      if (t >= 1) { this.cancelTween = null; this.arriving.value = false; this.setPose(target); this.scheduleInterference(); return; }
      this.cancelTween = this.clock.frame(step);
    };
    step();
  }

  /** Local solve with the animation's joint values over the core's. */
  private predict(): void {
    const d = this.data.peek();
    if (!d) return;
    const joints = d.joints.map((j) => ({ ...j, value: this.moving.get(j.id) ?? this.values.peek()[j.id] ?? j.value }));
    const r = solve({ instances: d.instances, joints }, this.resolver());
    this.cancelTween?.();
    this.cancelTween = null;
    this.arriving.value = false; // the animation took over from an agent's glide
    this.setPose(r.transforms);
  }

  /** Show exactly the core's last answer (no local prediction left over), when nothing is moving. */
  private showCore(): void {
    const d = this.data.peek();
    if (!d || this.moving.size > 0 || this.animating.peek() !== null) return;
    const p: Record<string, Mat4> = {};
    for (const i of d.instances) p[i.id] = i.transform;
    this.setPose(p);
  }

  /** The motion is over: catch up on events that came in during it, then check interference. */
  private settle(): void {
    if (this.dirty) { this.dirty = false; void this.refresh(true); }
    this.scheduleInterference();
  }

  // ---- animation

  /** play(): every joint it touched and the value to put back, and who hears a natural end. */
  private playRestore: Map<string, JointValue> | null = null;
  private playEnd: (() => void) | null = null;

  /**
   * An agent's animation (Plan 7 R19, `assembly_animate`): each step moves its joint from → to
   * over duration_s, one after another, the whole sequence `loops` times; then every joint it
   * touched goes back to where it was (as on stopAnimate). A preview: nothing is written. Steps
   * naming a joint this assembly doesn't have are skipped; false when none is left. A new play
   * replaces the one running (never toggles it off).
   */
  play(steps: readonly MotionStep[], opts: { loops?: number; onEnd?: () => void } = {}): boolean {
    const usable = this.beginMotion(steps);
    if (!usable) return false;
    const loops = Math.max(1, Math.floor(opts.loops ?? 1));
    this.playEnd = opts.onEnd ?? null;
    const durs = usable.map((s) => Math.max(0.05, s.duration_s) * 1000);
    const cycle = durs.reduce((a, b) => a + b, 0);
    const t0 = this.clock.now();
    this.animating.value = usable[0].joint_id;
    const frame = () => {
      const el = this.clock.now() - t0;
      if (el >= cycle * loops) { this.finishPlay(); return; }
      this.showMotionAt(usable, durs, el % cycle);
      this.cancelSweep = this.clock.frame(frame);
    };
    frame();
    return true;
  }

  /** The joints `steps` touch and the value each goes back to; the steps this assembly can play. */
  private beginMotion(steps: readonly MotionStep[]): MotionStep[] | null {
    const usable = steps.filter((s) => this.joint(s.joint_id) !== null);
    if (usable.length === 0) return null;
    this.stopAnimate();
    const restore = new Map<string, JointValue>();
    for (const s of usable) {
      for (const id of [s.joint_id, ...(s.follow ?? []).map((f) => f.joint_id)]) {
        const j = this.joint(id);
        if (j && !restore.has(id)) restore.set(id, this.values.peek()[id] ?? j.value);
      }
    }
    this.playRestore = restore;
    return usable;
  }

  /** The pose `u` ms into one pass of `steps`: steps before the current one at their end, the
   *  current one on its way (eased), later ones at their start. */
  private motionValuesAt(usable: readonly MotionStep[], durs: readonly number[], u: number): { pose: Map<string, JointValue>; i: number } {
    let i = 0;
    while (i < usable.length - 1 && u >= durs[i]) { u -= durs[i]; i++; }
    const pose = new Map<string, JointValue>();
    usable.forEach((s, k) => {
      const j = this.joint(s.joint_id) as Joint;
      // how far along its step: done, on its way (eased; whole turns at constant speed), not yet
      const f = k < i ? 1 : k > i ? 0 : wholeTurns(s, j.kind) ? Math.min(1, u / durs[i]) : ease(Math.min(1, u / durs[i]));
      if (k > i && pose.has(s.joint_id)) return;
      pose.set(s.joint_id, clampJoint(lerpValue(s.from, s.to, f), j.limits));
      // G4: the joints it carries (gear trains, linkages) at the same fraction
      for (const fw of s.follow ?? []) {
        const fj = this.joint(fw.joint_id);
        if (fj && fw.values.length >= 2 && !(k > i && pose.has(fw.joint_id))) pose.set(fw.joint_id, clampJoint(sampleAt(fw.values, f), fj.limits));
      }
    });
    return { pose, i };
  }

  /** The poses the open motion passes through (`n` + 1 samples, ends included), for framing it
   *  (fix round 1: the full window frames what the motion sweeps, not every joint's full reach). */
  motionPoses(n = 24): Record<string, Mat4>[] {
    const d = this.data.peek();
    const total = this.motionDurs.reduce((a, b) => a + b, 0);
    if (!d || this.motion.peek() === null || total <= 0) return [];
    const out: Record<string, Mat4>[] = [];
    for (let k = 0; k <= n; k++) {
      const { pose } = this.motionValuesAt(this.motionSteps, this.motionDurs, (k / n) * total);
      const joints = d.joints.map((j) => ({ ...j, value: pose.get(j.id) ?? this.values.peek()[j.id] ?? j.value }));
      out.push(solve({ instances: d.instances, joints }, this.resolver()).transforms);
    }
    return out;
  }

  private showMotionAt(usable: readonly MotionStep[], durs: readonly number[], u: number): void {
    const { pose, i } = this.motionValuesAt(usable, durs, u);
    const next = { ...this.values.peek() };
    for (const [id, v] of pose) { this.moving.set(id, v); next[id] = v; }
    batch(() => {
      if (this.animating.peek() !== usable[i].joint_id) this.animating.value = usable[i].joint_id;
      this.values.value = next;
    });
    this.predict();
  }

  // ---- a presented animation (Plan 9 G-D): full window, scrubbable

  /** The presented animation on screen: where it is (s), whether it plays, its speed and loop. */
  readonly motion: Signal<MotionState | null> = signal(null);
  private motionSteps: MotionStep[] = [];
  private motionDurs: number[] = [];
  private motionLast = 0;

  /**
   * Show `steps` as a scrubbable motion from its start: it plays at once (`autoplay`, default)
   * at 1x, wrapping when `loop`, else holding its end. Nothing is written; closeMotion (or Esc,
   * or an agent's live play) puts every joint back. False when no step names a joint here.
   */
  openMotion(steps: readonly MotionStep[], opts: { loop?: boolean; autoplay?: boolean } = {}): boolean {
    const usable = this.beginMotion(steps);
    if (!usable) return false;
    this.motionSteps = usable;
    this.motionDurs = usable.map((s) => Math.max(0.05, s.duration_s) * 1000);
    const duration = this.motionDurs.reduce((a, b) => a + b, 0) / 1000;
    this.animating.value = usable[0].joint_id;
    this.motion.value = { t: 0, duration, playing: opts.autoplay ?? true, speed: 1, loop: opts.loop ?? false };
    this.showMotionAt(usable, this.motionDurs, 0);
    if (this.motion.peek()?.playing) this.runMotion();
    return true;
  }

  /** Play / pause, speed, loop. Playing from the end starts over. */
  setMotion(patch: Partial<Pick<MotionState, "playing" | "speed" | "loop">>): void {
    const m = this.motion.peek();
    if (!m) return;
    const next = { ...m, ...patch, speed: Math.min(4, Math.max(0.1, patch.speed ?? m.speed)) };
    if (patch.playing && !m.playing && m.t >= m.duration) next.t = 0;
    this.motion.value = next;
    if (next.t !== m.t) this.showMotionAt(this.motionSteps, this.motionDurs, next.t * 1000);
    if (next.playing && !m.playing) this.runMotion();
    else if (!next.playing) { this.cancelSweep?.(); this.cancelSweep = null; }
  }

  /** Scrub: show the motion `t` seconds in (clamped), paused there. */
  seekMotion(t: number): void {
    const m = this.motion.peek();
    if (!m) return;
    this.cancelSweep?.();
    this.cancelSweep = null;
    const at = Math.min(m.duration, Math.max(0, Number.isFinite(t) ? t : 0));
    this.motion.value = { ...m, t: at, playing: false };
    this.showMotionAt(this.motionSteps, this.motionDurs, at * 1000);
  }

  /** Close the motion: every joint goes back. */
  closeMotion(): void {
    if (this.motion.peek() === null) return;
    this.stopAnimate();
  }

  private runMotion(): void {
    this.cancelSweep?.();
    this.motionLast = this.clock.now();
    const frame = () => {
      const m = this.motion.peek();
      if (!m || !m.playing) { this.cancelSweep = null; return; }
      const now = this.clock.now();
      let t = m.t + ((now - this.motionLast) / 1000) * m.speed;
      this.motionLast = now;
      let playing = true;
      if (t >= m.duration) {
        if (m.loop && m.duration > 0) t %= m.duration;
        else { t = m.duration; playing = false; }
      }
      this.motion.value = { ...m, t, playing };
      this.showMotionAt(this.motionSteps, this.motionDurs, t * 1000);
      this.cancelSweep = playing ? this.clock.frame(frame) : null;
    };
    this.cancelSweep = this.clock.frame(frame);
  }

  private finishPlay(): void {
    const end = this.playEnd;
    this.playEnd = null;
    this.stopAnimate();
    end?.();
  }

  stopAnimate(): void {
    this.cancelSweep?.();
    this.cancelSweep = null;
    const pr = this.playRestore;
    this.playRestore = null;
    this.playEnd = null;
    this.motion.value = null;
    if (this.animating.peek() === null) return;
    this.animating.value = null;
    if (pr) {
      const next = { ...this.values.peek() };
      for (const [id, v] of pr) { this.moving.delete(id); next[id] = v; }
      this.values.value = next;
      this.predict();
    }
    this.showCore();
    this.settle();
  }

  // ---- selection

  /** A click on a part in the view (null: empty space). */
  select(id: string | null): void { this.selected.value = id; }

  // ---- interference

  private scheduleInterference(ms = INTERFERENCE_SETTLE_MS): void {
    this.cancelSettle?.();
    if (!this.projectId.peek()) return;
    this.cancelSettle = this.clock.later(() => { this.cancelSettle = null; void this.checkInterference(); }, ms);
  }

  async checkInterference(): Promise<void> {
    const pid = this.projectId.peek();
    const d = this.data.peek();
    if (!pid || !d) return;
    if (this.busy.peek()) { this.scheduleInterference(); return; }
    if (d.instances.length < 2) { this.interference.value = { ...IDLE_INTERFERENCE, status: "done" }; return; }
    const seq = ++this.interferenceSeq;
    this.interference.value = { ...this.interference.peek(), status: "checking", error: null };
    try {
      const items = await this.client().interference(pid);
      if (seq !== this.interferenceSeq) return;
      const byName = new Map<string, string[]>();
      for (const i of this.data.peek()?.instances ?? []) byName.set(i.name || i.id, [...(byName.get(i.name || i.id) ?? []), i.id]);
      const ids = new Set<string>();
      for (const it of items) for (const n of [it.a, it.b]) for (const id of byName.get(n) ?? [n]) ids.add(id);
      this.interference.value = { status: "done", items, ids, error: null };
    } catch (e) {
      if (seq !== this.interferenceSeq) return;
      this.interference.value = { ...IDLE_INTERFERENCE, status: "error", error: messageOf(e) };
    }
  }

  // ---- teardown

  /** Leaving the build view: stop motion, then put the core's pose back. The data stays for a
   *  quick return. */
  pause(): void {
    this.stopAnimate();
    this.cancelTween?.(); this.cancelTween = null;
    this.cancelSettle?.(); this.cancelSettle = null;
    this.cancelRefresh?.(); this.cancelRefresh = null;
    this.arriving.value = false;
    this.moving.clear();
    const open = this.dirty;
    this.dirty = false;
    if (open && this.data.peek()) void this.refresh(false);
  }

  /** Tests: forget everything. */
  reset(): void {
    this.pause();
    this.loadSeq++;
    this.interferenceSeq++;
    this.moving.clear();
    this.dirty = false;
    this.pose = {};
    batch(() => {
      this.projectId.value = null; this.data.value = null; this.parts.value = []; this.loading.value = false; this.error.value = null;
      this.values.value = {}; this.selected.value = null;
      this.interference.value = IDLE_INTERFERENCE; this.animating.value = null;
    });
  }
}

/** from + (to − from)·f, per component (a scalar meets a list as its first component). */
export function lerpValue(from: JointValue, to: JointValue, f: number): JointValue {
  if (!Array.isArray(from) && !Array.isArray(to)) return from + (to - from) * f;
  const a = Array.isArray(from) ? from : [from];
  const b = Array.isArray(to) ? to : [to];
  return Array.from({ length: Math.max(a.length, b.length) }, (_, i) => {
    const x = a[i] ?? a[a.length - 1] ?? 0;
    const y = b[i] ?? b[b.length - 1] ?? 0;
    return x + (y - x) * f;
  });
}

function clampJoint(v: JointValue, limits: readonly number[] | null): JointValue {
  if (!limits) return v;
  const c = (x: number) => Math.min(Math.max(x, limits[0]), limits[1]);
  return Array.isArray(v) ? v.map(c) : c(v);
}

/** Whether anything that changes interference changed (instances, joints, values). */
function changedShape(a: Assembly, b: Assembly): boolean {
  if (a.instances.length !== b.instances.length || a.joints.length !== b.joints.length) return true;
  return a.instances.some((i, k) => i.id !== b.instances[k].id || i.part_id !== b.instances[k].part_id || !samePose(i.transform, b.instances[k].transform));
}
