// Test helpers for the assembly controller and panel: an in-memory core client seeded from the
// real-core fixtures (test/fixtures/core-ui/assembly*.json) and a hand-cranked clock.
import asmJson from "../../../test/fixtures/core-ui/assembly.json";
import partsJson from "../../../test/fixtures/core-ui/assembly_parts.json";
import { assemblyPartSchema, assemblySchema, type Assembly, type AssemblyPart, type Joint, type JointValue } from "../../api/assembly";
import { CoreError } from "../../api/client";
import type { AssemblyClient, Clock } from "./controller";
import { clampValue, solve } from "./kinematics";

export const PROJECT = "86c24e6134e7";
export const hingeFixture = (): Assembly => assemblySchema.parse(asmJson);
export const partsFixture = (): AssemblyPart[] => (partsJson as unknown[]).map((p) => assemblyPartSchema.parse(p));

export interface Call { name: string; args: unknown[] }

/** Resolvable promises so a test can hold a drive in flight. */
interface Gate { open(): void; wait: Promise<void> }
function gate(): Gate {
  let open = () => {};
  const wait = new Promise<void>((r) => { open = r; });
  return { open, wait };
}

export class MemoryAssemblyClient implements AssemblyClient {
  asm: Assembly;
  parts: AssemblyPart[];
  calls: Call[] = [];
  interferenceAnswer: { a: string; b: string; volume_mm3: number }[] = [];
  holdDrives = false;
  private gates: Gate[] = [];
  private nextId = 0;

  constructor(asm: Assembly = hingeFixture(), parts: AssemblyPart[] = partsFixture()) {
    this.asm = structuredClone(asm);
    this.parts = parts;
  }

  /** Lets the oldest held drive answer. */
  release(): void { this.gates.shift()?.open(); }

  private log(name: string, ...args: unknown[]): void { this.calls.push({ name, args }); }
  named(name: string): Call[] { return this.calls.filter((c) => c.name === name); }

  private resolved(): Assembly {
    const r = solve(this.asm);
    return { ...this.asm, instances: this.asm.instances.map((i) => ({ ...i, transform: r.transforms[i.id] })) };
  }

  projects = async () => { this.log("projects"); return [{ id: PROJECT, name: "Default", created_at: 1, trashed_at: null }]; };
  activeProjectId = async () => { this.log("activeProjectId"); return null; };
  assemblyParts = async (pid: string) => { this.log("assemblyParts", pid); return this.parts; };
  assembly = async (pid: string) => { this.log("assembly", pid); return structuredClone(this.resolved()); };
  addInstance: AssemblyClient["addInstance"] = async (pid, body) => {
    this.log("addInstance", pid, body);
    const inst = { id: `inst${++this.nextId}`, part_id: body.part_id, name: body.name, fixed: body.fixed ?? false,
      transform: body.transform ?? [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]] };
    this.asm.instances.push(inst);
    return inst;
  };
  patchInstance: AssemblyClient["patchInstance"] = async (pid, iid, patch) => {
    this.log("patchInstance", pid, iid, patch);
    const i = this.asm.instances.find((x) => x.id === iid);
    if (!i) throw new CoreError("not_found", `not in this assembly: '${iid}'`, { status: 404 });
    Object.assign(i, patch);
    return i;
  };
  deleteInstance: AssemblyClient["deleteInstance"] = async (pid, iid) => {
    this.log("deleteInstance", pid, iid);
    const gone = this.asm.joints.filter((j) => j.a.instance_id === iid || j.b.instance_id === iid).map((j) => j.id);
    this.asm.instances = this.asm.instances.filter((i) => i.id !== iid);
    this.asm.joints = this.asm.joints.filter((j) => !gone.includes(j.id));
    return gone;
  };
  addJoint: AssemblyClient["addJoint"] = async (pid, body) => {
    this.log("addJoint", pid, body);
    const trial = [...this.asm.joints, { ...body, id: "trial", value: body.value ?? 0, limits: body.limits ?? null } as Joint];
    if (solve({ ...this.asm, joints: trial }).unresolved.length) {
      throw new CoreError("invalid_input", "joint closes a loop; this solve needs an open joint tree", { status: 400 });
    }
    const j: Joint = { id: `joint${++this.nextId}`, kind: body.kind, a: body.a, b: body.b, value: body.value ?? 0, limits: body.limits ?? null };
    this.asm.joints.push(j);
    return j;
  };
  patchJoint: AssemblyClient["patchJoint"] = async (pid, jid, patch) => {
    this.log("patchJoint", pid, jid, patch);
    const j = this.asm.joints.find((x) => x.id === jid) as Joint;
    Object.assign(j, patch);
    return j;
  };
  deleteJoint: AssemblyClient["deleteJoint"] = async (pid, jid) => {
    this.log("deleteJoint", pid, jid);
    this.asm.joints = this.asm.joints.filter((j) => j.id !== jid);
  };
  drive: AssemblyClient["drive"] = async (pid, jid, value: JointValue) => {
    this.log("drive", pid, jid, value);
    if (this.holdDrives) { const g = gate(); this.gates.push(g); await g.wait; }
    const j = this.asm.joints.find((x) => x.id === jid) as Joint;
    j.value = clampValue(value, j.limits);
    const r = solve(this.asm);
    return { applied: j.value, transforms: r.transforms };
  };
  interference: AssemblyClient["interference"] = async (pid) => { this.log("interference", pid); return this.interferenceAnswer; };
}

/** A clock the test advances by hand: frames and timers run on tick(). */
export class ManualClock implements Clock {
  t = 0;
  private frames: (() => void)[] = [];
  private timers: { at: number; cb: () => void }[] = [];
  now = () => this.t;
  frame = (cb: () => void) => { this.frames.push(cb); return () => { this.frames = this.frames.filter((f) => f !== cb); }; };
  later = (cb: () => void, ms: number) => {
    const e = { at: this.t + ms, cb };
    this.timers.push(e);
    return () => { this.timers = this.timers.filter((x) => x !== e); };
  };
  /** Advance `ms` in 16 ms frames, running due timers and one round of frames per step. */
  tick(ms: number): void {
    const end = this.t + ms;
    while (this.t < end) {
      this.t = Math.min(end, this.t + 16);
      const due = this.timers.filter((x) => x.at <= this.t);
      this.timers = this.timers.filter((x) => x.at > this.t);
      due.forEach((x) => x.cb());
      const f = this.frames;
      this.frames = [];
      f.forEach((cb) => cb());
    }
  }
}

/** Let pending promise callbacks run. */
export async function flush(): Promise<void> {
  for (let i = 0; i < 8; i++) await Promise.resolve();
}
