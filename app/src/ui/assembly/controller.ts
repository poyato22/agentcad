// Assembly mode state: the build's assembly as the core has it, the pose on screen, and every
// interaction that moves it. One controller per app (module level), like the Studies page.
//
// Feel first: a slider, a drag or Animate poses the parts locally at frame rate with the core's own
// solve (kinematics.ts); the core hears the value through POST /assembly/drive, at most one call in
// flight and only the newest value queued, and its answer (the solved transforms) replaces the
// local pose when the gesture settles. Animate is a preview: it sweeps locally and puts the joint
// back where it was, writing nothing.
//
// Agent edits arrive as `assembly_changed`: the controller re-reads the assembly and tweens every
// part from where it is to the new pose (blue edges while it moves). An event that lands during a
// gesture waits until the gesture ends. Our own mutations also emit the event; the re-read then
// finds nothing changed and nothing moves.
import { activeBuildId } from "../../state/builds";
import { batch, computed, signal, type ReadonlySignal, type Signal } from "@preact/signals";
import { Matrix4, Quaternion, Vector3 } from "three";
import type {
  Assembly, AssemblyPart, Instance, Interference, Joint, JointKind, JointValue, Mat4, NewJointBody,
} from "../../api/assembly";
import { CoreError, type CoreClient } from "../../api/client";
import type { MisesEvent } from "../../api/types";
import { isAutoNumbered } from "../../lib/names";
import { mobility, samePose, solve, type FaceResolver, type Mobility, type SolveResult, type Vec3 } from "./kinematics";
import { draftProblem, emptyDraft, defaultLimits, jointBody, type FaceOption, type JointDraft } from "./pick";

export type AssemblyClient = Pick<CoreClient,
  "projects" | "activeProjectId" | "assemblyParts" | "assembly" | "addInstance" | "patchInstance" | "deleteInstance"
  | "addJoint" | "patchJoint" | "deleteJoint" | "drive" | "interference">;

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

/** How long an agent's change takes to glide in, and one Animate sweep (there and back). */
export const TWEEN_MS = 420;
export const SWEEP_MS = 2400;
/** Interference is checked this long after the assembly stops moving. */
export const INTERFERENCE_SETTLE_MS = 450;
/** Slider range for a joint with no limits. */
export const UNLIMITED: Record<"angle" | "distance", [number, number]> = { angle: [-180, 180], distance: [-100, 100] };

const IDLE_INTERFERENCE: InterferenceState = { status: "idle", items: [], ids: new Set(), error: null };

const messageOf = (e: unknown): string => (e instanceof CoreError ? (e.hint ? `${e.message} (${e.hint})` : e.message) : e instanceof Error ? e.message : String(e));

/** One editable component of a joint value: what the slider shows. */
export interface JointAxis { index: number; label: string; unit: "°" | "mm"; kind: "angle" | "distance" }

export function jointAxes(kind: JointKind): JointAxis[] {
  switch (kind) {
    case "fixed": return [];
    case "revolute": return [{ index: 0, label: "Turn", unit: "°", kind: "angle" }];
    case "slider": return [{ index: 0, label: "Slide", unit: "mm", kind: "distance" }];
    case "cylindrical": return [{ index: 0, label: "Turn", unit: "°", kind: "angle" }, { index: 1, label: "Slide", unit: "mm", kind: "distance" }];
    case "ball": return [{ index: 0, label: "About X", unit: "°", kind: "angle" }, { index: 1, label: "About Y", unit: "°", kind: "angle" }, { index: 2, label: "About Z", unit: "°", kind: "angle" }];
  }
}

export function valueAt(v: JointValue, i: number): number {
  return Array.isArray(v) ? (v[i] ?? 0) : i === 0 ? v : 0;
}

export function withComponent(kind: JointKind, v: JointValue, i: number, x: number): JointValue {
  const n = jointAxes(kind).length;
  if (n <= 1) return x;
  const out = Array.from({ length: n }, (_, k) => valueAt(v, k));
  out[i] = x;
  return out;
}

/** The slider range of one component: the joint's limits, else a generous default. */
export function axisRange(j: Pick<Joint, "limits">, axis: JointAxis): [number, number] {
  return j.limits ? [j.limits[0], j.limits[1]] : UNLIMITED[axis.kind];
}

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

export class AssemblyController {
  readonly projectId: Signal<string | null> = signal(null);
  readonly data: Signal<Assembly | null> = signal(null);
  readonly parts: Signal<readonly AssemblyPart[]> = signal([]);
  readonly loading: Signal<boolean> = signal(false);
  readonly error: Signal<string | null> = signal(null);
  /** Current value of every joint as shown (moves with the slider before the core answers). */
  readonly values: Signal<Readonly<Record<string, JointValue>>> = signal({});
  readonly selected: Signal<string | null> = signal(null);
  readonly draft: Signal<JointDraft | null> = signal(null);
  readonly draftError: Signal<string | null> = signal(null);
  readonly interference: Signal<InterferenceState> = signal(IDLE_INTERFERENCE);
  readonly animating: Signal<string | null> = signal(null);
  /** An agent's change is gliding in. */
  readonly arriving: Signal<boolean> = signal(false);
  readonly snap: Signal<boolean> = signal(true);
  readonly gizmo: Signal<"move" | "turn"> = signal("move");
  /** The last local solve (who places whom), for the gizmo and the panel. */
  readonly solved: ReadonlySignal<SolveResult | null>;
  readonly busy: ReadonlySignal<boolean>;

  private readonly client: () => AssemblyClient;
  private readonly clock: Clock;
  private readonly poseListeners = new Set<(pose: Readonly<Record<string, Mat4>>) => void>();
  private pose: Record<string, Mat4> = {};
  /** Instance transforms being dragged (stored transform overrides) and joint values being moved. */
  private dragging = new Map<string, Mat4>();
  private moving = new Map<string, JointValue>();
  private driveInflight = false;
  /** The newest value waiting to be sent, per joint (insertion order = send order). */
  private drivePending = new Map<string, JointValue>();
  private gesture = signal(0);
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
    this.busy = computed(() => this.gesture.value > 0 || this.animating.value !== null);
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

  /** The scene listens here (every frame of a gesture); returns unsubscribe. */
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
          this.draft.value = null;
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
    } catch (e) {
      if (seq === this.loadSeq) this.error.value = messageOf(e);
    }
  }

  onEvent(ev: MisesEvent): void {
    if (ev.type !== "assembly_changed" || ev.data["project_id"] !== this.projectId.peek()) return;
    if (this.busy.peek() || this.driveInflight) { this.dirty = true; return; }
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

  /** Local solve with the gesture's overrides applied (dragged instances, moving joints, a draft). */
  private predict(extra?: NewJointBody): void {
    const d = this.data.peek();
    if (!d) return;
    const instances = d.instances.map((i) => {
      const drag = this.dragging.get(i.id);
      // a dragged anchor, else the core's solved pose (anchors' solved = stored)
      return { ...i, transform: drag ?? this.storedOf(i) };
    });
    const joints: Pick<Joint, "id" | "kind" | "a" | "b" | "value" | "limits">[] = d.joints.map((j) => ({ ...j, value: this.moving.get(j.id) ?? this.values.peek()[j.id] ?? j.value }));
    if (extra) joints.push({ id: "__draft", kind: extra.kind, a: extra.a, b: extra.b, value: extra.value ?? 0, limits: extra.limits ?? null });
    const r = solve({ instances, joints }, this.resolver());
    this.cancelTween?.();
    this.cancelTween = null;
    this.arriving.value = false; // the user took over from an agent's glide
    this.setPose(r.transforms);
  }

  /** Anchors (grounded, first of a free component, free bodies) keep their pose; the GET's solved
   *  transform is exactly that, and placed instances are re-derived by the solve anyway. */
  private storedOf(i: Instance): Mat4 { return i.transform; }

  private begin(): void { this.gesture.value++; this.cancelSettle?.(); }
  private end(): void {
    this.gesture.value = Math.max(0, this.gesture.value - 1);
    if (this.gesture.value === 0 && !this.driveInflight) { this.showCore(); this.settle(); }
  }

  /** Show exactly the core's last answer (no local prediction left over), when nothing is moving. */
  private showCore(): void {
    const d = this.data.peek();
    if (!d || this.gesture.peek() > 0 || this.dragging.size > 0 || this.moving.size > 0 || this.animating.peek() !== null || this.draft.peek()) return;
    const p: Record<string, Mat4> = {};
    for (const i of d.instances) p[i.id] = i.transform;
    this.setPose(p);
  }

  /** The gesture is over: catch up on events that came in during it, then check interference. */
  private settle(): void {
    if (this.dirty) { this.dirty = false; void this.refresh(true); }
    this.scheduleInterference();
  }

  // ---- joints: drive, animate

  /** A slider input / joint drag frame: pose now, tell the core (coalesced). */
  setJointValue(jointId: string, value: JointValue): void {
    const j = this.joint(jointId);
    if (!j) return;
    const clamped = clampJoint(value, j.limits);
    this.moving.set(jointId, clamped);
    this.values.value = { ...this.values.peek(), [jointId]: clamped };
    this.predict();
    this.queueDrive(jointId, clamped);
  }

  /** Slider pointer-down / joint-drag start and end (events wait; the core's answer lands after). */
  beginJointGesture(): void { this.begin(); }
  endJointGesture(): void { this.end(); }

  private queueDrive(jointId: string, value: JointValue): void {
    this.drivePending.delete(jointId); // re-queued at the back with its newest value
    this.drivePending.set(jointId, value);
    if (!this.driveInflight) void this.pumpDrives();
  }

  /** Send the queued values one call at a time (newest per joint). A joint's local value is
   *  dropped only once the core has answered its latest one, so the core stays the truth. */
  private async pumpDrives(): Promise<void> {
    const pid = this.projectId.peek();
    if (!pid || this.driveInflight) return;
    this.driveInflight = true;
    try {
      while (this.drivePending.size > 0) {
        const [jointId, value] = this.drivePending.entries().next().value as [string, JointValue];
        this.drivePending.delete(jointId);
        const r = await this.client().drive(pid, jointId, value);
        if (!this.drivePending.has(jointId) && this.animating.peek() !== jointId) this.moving.delete(jointId);
        this.applyDrive(jointId, r.applied, r.transforms);
      }
      this.driveInflight = false;
      if (this.gesture.peek() === 0) { this.showCore(); this.settle(); }
    } catch (e) {
      this.driveInflight = false;
      this.drivePending.clear();
      this.moving.clear();
      this.error.value = messageOf(e);
      void this.refresh(false);
    }
  }

  private applyDrive(jointId: string, applied: JointValue, transforms: Record<string, Mat4>): void {
    const d = this.data.peek();
    if (!d) return;
    const asm: Assembly = {
      ...d,
      instances: d.instances.map((i) => (transforms[i.id] ? { ...i, transform: transforms[i.id] } : i)),
      joints: d.joints.map((j) => (j.id === jointId ? { ...j, value: applied } : j)),
    };
    batch(() => {
      this.data.value = asm;
      if (!this.moving.has(jointId)) this.values.value = { ...this.values.peek(), [jointId]: applied };
    });
  }

  /** Sweep a joint lo → hi → back to where it was, locally (a preview; nothing is written). */
  animate(jointId: string): void {
    const j = this.joint(jointId);
    if (!j || jointAxes(j.kind).length === 0) return;
    if (this.animating.peek() === jointId) { this.stopAnimate(); return; }
    this.stopAnimate();
    const axis = jointAxes(j.kind)[0];
    const [lo, hi] = axisRange(j, axis);
    const start = this.values.peek()[jointId] ?? j.value;
    const s0 = valueAt(start, 0);
    this.animating.value = jointId;
    this.sweepRestore = { jointId, value: start };
    const t0 = this.clock.now();
    // there (s0 → hi), across (hi → lo), and back (lo → s0), timed by distance
    const legs: [number, number][] = [[s0, hi], [hi, lo], [lo, s0]];
    const total = legs.reduce((acc, [a, b]) => acc + Math.abs(b - a), 0) || 1;
    const step = () => {
      let u = Math.min(1, (this.clock.now() - t0) / SWEEP_MS) * total;
      let x = s0;
      for (const [a, b] of legs) {
        const span = Math.abs(b - a);
        if (u <= span) { x = a + (b - a) * ease(span === 0 ? 1 : u / span); break; }
        u -= span;
        x = b;
      }
      this.moving.set(jointId, withComponent(j.kind, start, 0, x));
      this.values.value = { ...this.values.peek(), [jointId]: this.moving.get(jointId) as JointValue };
      this.predict();
      if (this.clock.now() - t0 >= SWEEP_MS) { this.stopAnimate(); return; }
      this.cancelSweep = this.clock.frame(step);
    };
    step();
  }

  private sweepRestore: { jointId: string; value: JointValue } | null = null;

  stopAnimate(): void {
    this.cancelSweep?.();
    this.cancelSweep = null;
    const r = this.sweepRestore;
    this.sweepRestore = null;
    if (this.animating.peek() === null) return;
    this.animating.value = null;
    if (r) {
      this.moving.delete(r.jointId);
      this.values.value = { ...this.values.peek(), [r.jointId]: r.value };
      this.predict();
    }
    this.settle();
  }

  async setLimits(jointId: string, limits: [number, number]): Promise<void> {
    const pid = this.projectId.peek();
    if (!pid) return;
    if (!(limits[0] <= limits[1])) { this.error.value = "The lower limit is above the upper one."; return; }
    try { await this.client().patchJoint(pid, jointId, { limits }); await this.refresh(true); } catch (e) { this.error.value = messageOf(e); }
  }

  async removeJoint(jointId: string): Promise<void> {
    const pid = this.projectId.peek();
    if (!pid) return;
    if (this.animating.peek() === jointId) this.stopAnimate();
    try { await this.client().deleteJoint(pid, jointId); await this.refresh(true); } catch (e) { this.error.value = messageOf(e); }
  }

  // ---- instances

  select(id: string | null): void {
    if (this.draft.peek()) return; // clicks pick faces while a joint is being made
    this.selected.value = id;
  }

  /** Names already used in this assembly (lower-cased): a second copy of a part needs its own. */
  nameTaken(name: string): boolean {
    const n = name.trim().toLowerCase();
    return (this.data.peek()?.instances ?? []).some((i) => i.name.trim().toLowerCase() === n);
  }

  async addInstance(partId: string, name: string, fixed?: boolean): Promise<string | null> {
    const pid = this.projectId.peek();
    const clean = name.trim();
    if (!pid) return "No build is open.";
    const bad = nameProblem(clean);
    if (bad) return bad;
    if (this.nameTaken(clean)) return `“${clean}” is already in this assembly. Pick another name.`;
    const d = this.data.peek();
    // the first part is grounded so joints have something to hang from; later ones drop in beside
    const ground = fixed ?? (d?.instances.length ?? 0) === 0;
    const body = { part_id: partId, name: clean, fixed: ground, transform: this.dropSpot(partId) };
    try {
      const inst = await this.client().addInstance(pid, body);
      await this.refresh(false);
      this.selected.value = inst.id;
      return null;
    } catch (e) { return messageOf(e); }
  }

  /** Where a new part lands: to the right of everything already placed, 10 mm clear. */
  private dropSpot(partId: string): Mat4 {
    const d = this.data.peek();
    const size = this.part(partId)?.metrics?.bbox;
    if (!d || d.instances.length === 0 || !size) return [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];
    let maxX = -Infinity;
    for (const i of d.instances) {
      const b = this.part(i.part_id)?.metrics?.bbox;
      const t = this.pose[i.id] ?? i.transform;
      maxX = Math.max(maxX, t[0][3] + (b ? Math.max(Math.abs(b.min[0]), Math.abs(b.max[0]), Math.abs(b.min[1]), Math.abs(b.max[1])) : 0));
    }
    const x = Math.round(maxX + 10 - size.min[0]);
    return [[1, 0, 0, x], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];
  }

  async removeInstance(id: string): Promise<void> {
    const pid = this.projectId.peek();
    if (!pid) return;
    try { await this.client().deleteInstance(pid, id); await this.refresh(true); } catch (e) { this.error.value = messageOf(e); }
  }

  async setFixed(id: string, fixed: boolean): Promise<void> {
    const pid = this.projectId.peek();
    if (!pid) return;
    try { await this.client().patchInstance(pid, id, { fixed }); await this.refresh(true); } catch (e) { this.error.value = messageOf(e); }
  }

  async rename(id: string, name: string): Promise<string | null> {
    const pid = this.projectId.peek();
    const clean = name.trim();
    const cur = this.instance(id);
    if (!pid || !cur) return null;
    const bad = nameProblem(clean);
    if (bad) return bad;
    if (clean.toLowerCase() !== cur.name.trim().toLowerCase() && this.nameTaken(clean)) return `“${clean}” is already in this assembly.`;
    try { await this.client().patchInstance(pid, id, { name: clean }); await this.refresh(false); return null; } catch (e) { return messageOf(e); }
  }

  /** Gizmo drag of a free instance: pose it (and whatever hangs off it) every frame. */
  beginDrag(): void { this.begin(); }
  dragInstance(id: string, transform: Mat4): void {
    this.dragging.set(id, transform);
    this.predict();
  }
  /** Drag released (or a typed position): store the new transform. */
  async commitInstance(id: string, transform: Mat4, fromDrag = true): Promise<void> {
    const pid = this.projectId.peek();
    if (!pid) { if (fromDrag) this.end(); return; }
    this.dragging.set(id, transform);
    this.predict();
    try {
      await this.client().patchInstance(pid, id, { transform });
      const asm = await this.client().assembly(pid);
      this.dragging.delete(id);
      this.accept(asm, false);
    } catch (e) {
      this.dragging.delete(id);
      this.error.value = messageOf(e);
      void this.refresh(true);
    } finally {
      if (fromDrag) this.end();
      else this.scheduleInterference();
    }
  }

  // ---- making a joint

  startJoint(kind: JointKind = "revolute"): void {
    this.stopAnimate();
    batch(() => {
      this.draft.value = emptyDraft(kind);
      this.draftError.value = null;
      this.selected.value = null;
    });
  }

  updateDraft(patch: Partial<JointDraft>): void {
    const d = this.draft.peek();
    if (!d) return;
    let next = { ...d, ...patch };
    if (patch.kind && patch.kind !== d.kind) {
      const [lo, hi] = defaultLimits(patch.kind);
      next = { ...next, lo, hi, limited: patch.kind !== "fixed" && patch.kind !== "ball" };
    }
    this.draft.value = next;
    this.draftError.value = null;
    this.previewDraft();
  }

  /** A face picked in the view (or chosen from the list): fills the first face, then the second. */
  pickFace(instanceId: string, option: FaceOption, slot?: "a" | "b"): void {
    const d = this.draft.peek();
    if (!d) return;
    const which = slot ?? (d.a === null ? "a" : d.b === null ? "b" : "b");
    this.updateDraft({ [which]: { instanceId, option } });
  }

  /** Show where b would land, before anything is written. */
  private previewDraft(): void {
    const d = this.draft.peek();
    if (!d || draftProblem(d)) { this.predict(); return; }
    this.predict(jointBody(d));
  }

  cancelJoint(): void {
    batch(() => { this.draft.value = null; this.draftError.value = null; });
    this.predict();
  }

  async createJoint(): Promise<boolean> {
    const d = this.draft.peek();
    const pid = this.projectId.peek();
    if (!d || !pid) return false;
    const problem = draftProblem(d);
    if (problem) { this.draftError.value = problem; return false; }
    try {
      await this.client().addJoint(pid, jointBody(d));
      batch(() => { this.draft.value = null; this.draftError.value = null; });
      await this.refresh(false);
      return true;
    } catch (e) {
      this.draftError.value = messageOf(e);
      return false;
    }
  }

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
    if (this.busy.peek() || this.driveInflight) { this.scheduleInterference(); return; }
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

  /** Leaving assembly mode: stop motion and end any gesture left open (the gizmo is torn down
   *  without telling us the drag ended), then put the core's pose back. The data stays for a
   *  quick return. An uncommitted drag is dropped, not saved. */
  pause(): void {
    this.stopAnimate();
    this.cancelTween?.(); this.cancelTween = null;
    this.cancelSettle?.(); this.cancelSettle = null;
    this.cancelRefresh?.(); this.cancelRefresh = null;
    this.arriving.value = false;
    if (this.draft.peek()) this.cancelJoint();
    const open = this.gesture.peek() > 0 || this.dragging.size > 0 || this.moving.size > 0 || this.dirty;
    this.gesture.value = 0;
    this.dragging.clear();
    this.drivePending.clear();
    if (!this.driveInflight) this.moving.clear();
    this.dirty = false;
    if (open && this.data.peek()) void this.refresh(false);
  }

  /** Tests: forget everything. */
  reset(): void {
    this.pause();
    this.loadSeq++;
    this.interferenceSeq++;
    this.dragging.clear();
    this.moving.clear();
    this.driveInflight = false;
    this.drivePending.clear();
    this.dirty = false;
    this.gesture.value = 0;
    this.pose = {};
    batch(() => {
      this.projectId.value = null; this.data.value = null; this.parts.value = []; this.loading.value = false; this.error.value = null;
      this.values.value = {}; this.selected.value = null; this.draft.value = null; this.draftError.value = null;
      this.interference.value = IDLE_INTERFERENCE; this.animating.value = null; this.snap.value = true; this.gizmo.value = "move";
    });
  }
}

/** Instance names say what the part is for (design §5.1: never a trailing number). */
export function nameProblem(name: string): string | null {
  if (!name) return "Give the part a name.";
  if (isAutoNumbered(name)) return "Name it by what it does (like “left lid”), not with a number.";
  return null;
}

/** The name a part gets when added: its label, else its id in words. */
export function defaultInstanceName(part: Pick<AssemblyPart, "part_id" | "label">): string {
  return (part.label ?? part.part_id).replace(/[_-]+/g, " ").trim();
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
