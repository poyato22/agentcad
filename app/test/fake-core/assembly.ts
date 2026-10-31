// Fake core: assembly routes (core server/assembly_api.py, assembly/{model,solve}.py) with the real
// core's shapes, checked against core/tests/fixtures/ui/assembly*.json by the contract test.
//   · Kinematics use the app's port of the core solve (ui/assembly/kinematics.ts, itself checked
//     against the real core's numbers), so drives answer with the same transforms.
//   · GET returns SOLVED world transforms, like the core.
//   · Interference is the overlap of the instances' world-axis-aligned part boxes (the real core
//     intersects the solids with OCCT; for the boxy fixture plates the numbers agree).
//   · Every mutation emits `assembly_changed {project_id}`.
// Builds come from the ONE fake GET /projects (A3, fake-core/builds.ts); `projects` is that list.
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { Assembly, Instance, Joint, JointKind } from "../../src/api/assembly";
import { JOINT_KINDS } from "../../src/api/assembly";
import type { PartSummary } from "../../src/api/types";
import { clampValue, solve, transformPoint } from "../../src/ui/assembly/kinematics";
import { asBody, HttpError, mises, type Body, type EventIn, type Route } from "./server";

const FIXTURES = resolve(dirname(fileURLToPath(import.meta.url)), "../../../core/tests/fixtures/ui");
/** The real core's Default build (projects.json), so ids in the fixtures line up. */
export const DEFAULT_PROJECT_ROW = (JSON.parse(readFileSync(resolve(FIXTURES, "projects.json"), "utf8")) as ProjectRowIn[])[0];
export const DEFAULT_PROJECT = DEFAULT_PROJECT_ROW.id;
const PART_ID_RE = /^[a-z][a-z0-9_]{1,40}$/;
const THRESHOLD_MM3 = 0.01;

export interface ProjectRowIn { id: string; name: string; created_at?: number; trashed_at?: number | null }

export interface AssemblyScenario {
  /** Builds GET /projects lists (default: one "Default" build). */
  projects?: ProjectRowIn[];
  /** Stored assemblies per project id (stored transforms; GET answers solved ones). */
  by_project?: Record<string, { instances: Instance[]; joints: Joint[] }>;
  /** "none": the solids sit inside each other's boxes but don't touch (a nested enclosure), so the
   *  box-overlap check would report clashes the real (solid) check doesn't. Default "boxes". */
  interference?: "boxes" | "none";
  /** Plan 9 G-D: saved animations per project id (GET /projects/{id}/assembly/animations). */
  animations?: Record<string, unknown[]>;
}

export interface FakeAssembly {
  projects: ProjectRowIn[];
  byProject: Map<string, { instances: Instance[]; joints: Joint[] }>;
  interference: "boxes" | "none";
  animations: Record<string, unknown[]>;
}

export function assemblyFrom(sc: AssemblyScenario | undefined): FakeAssembly {
  return {
    projects: structuredClone(sc?.projects ?? [{ id: DEFAULT_PROJECT, name: "Default", created_at: DEFAULT_PROJECT_ROW.created_at, trashed_at: null }]),
    byProject: new Map(Object.entries(structuredClone(sc?.by_project ?? {}))),
    interference: sc?.interference ?? "boxes",
    animations: structuredClone(sc?.animations ?? {}),
  };
}

interface Deps {
  get(): FakeAssembly;
  parts(): readonly PartSummary[];
  emit(ev: EventIn): void;
}

const newId = () => randomBytes(6).toString("hex");
const IDENTITY = () => [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];

function invalid422(loc: string[], msg: string, input: unknown): HttpError {
  return new HttpError(422, { detail: [{ type: "value_error", loc: ["body", ...loc], msg, input }] });
}

function checkMatrix(v: unknown, field: string): number[][] {
  if (!Array.isArray(v) || v.length !== 4 || v.some((r) => !Array.isArray(r) || r.length !== 4 || r.some((x) => typeof x !== "number"))) {
    throw invalid422([field], "Input should be a 4x4 list of numbers", v);
  }
  return v as number[][];
}

function checkPartId(v: unknown): string {
  if (typeof v !== "string" || !PART_ID_RE.test(v)) throw invalid422(["part_id"], `Value error, part_id '${String(v)}' must match ${PART_ID_RE.source}`, v);
  return v;
}

function checkFrame(v: unknown, at: string): Joint["a"]["frame"] {
  const f = asBody(v);
  const origin = f.origin ?? null; const axis = f.axis ?? null; const face = f.face_id ?? null;
  if (face === null && (origin === null || axis === null)) throw invalid422([at, "frame"], "Value error, frame needs origin+axis or a face_id", v);
  for (const [n, x] of [["origin", origin], ["axis", axis]] as const) {
    if (x !== null && (!Array.isArray(x) || x.length !== 3)) throw invalid422([at, "frame", n], `Value error, frame ${n} must be 3 numbers`, x);
  }
  return { origin: origin as [number, number, number] | null, axis: axis as [number, number, number] | null, face_id: face as string | null };
}

function checkRef(v: unknown, at: string): Joint["a"] {
  const r = asBody(v);
  if (typeof r.instance_id !== "string") throw invalid422([at, "instance_id"], "Field required", v);
  return { instance_id: r.instance_id, frame: checkFrame(r.frame, at) };
}

function checkLimits(v: unknown): [number, number] | null {
  if (v === undefined || v === null) return null;
  if (!Array.isArray(v) || v.length !== 2 || v.some((x) => typeof x !== "number")) throw invalid422(["limits"], "Value error, limits must be [lo, hi]", v);
  if (v[0] > v[1]) throw invalid422(["limits"], "Value error, limits lo must be <= hi", v);
  return [v[0], v[1]];
}

function checkKind(v: unknown): JointKind {
  if (typeof v !== "string" || !(JOINT_KINDS as readonly string[]).includes(v)) {
    throw invalid422(["kind"], `Input should be ${JOINT_KINDS.map((k) => `'${k}'`).join(", ")}`, v);
  }
  return v as JointKind;
}

function checkValue(v: unknown): number | number[] {
  if (v === undefined) return 0;
  if (typeof v === "number") return v;
  if (Array.isArray(v) && v.every((x) => typeof x === "number")) return v as number[];
  throw invalid422(["value"], "Input should be a valid number", v);
}

export function assemblyRoutes(d: Deps): Route[] {
  const store = (pid: string) => {
    let a = d.get().byProject.get(pid);
    if (!a) { a = { instances: [], joints: [] }; d.get().byProject.set(pid, a); }
    return a;
  };
  const changed = (pid: string) => d.emit({ type: "assembly_changed", run_id: null, data: { project_id: pid } });
  const notFound = (id: string) => mises("not_found", `not in this assembly: '${id}'`);
  const faceResolver = (partId: string, faceId: string): [[number, number, number], [number, number, number]] | null => {
    const f = d.parts().find((p) => p.part_id === partId)?.metrics?.face_list?.find((x) => x.id === faceId);
    return f && f.normal ? [f.center, f.normal] : null;
  };
  const solved = (pid: string): Assembly => {
    const a = store(pid);
    const r = solve(a, faceResolver);
    const movable = a.instances.filter((i) => !i.fixed).length;
    const removes: Record<JointKind, number> = { fixed: 6, revolute: 5, slider: 5, cylindrical: 4, ball: 3 };
    const dof = Math.max(0, 6 * movable - a.joints.reduce((s, j) => s + removes[j.kind], 0));
    return {
      instances: a.instances.map((i) => ({ ...i, transform: r.transforms[i.id] })),
      joints: structuredClone(a.joints),
      dof_remaining: dof,
      overconstrained: [],
    };
  };
  const base = /^\/projects\/([^/]+)\/assembly/;

  return [
    // GET /projects: the ONE fake route is A3's (fake-core/builds.ts); this list is shared with it
    { method: "GET", re: new RegExp(`${base.source}$`), h: (m) => solved(m[1]) },
    {
      method: "POST", re: new RegExp(`${base.source}/instances$`), h: (m, _q, raw) => {
        const b = asBody(raw);
        if (typeof b.name !== "string") throw invalid422(["name"], "Field required", b);
        const inst: Instance = {
          id: newId(), part_id: checkPartId(b.part_id), name: b.name,
          transform: b.transform === undefined || b.transform === null ? IDENTITY() : checkMatrix(b.transform, "transform"),
          fixed: b.fixed === true,
        };
        store(m[1]).instances.push(inst);
        changed(m[1]);
        return inst;
      },
    },
    {
      method: "PATCH", re: new RegExp(`${base.source}/instances/([^/]+)$`), h: (m, _q, raw) => {
        const b: Body = asBody(raw);
        const inst = store(m[1]).instances.find((i) => i.id === m[2]);
        if (!inst) throw notFound(m[2]);
        if (b.part_id !== undefined && b.part_id !== null) inst.part_id = checkPartId(b.part_id);
        if (typeof b.name === "string") inst.name = b.name;
        if (b.transform !== undefined && b.transform !== null) inst.transform = checkMatrix(b.transform, "transform");
        if (typeof b.fixed === "boolean") inst.fixed = b.fixed;
        changed(m[1]);
        return inst;
      },
    },
    {
      method: "DELETE", re: new RegExp(`${base.source}/instances/([^/]+)$`), h: (m) => {
        const a = store(m[1]);
        if (!a.instances.some((i) => i.id === m[2])) throw notFound(m[2]);
        const removed = a.joints.filter((j) => j.a.instance_id === m[2] || j.b.instance_id === m[2]).map((j) => j.id);
        a.joints = a.joints.filter((j) => !removed.includes(j.id));
        a.instances = a.instances.filter((i) => i.id !== m[2]);
        changed(m[1]);
        return { removed_instance: m[2], removed_joints: removed };
      },
    },
    {
      // Plan 9 D29: core assembly_api remove_parts / restore (AssemblyStore.take_out / put_back)
      method: "POST", re: new RegExp(`${base.source}/remove_parts$`), h: (m, _q, raw) => {
        const ids = (asBody(raw).part_ids as unknown[] | undefined ?? []).map(String);
        const a = store(m[1]);
        const used = a.instances.map((i, k) => ({ i, k })).filter(({ i }) => ids.includes(i.part_id));
        const gone = new Set(used.map(({ i }) => i.id));
        const joints = a.joints.map((j, k) => ({ j, k })).filter(({ j }) => gone.has(j.a.instance_id) || gone.has(j.b.instance_id));
        a.instances = a.instances.filter((i) => !gone.has(i.id));
        a.joints = a.joints.filter((j) => !joints.some((x) => x.j === j));
        if (used.length) changed(m[1]);
        return {
          removed_instances: used.map(({ i }) => i.id), removed_joints: joints.map(({ j }) => j.id),
          undo: { instances: used.map(({ i, k }) => ({ ...i, _ord: k, _auto: false })), joints: joints.map(({ j, k }) => ({ ...j, _ord: k })) },
        };
      },
    },
    {
      method: "POST", re: new RegExp(`${base.source}/restore$`), h: (m, _q, raw) => {
        const b = asBody(raw);
        const a = store(m[1]);
        const strip = <T extends Record<string, unknown>>(x: T) => Object.fromEntries(Object.entries(x).filter(([k]) => !k.startsWith("_")));
        let ni = 0; let nj = 0;
        for (const x of (b.instances as Record<string, unknown>[] | undefined) ?? []) {
          if (a.instances.some((i) => i.id === x.id)) continue;
          a.instances.splice(Math.min(Number(x._ord ?? a.instances.length), a.instances.length), 0, strip(x) as unknown as Instance);
          ni++;
        }
        for (const x of (b.joints as Record<string, unknown>[] | undefined) ?? []) {
          a.joints.splice(Math.min(Number(x._ord ?? a.joints.length), a.joints.length), 0, strip(x) as unknown as Joint);
          nj++;
        }
        if (ni || nj) changed(m[1]);
        return { restored_instances: ni, restored_joints: nj };
      },
    },
    {
      method: "POST", re: new RegExp(`${base.source}/joints$`), h: (m, _q, raw) => {
        const b = asBody(raw);
        const j: Joint = {
          id: newId(), kind: checkKind(b.kind), a: checkRef(b.a, "a"), b: checkRef(b.b, "b"), value: checkValue(b.value), limits: checkLimits(b.limits),
        };
        const a = store(m[1]);
        for (const ref of [j.a, j.b]) {
          if (!a.instances.some((i) => i.id === ref.instance_id)) {
            throw mises("invalid_input", `joint ${j.id} references unknown instance '${ref.instance_id}'`);
          }
        }
        const trial = solve({ instances: a.instances, joints: [...a.joints, j] }, faceResolver);
        if (trial.unresolved.includes(j.id)) {
          throw mises("invalid_input", `joint ${j.id} closes a loop; this solve needs an open joint tree`, "remove the redundant joint or ground one side");
        }
        a.joints.push(j);
        changed(m[1]);
        return j;
      },
    },
    {
      method: "PATCH", re: new RegExp(`${base.source}/joints/([^/]+)$`), h: (m, _q, raw) => {
        const b = asBody(raw);
        const j = store(m[1]).joints.find((x) => x.id === m[2]);
        if (!j) throw notFound(m[2]);
        if (b.kind !== undefined && b.kind !== null) j.kind = checkKind(b.kind);
        if (b.a !== undefined && b.a !== null) j.a = checkRef(b.a, "a");
        if (b.b !== undefined && b.b !== null) j.b = checkRef(b.b, "b");
        if (b.value !== undefined && b.value !== null) j.value = checkValue(b.value);
        if (b.limits !== undefined && b.limits !== null) j.limits = checkLimits(b.limits);
        changed(m[1]);
        return j;
      },
    },
    {
      method: "DELETE", re: new RegExp(`${base.source}/joints/([^/]+)$`), h: (m) => {
        const a = store(m[1]);
        if (!a.joints.some((j) => j.id === m[2])) throw notFound(m[2]);
        a.joints = a.joints.filter((j) => j.id !== m[2]);
        changed(m[1]);
        return { removed_joint: m[2] };
      },
    },
    {
      method: "POST", re: new RegExp(`${base.source}/drive$`), h: (m, _q, raw) => {
        const b = asBody(raw);
        if (typeof b.joint_id !== "string") throw invalid422(["joint_id"], "Field required", b);
        const j = store(m[1]).joints.find((x) => x.id === b.joint_id);
        if (!j) throw notFound(b.joint_id);
        j.value = checkValue(b.value);
        const s = solved(m[1]);
        changed(m[1]);
        return {
          applied: clampValue(j.value, j.limits),
          transforms: Object.fromEntries(s.instances.map((i) => [i.id, i.transform])),
        };
      },
    },
    {
      method: "GET", re: new RegExp(`${base.source}/animations$`), h: (m) => d.get().animations[m[1]] ?? [],
    },
    {
      method: "GET", re: new RegExp(`${base.source}/interference$`), h: (m) => {
        const s = solved(m[1]);
        if (d.get().interference === "none") return [];
        const boxes = s.instances.map((i) => {
          const bb = d.parts().find((p) => p.part_id === i.part_id)?.metrics?.bbox;
          if (!bb) return null;
          const lo = [Infinity, Infinity, Infinity]; const hi = [-Infinity, -Infinity, -Infinity];
          for (const x of [bb.min[0], bb.max[0]]) for (const y of [bb.min[1], bb.max[1]]) for (const z of [bb.min[2], bb.max[2]]) {
            const p = transformPoint(i.transform, [x, y, z]);
            for (let k = 0; k < 3; k++) { lo[k] = Math.min(lo[k], p[k]); hi[k] = Math.max(hi[k], p[k]); }
          }
          return { name: i.name || i.id, lo, hi };
        });
        const out: { a: string; b: string; volume_mm3: number }[] = [];
        for (let x = 0; x < boxes.length; x++) {
          for (let y = x + 1; y < boxes.length; y++) {
            const A = boxes[x]; const B = boxes[y];
            if (!A || !B) continue;
            let v = 1;
            for (let k = 0; k < 3; k++) v *= Math.max(0, Math.min(A.hi[k], B.hi[k]) - Math.max(A.lo[k], B.lo[k]));
            if (v > THRESHOLD_MM3) out.push({ a: A.name, b: B.name, volume_mm3: Number(v.toFixed(6)) });
          }
        }
        return out;
      },
    },
  ];
}
