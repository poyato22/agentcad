// Test helpers for the assembly controller: an in-memory core client seeded from the
// real-core fixtures (test/fixtures/core-ui/assembly*.json) and a hand-cranked clock.
import asmJson from "../../../test/fixtures/core-ui/assembly.json";
import partsJson from "../../../test/fixtures/core-ui/assembly_parts.json";
import projectsJson from "../../../test/fixtures/core-ui/projects.json";
import { assemblyPartSchema, assemblySchema, type Assembly, type AssemblyPart } from "../../api/assembly";
import type { AssemblyClient, Clock } from "./controller";
import { solve } from "./kinematics";

export const PROJECT = projectsJson[0].id;
export const hingeFixture = (): Assembly => assemblySchema.parse(asmJson);
export const partsFixture = (): AssemblyPart[] => (partsJson as unknown[]).map((p) => assemblyPartSchema.parse(p));

export interface Call { name: string; args: unknown[] }

export class MemoryAssemblyClient implements AssemblyClient {
  asm: Assembly;
  parts: AssemblyPart[];
  calls: Call[] = [];
  interferenceAnswer: { a: string; b: string; volume_mm3: number }[] = [];

  constructor(asm: Assembly = hingeFixture(), parts: AssemblyPart[] = partsFixture()) {
    this.asm = structuredClone(asm);
    this.parts = parts;
  }

  private log(name: string, ...args: unknown[]): void { this.calls.push({ name, args }); }
  named(name: string): Call[] { return this.calls.filter((c) => c.name === name); }

  private resolved(): Assembly {
    const r = solve(this.asm);
    return { ...this.asm, instances: this.asm.instances.map((i) => ({ ...i, transform: r.transforms[i.id] })) };
  }

  projects = async () => { this.log("projects"); return projectsJson.map((p) => ({ id: p.id, name: p.name, created_at: p.created_at, trashed_at: p.trashed_at })); };
  activeProjectId = async () => { this.log("activeProjectId"); return null; };
  assemblyParts = async (pid: string) => { this.log("assemblyParts", pid); return this.parts; };
  assembly = async (pid: string) => { this.log("assembly", pid); return structuredClone(this.resolved()); };
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
