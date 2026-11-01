// Fake core: Plan 7 U2a measure (POST /parts/{id}/measure, core server/measure_api.py) and
// feedback pins (POST /projects/{id}/feedback, core server/agents_api.py → orchestrator/lead.py), with the real core's
// shapes, messages and status codes (fixtures from core/scripts/dump_measure_feedback_fixtures.py).
//   · The fake never runs OCCT: values come from the part's metrics (face area, edge length and
//     radius) and simple geometry on their centres / normals; enough to drive the UI.
//   · A stale base_version is 409 {current_version}; an unknown face / edge id is 404 "pick again".
//   · Feedback goes to the build's newest top-level run (its lead), else a new one: a decision step
//     {feedback} plus the user's message step {role, text, feedback}, like the real core.
import { HttpError, missing, mises, type Body, type EventIn, type Route } from "./server";
import type { AgentRun, PartSummary, Step } from "../../src/api/types";

type V = [number, number, number];
type Val = { key: string; label: string; value: number; unit: string };

export interface MeasureDeps {
  parts(): PartSummary[];
  partVersion(id: string): number | undefined;
  partProject(id: string): string | undefined;
  /** The tree's label (what the core names the part in the location block). */
  partLabel(id: string): string | undefined;
  liveBuild(id: string): boolean;
  runs(): AgentRun[];
  newRun(task: string, projectId: string): AgentRun;
  appendStep(s: { kind: Step["kind"]; payload: Record<string, unknown> }, runId: string): Step;
  emit(ev: EventIn): void;
  /** Plan 10 E35: an instance of the build's assembly (an assembly pin names one). */
  instance?(projectId: string, instanceId: string): { part_id: string; name: string } | null;
}

const bad = (message: string, hint?: string) => mises("invalid_input", message, hint);
const r6 = (v: number) => Math.round(v * 1e6) / 1e6 + 0;
const val = (key: string, label: string, value: number, unit: string): Val => ({ key, label, value: r6(value), unit });
const sub = (a: V, b: V): V => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a: V, b: V) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const len = (a: V) => Math.sqrt(dot(a, a));
function lineAngle(a: V, b: V): number {
  const c = Math.max(-1, Math.min(1, dot(a, b) / (len(a) * len(b))));
  const d = (Math.acos(c) * 180) / Math.PI;
  return Math.min(d, 180 - d);
}

type Item = { kind: "face" | "edge" | "vertex" | "point"; id?: string; type?: string; area?: number; length?: number; radius?: number; center?: V; normal?: V; direction?: V; point?: V; arc_deg?: number; axis?: V; start?: V; end?: V };

function resolve(p: PartSummary, ref: unknown): Item {
  if (typeof ref !== "object" || ref === null || Object.keys(ref).length !== 1) throw bad("each ref is one of {face_id}, {edge_id}, {vertex_id} or {point}");
  const [k, v] = Object.entries(ref as Body)[0];
  if (k === "face_id") {
    const f = p.metrics?.face_list?.find((x) => x.id === v);
    if (!f) throw mises("not_found", `face '${String(v)}' is not on this version of the part; pick again`);
    // a hole's wall: area = π · d · depth (older metrics carry no radius)
    const wall = f.type === "cylinder" ? p.metrics?.holes.find((x) => Math.abs(Math.PI * x.diameter_mm * x.depth_mm - f.area) < 0.05) : undefined;
    const radius = f.radius ?? (wall ? wall.diameter_mm / 2 : undefined);
    return { kind: "face", id: f.id, type: f.type, area: f.area, center: f.center, ...(f.normal ? { normal: f.normal } : {}), ...(radius !== undefined ? { radius } : {}) };
  }
  if (k === "edge_id") {
    const e = p.metrics?.edge_list?.find((x) => x.id === v);
    if (!e) throw mises("not_found", `edge '${String(v)}' is not on this version of the part; pick again`);
    const c = e.center ?? [0, 0, 0];
    if (e.type === "line" && e.direction) {
      const h = e.length / 2;
      const dir = e.direction;
      return { kind: "edge", id: e.id, type: e.type, length: e.length, start: [c[0] - dir[0] * h, c[1] - dir[1] * h, c[2] - dir[2] * h], end: [c[0] + dir[0] * h, c[1] + dir[1] * h, c[2] + dir[2] * h], direction: dir };
    }
    // a hole's ring: the hole whose circumference is this edge's length (older metrics carry no
    // radius), its centre on the hole's axis (the fake has no curve, only the metrics)
    const hole = p.metrics?.holes.find((x) => e.radius !== undefined ? Math.abs(x.diameter_mm - 2 * e.radius) < 1e-3 : Math.abs(Math.PI * x.diameter_mm - e.length) < 0.01);
    const radius = e.radius ?? (hole ? hole.diameter_mm / 2 : undefined);
    const axis: V = hole ? [hole.axis[0], hole.axis[1], hole.axis[2]] : [0, 0, 1];
    const centre: V = hole ? [hole.center[0], hole.center[1], c[2]] : c;
    return { kind: "edge", id: e.id, type: e.type, length: e.length, ...(radius !== undefined ? { radius, center: centre, axis, arc_deg: 360 } : {}) };
  }
  if (k === "point") {
    if (!Array.isArray(v) || v.length !== 3 || !v.every((c) => typeof c === "number")) throw bad("point must be [x, y, z] in mm");
    // a corner of the bbox within 1 mm is that vertex (the real core snaps to the B-rep's, 0.02 mm)
    const bb = p.metrics!.bbox;
    const corners: V[] = [];
    for (const x of [bb.min[0], bb.max[0]]) for (const y of [bb.min[1], bb.max[1]]) for (const z of [bb.min[2], bb.max[2]]) corners.push([x, y, z]);
    const i = corners.findIndex((q) => len(sub(q, v as V)) <= 1);
    return i >= 0 ? { kind: "vertex", id: `v${i}`, point: corners[i] } : { kind: "point", point: v as V };
  }
  if (k === "vertex_id") throw mises("not_found", `vertex '${String(v)}' is not on this version of the part; pick again`);
  throw bad(`unknown ref '${k}': use face_id, edge_id, vertex_id or point`);
}

function one(i: Item): Val[] {
  const out: Val[] = [];
  if (i.kind === "point" || i.kind === "vertex") {
    const p = i.point as V;
    return [val("x", "X", p[0], "mm"), val("y", "Y", p[1], "mm"), val("z", "Z", p[2], "mm")];
  }
  if (i.radius !== undefined) out.push(val("diameter", "Diameter", 2 * i.radius, "mm"), val("radius", "Radius", i.radius, "mm"));
  out.push(i.kind === "edge" ? val("length", "Length", i.length ?? 0, "mm") : val("area", "Area", i.area ?? 0, "mm²"));
  return out;
}

function two(a: Item, b: Item): Val[] {
  const pa = (a.point ?? a.center ?? [0, 0, 0]) as V;
  const pb = (b.point ?? b.center ?? [0, 0, 0]) as V;
  const d = sub(pb, pa);
  const out: Val[] = [val("distance", "Distance", len(d), "mm"), val("dx", "ΔX", Math.abs(d[0]), "mm"), val("dy", "ΔY", Math.abs(d[1]), "mm"), val("dz", "ΔZ", Math.abs(d[2]), "mm")];
  const da = a.normal ?? a.direction;
  const db = b.normal ?? b.direction;
  if (da && db) {
    const ang = lineAngle(da, db);
    out.push(val("angle", "Angle", ang, "°"));
    if (a.normal && b.normal && ang < 0.01) {
      const gap = Math.abs(dot(d, a.normal));
      out[0] = val("distance", "Distance", gap, "mm");
      out.unshift(val("normal_distance", "Normal distance", gap, "mm"));
    }
  }
  return out;
}

export function measureRoutes(d: MeasureDeps): Route[] {
  return [
    {
      method: "POST", re: /^\/parts\/([^/]+)\/measure$/, h: (m, _q, raw) => {
        const id = decodeURIComponent(m[1]);
        const p = d.parts().find((x) => x.part_id === id);
        if (!p || !p.metrics) throw mises("not_found", `no built part '${id}'`);
        const b = (raw ?? {}) as Body;
        if (b.warm === true && b.refs === undefined) return { part_id: id, warm: true };    // Measure mode opened
        const refs = b.refs;
        if (!Array.isArray(refs) || refs.length < 1 || refs.length > 8) throw bad("refs must list 1 to 8 picks");
        const version = d.partVersion(id) ?? p.version ?? 0;
        const bv = b.base_version;
        if (bv !== undefined && bv !== null) {
          if (typeof bv !== "number") throw bad("base_version must be an integer");
          if (bv !== version) {
            throw new HttpError(409, { error: "stale_version", retryable: false, message: `${id} changed since version ${bv}; pick again`, current_version: version });
          }
        }
        const items = refs.map((r) => resolve(p, r));
        let values: Val[];
        if (items.length === 1) values = one(items[0]);
        else if (items.length === 2) values = two(items[0], items[1]);
        else if (items.every((i) => i.kind === "edge")) values = [val("total_length", "Total length", items.reduce((s, i) => s + (i.length ?? 0), 0), "mm")];
        else if (items.every((i) => i.kind === "face")) values = [val("total_area", "Total area", items.reduce((s, i) => s + (i.area ?? 0), 0), "mm²")];
        else throw bad("pick one or two items (or several edges, or several faces)");
        return { part_id: id, version, items, values };
      },
    },
    {
      // U3c's route (core orchestrator/lead.submit_feedback): the pin is stored as structured
      // `feedback` on a decision step (the answer's step_id) and on the user's message step; the
      // lead gets the wrapped block, the thread never shows it
      method: "POST", re: /^\/projects\/([^/]+)\/feedback$/, h: (m, _q, raw) => {
        const pid = decodeURIComponent(m[1]);
        if (!d.liveBuild(pid)) throw mises("not_found", `unknown build: ${pid}`);
        const b = (raw ?? {}) as Body;
        const text = b.text;
        if (typeof text !== "string" || !text) throw missing("text", b);
        const point = b.point;
        if (!Array.isArray(point) || point.length !== 3 || !point.every((c) => typeof c === "number" && Number.isFinite(c))) throw missing("point", b);
        const partId = String(b.part_id ?? "");
        const p = d.parts().find((x) => x.part_id === partId);
        if (!p || d.partProject(partId) !== pid) throw mises("not_found", `no part '${partId}' in this build`);
        const faceId = typeof b.face_id === "string" ? b.face_id : null;
        const f = p.metrics?.face_list?.find((x) => x.id === faceId) ?? null;
        const pin = {
          face_id: faceId, nearby_feature: f ? { area_mm2: f.area, center: f.center, face_id: f.id, type: f.type } : null,
          nearby_hole: null, part: d.partLabel(partId) ?? partId, part_id: partId, point, text,
        };
        // Plan 10 E35 (core lead.submit_feedback): an assembly pin names the instance and the world point
        if (b.instance_id !== undefined && b.instance_id !== null) {
          const wp = b.world_point;
          if (wp !== undefined && wp !== null && !(Array.isArray(wp) && wp.length === 3 && wp.every((c) => typeof c === "number" && Number.isFinite(c)))) throw missing("world_point", b);
          const inst = d.instance?.(pid, String(b.instance_id)) ?? null;
          if (!inst || inst.part_id !== partId) throw mises("not_found", `no instance '${String(b.instance_id)}' of '${partId}' in this build's assembly`);
          Object.assign(pin, { instance_id: String(b.instance_id), instance: inst.name, world_point: (wp as number[] | undefined) ?? point });
        }
        const lead = d.runs().filter((r) => r.provider !== "ui" && r.parent_id === null && r.project_id === pid && !r.archived)
          .sort((x, y) => y.created_at - x.created_at)[0] ?? d.newRun(text, pid);
        const step = d.appendStep({ kind: "decision", payload: { feedback: pin } }, lead.id);
        d.appendStep({ kind: "message", payload: { feedback: pin, role: "user", text } }, lead.id);
        d.emit({ type: "run_started", run_id: lead.id, data: { resumed: true } });
        return { run_id: lead.id, step_id: step.id, routed: { agent: lead.agent, reason: "build lead", confidence: 1 }, queued: false, position: null, message_id: null };
      },
    },
  ];
}
