// Fake core: the engineering loop's routes (core server/spec_api.py), with the real core's shapes.
// The spec, the design study, the spec_check rows and the apply answer come from the fixtures the
// real core wrote (core/scripts/dump_ui_fixtures.py --only spec → core/tests/fixtures/ui); the
// contract test checks this module against them. Also a minimal GET /projects (one live build),
// which the spec panel uses to find the active build until the app has builds of its own (A3).
//
// POST .../apply sets the part's vars to the variant's (bumping mtime) and emits a tool_result for
// study_apply, as the core's Gate does; a stale base_version is 409, an infeasible variant 400.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { Spec, SpecCheck, SpecDoc, Study } from "../../src/api/spec";
import type { PartSummary } from "../../src/api/types";
import { meetsSpec } from "../../src/ui/spec/model";
import { DEFAULT_PROJECT } from "./assembly";
import { asBody, HttpError, mises, type EventIn, type Route } from "./server";

const HERE = dirname(fileURLToPath(import.meta.url));
export const SPEC_FIXTURES = resolve(HERE, "../../../core/tests/fixtures/ui");
const fixture = <T>(name: string): T => JSON.parse(readFileSync(resolve(SPEC_FIXTURES, name), "utf8")) as T;

/** The real core's answers (fixtures). */
export const REAL_SPEC = {
  empty: fixture<SpecDoc>("spec_empty.json"),
  spec: fixture<SpecDoc>("spec.json"),
  studies: fixture<Study[]>("studies.json"),
  check: fixture<SpecCheck>("spec_check.json"),
  apply: fixture<{ study_id: string; part_id: string; iteration: number; summary: string; warnings: string[]; data: Record<string, unknown> }>("study_apply.json"),
};

/** The build the fake's spec lives in: the fake core's Default build (the ONE GET /projects is
 *  fake-core/assembly.ts), so the app's active-build lookup lands on it. The studies fixture is
 *  re-homed into it (specFrom). */
export const FIXTURE_PROJECT = DEFAULT_PROJECT;

export interface SpecScenario {
  /** The one live build. Default: the fixtures' build id. */
  project_id?: string;
  /** "fixture": the real core's drone-plate spec. Default: empty. */
  spec?: Spec | "fixture";
  /** "fixture": the real core's 6-evaluation study of mount_plate. Default: none. */
  studies?: Study[] | "fixture";
  /** Part versions (C6) for the stale check; absent: the core's "no version" (never stale). */
  versions?: Record<string, number>;
}

/** `updatedAt`: when the spec was last written (null: never), like the core's specs.updated_at. */
export interface FakeSpec { projectId: string; spec: Spec; updatedAt: number | null; studies: Map<string, Study>; versions: Record<string, number> }

export function specFrom(s: SpecScenario | undefined): FakeSpec {
  const projectId = s?.project_id ?? FIXTURE_PROJECT;
  const spec = s?.spec === "fixture" ? REAL_SPEC.spec.spec : (s?.spec ?? REAL_SPEC.empty.spec);
  const list = s?.studies === "fixture" ? REAL_SPEC.studies.map((x) => ({ ...x, project_id: projectId })) : (s?.studies ?? []);
  const updatedAt = s?.spec === undefined ? null : REAL_SPEC.spec.updated_at ?? 1793289539.3;
  return { projectId, spec: structuredClone(spec), updatedAt, studies: new Map(list.map((x) => [x.id, structuredClone(x)])), versions: { ...(s?.versions ?? {}) } };
}

export interface SpecDeps {
  get(): FakeSpec;
  parts(): readonly PartSummary[];
  setParts(p: PartSummary[]): void;
  emit(ev: EventIn): unknown;
  /** The part's C6 version in the part tree (builds fake), used when the scenario sets no
   *  `versions`; `bumpPart` is the accepted apply (version+1, part_changed by the user). */
  partVersion?(partId: string): number | undefined;
  bumpPart?(partId: string, summary: string): void;
}

const PART_ID_RE = /^[a-z][a-z0-9_]{1,40}$/;
const DIRS = new Set(["+x", "-x", "+y", "-y", "+z", "-z"]);
const NUMERIC = new Set(["max_mass_g", "min_safety_factor", "max_deflection_mm"]);

function invalid(msg: string): HttpError {
  return new HttpError(422, { error: "invalid_input", message: `invalid spec: ${msg}`, hint: "fix the listed fields", retryable: false });
}

/** The core's Spec validation, enough of it for the app's error paths. */
function validate(body: unknown): Spec {
  const b = asBody(body);
  const raw = (typeof b.spec === "object" && b.spec !== null ? b.spec : b) as Record<string, unknown>;
  const reqs = Array.isArray(raw.requirements) ? raw.requirements as Record<string, unknown>[] : [];
  const cases = Array.isArray(raw.load_cases) ? raw.load_cases as Record<string, unknown>[] : [];
  const caseIds = new Set(cases.map((c) => String(c.id)));
  reqs.forEach((r, i) => {
    if (NUMERIC.has(String(r.kind)) && !(typeof r.target === "number" && r.target > 0)) throw invalid(`requirements.${i}: Value error, ${String(r.kind)} target must be a positive number`);
    if (r.load_case_id && !caseIds.has(String(r.load_case_id))) throw invalid(`(root): Value error, requirement ${String(r.id)} references unknown load case '${String(r.load_case_id)}'`);
  });
  cases.forEach((c, i) => {
    if (!(typeof c.newtons === "number" && c.newtons > 0)) throw invalid(`load_cases.${i}.newtons: Input should be greater than 0`);
    if (!DIRS.has(String(c.direction))) throw invalid(`load_cases.${i}.direction: Input should be '+x', '-x', '+y', '-y', '+z' or '-z'`);
  });
  return {
    goal: typeof raw.goal === "string" ? raw.goal : "",
    requirements: reqs.map((r) => ({ id: String(r.id), kind: r.kind as Spec["requirements"][number]["kind"], target: r.target ?? null, load_case_id: (r.load_case_id as string | null | undefined) ?? null, priority: Number(r.priority ?? 3), source: r.source === "user" ? "user" : "agent" })),
    load_cases: cases.map((c) => ({ id: String(c.id), name: String(c.name), fix: String(c.fix), load: String(c.load), newtons: Number(c.newtons), direction: c.direction as Spec["load_cases"][number]["direction"], note: String(c.note ?? ""), estimate: Boolean(c.estimate) })),
    answers: (typeof raw.answers === "object" && raw.answers !== null ? raw.answers : {}) as Record<string, unknown>,
  };
}

/** Like core spec.evaluate_spec on geometry alone (no stress studies on the live part). */
function check(spec: Spec, part: PartSummary, projectId: string): SpecCheck {
  const rows = spec.requirements.map((r) => {
    let measured: unknown = null;
    let status: SpecCheck["overall"] = "not_checked";
    let detail = "";
    const t = r.target;
    if (r.kind === "max_mass_g" && typeof t === "number" && part.metrics) {
      measured = part.metrics.mass_g;
      status = part.metrics.mass_g <= t ? "pass" : part.metrics.mass_g <= t * 1.05 ? "warn" : "fail";
    } else if (r.kind === "envelope_mm" && Array.isArray(t) && part.metrics) {
      const size = part.metrics.bbox.size;
      measured = size;
      const s = [...size].sort((a, b) => a - b);
      const tt = [...(t as number[])].sort((a, b) => a - b);
      status = s.every((x, i) => x <= tt[i]) ? "pass" : s.every((x, i) => x <= tt[i] * 1.05) ? "warn" : "fail";
    } else if (r.kind === "min_safety_factor" || r.kind === "max_deflection_mm") {
      detail = "no stress study for this load case yet";
    } else {
      detail = "needs a human or a dedicated check; not evaluated from metrics";
    }
    const stress = r.kind === "min_safety_factor" || r.kind === "max_deflection_mm";
    const lc = spec.load_cases.find((c) => c.id === r.load_case_id);
    return { id: r.id, kind: r.kind, target: t, load_case_id: r.load_case_id, priority: r.priority, measured, status, detail, guide: stress, estimate: stress && !!lc?.estimate };
  });
  const order = { fail: 3, warn: 2, pass: 1, not_checked: 0 } as const;
  const checked = rows.filter((r) => r.status !== "not_checked");
  const overall = checked.length ? checked.reduce((a, b) => (order[b.status] > order[a.status] ? b : a)).status : "not_checked";
  return { project_id: projectId, part_id: part.part_id, requirements: rows, overall, checked: checked.length, total: rows.length };
}

export function specRoutes(d: SpecDeps): Route[] {
  const project = (id: string): FakeSpec => {
    const s = d.get();
    if (!id || id.length > 128) throw mises("invalid_input", "bad project id");
    return s;
  };
  const own = (id: string) => id === d.get().projectId;
  return [
    {
      method: "GET", re: /^\/projects\/([^/]+)\/spec$/, h: (m) => {
        const id = decodeURIComponent(m[1]);
        const s = project(id);
        return { project_id: id, spec: own(id) ? structuredClone(s.spec) : structuredClone(REAL_SPEC.empty.spec), updated_at: own(id) ? s.updatedAt : null };
      },
    },
    {
      method: "PUT", re: /^\/projects\/([^/]+)\/spec$/, h: (m, _q, body) => {
        const id = decodeURIComponent(m[1]);
        const s = project(id);
        const raw = asBody(body);
        const cur = own(id) ? s.updatedAt : null;
        if ("base_updated_at" in raw && raw.base_updated_at !== cur) {
          throw new HttpError(409, { error: "stale_version", message: "the spec changed since you opened it",
            hint: "reload it, or save yours anyway (without base_updated_at)", retryable: false, current_updated_at: cur });
        }
        const spec = validate(body);
        const at = Math.max(Date.now() / 1000, (cur ?? 0) + 0.001);
        if (own(id)) { s.spec = spec; s.updatedAt = at; }
        d.emit({ type: "spec_changed", run_id: null, data: { project_id: id } });
        return { project_id: id, spec, updated_at: at };
      },
    },
    {
      method: "GET", re: /^\/projects\/([^/]+)\/spec\/check$/, h: (m, q) => {
        const id = decodeURIComponent(m[1]);
        const s = project(id);
        const partId = q.get("part_id") ?? "";
        if (!PART_ID_RE.test(partId)) throw mises("invalid_input", `bad part id: '${partId}'`);
        const part = d.parts().find((p) => p.part_id === partId);
        if (!part || !part.metrics) throw mises("not_found", `no built part '${partId}'`, "create it with mirror_part_create");
        return check(own(id) ? s.spec : REAL_SPEC.empty.spec, part, id);
      },
    },
    {
      method: "GET", re: /^\/projects\/([^/]+)\/studies$/, h: (m) => {
        const id = decodeURIComponent(m[1]);
        const s = project(id);
        return own(id) ? [...s.studies.values()].sort((a, b) => b.created_at - a.created_at).map((x) => structuredClone(x)) : [];
      },
    },
    {
      method: "POST", re: /^\/projects\/([^/]+)\/studies\/([^/]+)\/apply$/, h: (m, _q, body) => {
        const id = decodeURIComponent(m[1]);
        const sid = decodeURIComponent(m[2]);
        const s = project(id);
        const b = asBody(body);
        if (typeof b.iteration !== "number" || !Number.isInteger(b.iteration) || b.iteration < 0) {
          throw new HttpError(422, { detail: [{ type: "missing", loc: ["body", "iteration"], msg: "Field required", input: b }] });
        }
        if (!sid.startsWith("study_") || sid.length > 64) throw mises("invalid_input", `bad study id: '${sid}'`);
        const st = s.studies.get(sid);
        if (!st || !own(id)) throw mises("not_found", `no study ${sid} in ${id}`);
        if (st.status === "running") throw mises("invalid_input", `${sid} is still running`, "wait for it to finish, or cancel it");
        const it = st.iterations.find((x) => x.n === b.iteration);
        if (!it) throw mises("not_found", `${sid} has no iteration ${b.iteration}`);
        // like the core's study_apply: against the spec as it is now, not the stored flag
        if (!meetsSpec(s.spec.requirements, it, st.geometry_only) && b.allow_infeasible !== true) {
          throw mises("invalid_input", `iteration ${b.iteration} is infeasible`, `apply the best feasible one (${st.best_id}) or set allow_infeasible`);
        }
        const pinned = s.versions[st.part_id];
        const cur = pinned ?? d.partVersion?.(st.part_id);
        if (typeof b.base_version === "number" && cur !== undefined && cur !== b.base_version) {
          throw mises("stale_version", `${st.part_id} is at version ${cur}, not ${b.base_version}`, "re-read the part, then decide again");
        }
        const parts = d.parts().map((p) => (p.part_id === st.part_id ? {
          ...p, vars: { ...p.vars, ...it.vars }, mtime: p.mtime + 1,
          metrics: p.metrics && it.metrics.mass_g !== null ? { ...p.metrics, mass_g: it.metrics.mass_g } : p.metrics,
        } : p));
        d.setParts(parts);
        if (pinned !== undefined) s.versions[st.part_id] = pinned + 1;
        else if (cur !== undefined) d.bumpPart?.(st.part_id, `applied variant #${b.iteration} of ${sid}`);
        const callId = `ui-${sid.slice(-6)}${b.iteration}`;
        const summary = `applied ${sid} #${b.iteration}: ${st.part_id}: mass ${it.metrics.mass_g?.toFixed(2) ?? "?"} g`;
        d.emit({ type: "tool_call", run_id: null, data: { call_id: callId, name: "study_apply", input: { study_id: sid, part_id: st.part_id, iteration: b.iteration } } });
        d.emit({ type: "tool_result", run_id: null, data: { call_id: callId, name: "study_apply", ok: true, summary } });
        return { study_id: sid, part_id: st.part_id, iteration: b.iteration, summary, warnings: [], data: { ...structuredClone(REAL_SPEC.apply.data), part_id: st.part_id, vars: it.vars, study_id: sid, iteration: b.iteration } };
      },
    },
  ];
}
