// Fake core: whole-assembly studies (Plan 13 H4; core server/system_study_api.py,
// sim/system_study.py) with the real core's shapes from
// core/scripts/dump_assembly_study_fixtures.py (core/tests/fixtures/ui/assembly_studies/): the
// kinds (the quadruped build: walk recommended), a walk and a drop record. POST /assembly_studies
// → 202 {run_id}; the study steps through its phases every `stepMs` (assembly_study_progress),
// then ends as the real record of its kind (walk / mechanism: the walk record; launch / drop: the
// drop record) and sim_finished. POST /jobs/{study_id}/cancel ends it cancelled. POST
// /assembly_studies/{id}/fem marks part stresses running, then done with one link.
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { BackgroundJob } from "../../src/api/jobs";
import { asBody, mises, HttpError, RAW, type EventIn, type Route } from "./server";

const DIR = resolve(import.meta.dirname, "../../../core/tests/fixtures/ui/assembly_studies");
const fx = <T>(n: string): T => JSON.parse(readFileSync(resolve(DIR, n), "utf8")) as T;
type Rec = Record<string, unknown> & { study_id: string; project_id: string; kind: string; status: string; run_id: string | null; created_at: number };
export const REAL_ASSEMBLY_STUDIES = {
  kinds: fx<{ project_id: string; kinds: Record<string, unknown>[] }>("assembly_study_kinds.json"),
  walk: fx<Rec>("assembly_study_walk.json"),
  drop: fx<Rec>("assembly_study_drop.json"),
};
const ID = /^as_[0-9a-f]{12}$/;
const KINDS = new Set(["walk", "launch", "drop", "mechanism"]);
const NAMES: Record<string, string> = { walk: "Walk", launch: "Launch", drop: "Drop", mechanism: "Run mechanism" };
const PHASES: Record<string, string[]> = { walk: ["dynamics", "stability", "range of motion"], mechanism: ["dynamics", "range of motion"], launch: ["stability", "flight"], drop: ["impact"] };
const SUMMARY_KEYS = ["study_id", "project_id", "kind", "name", "status", "verdict", "summary", "created_at", "finished_at", "run_id", "phase", "pct", "error", "hint"];

export interface AssemblyStudyDeps {
  liveBuild(id: string): boolean;
  newRun(task: string): { id: string };
  emit(ev: EventIn): void;
  jobs(): BackgroundJob[];
  setJobs(j: BackgroundJob[]): void;
  stepMs?: number;
}

export function assemblyStudiesFake(d: AssemblyStudyDeps): { routes: Route[]; reset(): void; records: Map<string, Rec>; cancel(id: string): boolean } {
  const records = new Map<string, Rec>();
  const timers = new Map<string, ReturnType<typeof setTimeout>[]>();
  const stepMs = d.stepMs ?? 300;
  const now = () => Date.now() / 1000;
  const summary = (r: Rec) => Object.fromEntries(SUMMARY_KEYS.map((k) => [k, r[k] ?? null]));
  const build = (pid: string | null) => { if (!pid || !d.liveBuild(pid)) throw mises("not_found", `no build '${String(pid)}'`); return pid; };
  const progress = (r: Rec, phase: string, pct: number, detail: string | null = null) => {
    Object.assign(r, { phase, pct });
    d.emit({ type: "assembly_study_progress", run_id: r.run_id, data: { study_id: r.study_id, project_id: r.project_id, kind: r.kind, phase, pct, detail, stream: "sim" } });
  };
  const finish = (r: Rec, status: string) => {
    d.setJobs(d.jobs().filter((j) => j.job_id !== r.study_id));
    d.emit({ type: "sim_finished", run_id: r.run_id, data: { kind: "assembly_study", job_id: r.study_id, run_id: r.run_id, build_id: r.project_id, status, summary: String(r.summary ?? ""), cancel_url: `/jobs/${r.study_id}/cancel`, study_id: r.study_id, project_id: r.project_id } });
    d.emit({ type: "run_finished", run_id: r.run_id, data: { status } });
  };

  function start(body: Record<string, unknown>): Rec {
    const pid = build(typeof body.project_id === "string" ? body.project_id : null);
    const kind = String(body.kind);
    if (!KINDS.has(kind)) throw new HttpError(422, { detail: [{ type: "literal_error", loc: ["body", "kind"], msg: "Input should be 'walk', 'launch', 'drop' or 'mechanism'" }] });
    const settings = (body.settings ?? {}) as Record<string, unknown>;
    if (kind === "drop" && typeof settings.drop_m === "number" && !(settings.drop_m > 0 && settings.drop_m <= 50)) throw mises("invalid_input", "bad drop settings: drop_m: Input should be greater than 0");
    const run = d.newRun(`Studies page: ${NAMES[kind].toLowerCase()} study of build ${pid}`);
    const id = `as_${randomBytes(6).toString("hex")}`;
    const r: Rec = { study_id: id, project_id: pid, kind, name: NAMES[kind], status: "running", verdict: null, phase: "queued", pct: 0, created_at: now(), finished_at: null, run_id: run.id, settings, summary: "", error: null, hint: null, checks: [], metrics: [], charts: [], playback: null, notes: [], accuracy: [], sources: [], fem: null };
    records.set(id, r);
    d.setJobs([...d.jobs(), { job_id: id, kind: "assembly_study", run_id: run.id, build_id: pid, part_id: null, label: `${NAMES[kind].toLowerCase()} study of the assembly`, status: "running", started_at: r.created_at, cancel_url: `/jobs/${id}/cancel` }]);
    const phases = PHASES[kind];
    const ts: ReturnType<typeof setTimeout>[] = [setTimeout(() => progress(r, "queued", 0), 0)];
    phases.forEach((ph, i) => ts.push(setTimeout(() => progress(r, ph, 10 + (80 * i) / phases.length), stepMs * (i + 1))));
    ts.push(setTimeout(() => {
      const real = structuredClone(kind === "walk" || kind === "mechanism" ? REAL_ASSEMBLY_STUDIES.walk : REAL_ASSEMBLY_STUDIES.drop);
      Object.assign(r, real, { study_id: id, project_id: pid, kind, name: NAMES[kind], run_id: run.id, created_at: r.created_at, finished_at: now(), settings: { ...(real.settings as object), ...settings }, status: "done", phase: "done", pct: 100 });
      progress(r, "done", 100);
      finish(r, "done");
    }, stepMs * (phases.length + 1)));
    timers.set(id, ts);
    return r;
  }

  function cancel(id: string): boolean {
    const r = records.get(id);
    if (!r || r.status !== "running") return false;
    for (const t of timers.get(id) ?? []) clearTimeout(t);
    Object.assign(r, { status: "cancelled", phase: "cancelled", finished_at: now(), summary: `${String(r.name)} cancelled` });
    progress(r, "cancelled", Number(r.pct ?? 0));
    finish(r, "cancelled");
    return true;
  }

  const get = (id: string): Rec => {
    const r = ID.test(id) ? records.get(id) : undefined;
    if (!r) throw mises("not_found", `no assembly study '${id}'`);
    return r;
  };

  const routes: Route[] = [
    { method: "GET", re: /^\/assembly_studies\/kinds$/, h: (_m, q) => ({ ...structuredClone(REAL_ASSEMBLY_STUDIES.kinds), project_id: build(q.get("project_id")) }) },
    { method: "GET", re: /^\/assembly_studies$/, h: (_m, q) => {
      const pid = build(q.get("project_id"));
      return [...records.values()].filter((r) => r.project_id === pid).sort((a, b) => b.created_at - a.created_at).map(summary);
    } },
    { method: "POST", re: /^\/assembly_studies$/, h: (_m, _q, raw, _r, res) => {
      const r = start(asBody(raw));
      const text = JSON.stringify({ run_id: r.run_id, status: "running" });
      res.writeHead(202, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(text) });
      res.end(text);
      return RAW;
    } },
    { method: "POST", re: /^\/assembly_studies\/([^/]+)\/fem$/, h: (m, _q, _raw, _r, res) => {
      const r = get(decodeURIComponent(m[1]));
      const fem = (r.fem ?? {}) as Record<string, unknown>;
      if (r.status !== "done") throw mises("invalid_input", `${r.study_id} is ${r.status}`, "check part stresses on a finished study");
      if (!fem.available) throw mises("invalid_input", `a ${r.kind} study has no per-part stress hand-off`);
      const run = d.newRun(`Studies page: part stresses of assembly study ${r.study_id}`);
      r.fem = { ...fem, status: "running", run_id: run.id };
      setTimeout(() => {
        r.fem = { ...(r.fem as object), status: "pass", summary: "part stresses pass: fl thigh SF 3.10 (fake core)",
          rows: [{ part_id: "fl_thigh", name: "fl thigh", sim_id: null, safety_factor: 3.1, status: "pass", reason: "SF 3.10 vs 2" }] };
        d.emit({ type: "run_finished", run_id: run.id, data: { status: "done" } });
      }, stepMs * 2);
      const text = JSON.stringify({ run_id: run.id, status: "running" });
      res.writeHead(202, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(text) });
      res.end(text);
      return RAW;
    } },
    { method: "GET", re: /^\/assembly_studies\/([^/]+)$/, h: (m) => get(decodeURIComponent(m[1])) },
  ];

  return {
    routes, records, cancel,
    reset() { for (const ts of timers.values()) for (const t of ts) clearTimeout(t); timers.clear(); records.clear(); },
  };
}
