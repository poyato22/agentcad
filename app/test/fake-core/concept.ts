// Fake core: Plan 9 G-A concept stage (core concept/api.py), built from the real core's dump
// (core/scripts/dump_concept_fixtures.py → core/tests/fixtures/ui/concept): the same pitch,
// sketch versions, images (scenarios/renders) and answer shapes, re-homed to the scenario's
// build. Marks are validated like core concept.FeedbackIn (normalised 0..1, 422 otherwise).
// After feedback the "lead" answers with the next sketch version after `revise_ms` (the dump's
// v2), as an announcement step on the lead plus concept_sketch / concept_updated events.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { AgentRun, Step } from "../../src/api/types";
import { HttpError, mises, type Body, type EventIn, type Route } from "./server";

export const CONCEPT_FIXTURES = resolve(dirname(fileURLToPath(import.meta.url)), "../../../core/tests/fixtures/ui/concept");

type Json = Record<string, unknown>;
type View = { concept: Json & { status: string; sketch_ids: string[]; pitch_ids?: string[]; chosen_option: string | null; approved_sketch_id?: string | null; project_id: string }; design_class: Json | null; sketches: Json[]; pitches: Json[] };

export interface ConceptScenario {
  /** What the build starts with: nothing, the dump's pitch, its v1 sketch only (default), v1 + v2
   *  with v1's feedback, or the approved view. */
  start?: "none" | "pitching" | "v1" | "v2" | "approved";
  /** The scenario's build the fixtures belong to (default: the Default build). */
  project_id?: string;
  /** The build's lead run the cards and messages go to (default: its newest top-level run). */
  lead_run?: string;
  /** ms after feedback until the lead answers with the next version; null: it never does. */
  revise_ms?: number | null;
  /** Design class the view reports. */
  design_class?: "engineering" | "mixed" | "design";
}

export interface FakeConcept { views: Map<string, View>; revise: number | null; spare: Json[]; timers: ReturnType<typeof setTimeout>[] }

const read = (name: string): Json => JSON.parse(readFileSync(resolve(CONCEPT_FIXTURES, name), "utf8")) as Json;
const clone = <T>(v: T): T => structuredClone(v);

/** Every string equal to the dump's build id becomes `to` (the fixtures' ids are random). */
function rehome<T>(v: T, from: string, to: string): T {
  return JSON.parse(JSON.stringify(v).split(`"${from}"`).join(`"${to}"`)) as T;
}

export function conceptFrom(sc: ConceptScenario | undefined, defaultProject: string): FakeConcept {
  const out: FakeConcept = { views: new Map(), revise: sc?.revise_ms === undefined ? 400 : sc.revise_ms, spare: [], timers: [] };
  const full = read("concept_view.json") as unknown as View;
  const dumpProject = String(full.concept.project_id);
  const project = sc?.project_id ?? defaultProject;
  const home = <T>(v: T): T => rehome(clone(v), dumpProject, project);
  // the dump's later versions, handed out one per feedback round
  out.spare = home(full.sketches.slice(1)).map((s) => ({ ...s, feedback: [] }));
  const start = sc?.start ?? "v1";
  let view: View;
  if (start === "none") {
    view = home({ ...full, sketches: [], pitches: [], concept: { ...full.concept, status: "none", sketch_ids: [], pitch_ids: [], chosen_option: null } });
  } else if (start === "pitching") {
    view = home(read("concept_view_pitching.json") as unknown as View);
  } else if (start === "approved") {
    view = home(read("concept_view_approved.json") as unknown as View);
    out.spare = [];
  } else if (start === "v2") {
    view = home(full);
    out.spare = [];
  } else {
    const v1: Json = { ...full.sketches[0], feedback: [] };
    view = home({ ...full, sketches: [v1], concept: { ...full.concept, sketch_ids: [v1.sketch_id as string], status: "sketching" } });
  }
  if (sc?.design_class) view.design_class = { cls: sc.design_class, source: "run" };
  out.views.set(project, view);
  return out;
}

export interface ConceptDeps {
  get(): FakeConcept;
  liveBuild(id: string): boolean;
  runs(): AgentRun[];
  newRun(task: string, projectId: string): AgentRun;
  appendStep(s: { kind: Step["kind"]; payload: Record<string, unknown> }, runId: string): Step;
  emit(ev: EventIn): void;
  leadRun?: string;
}

const bad = (message: string, hint?: string) => mises("invalid_input", message, hint ?? null);
const unprocessable = (msg: string, input: unknown) => new HttpError(422, { detail: [{ type: "value_error", loc: ["body"], msg, input }] });
const unit = (v: unknown) => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 1;
const hex6 = () => Math.random().toString(16).slice(2, 14).padEnd(12, "0");

function checkMark(m: unknown): Json {
  if (typeof m !== "object" || m === null) throw unprocessable("a mark is an object", m);
  const k = m as Json;
  const comment = k.comment ?? "";
  if (typeof comment !== "string" || comment.length > 1000) throw unprocessable("comment: at most 1000 characters", m);
  const image = k.image ?? 0;
  if (typeof image !== "number" || !Number.isInteger(image) || image < 0 || image > 7) throw unprocessable("image: 0..7", m);
  if (k.kind === "circle") {
    if (!unit(k.cx) || !unit(k.cy) || !(typeof k.r === "number" && k.r > 0 && k.r <= 1)) throw unprocessable("circle: cx, cy in 0..1, r in (0, 1]", m);
    return { image, comment, kind: "circle", cx: k.cx, cy: k.cy, r: k.r };
  }
  if (k.kind === "lasso") {
    const pts = k.points;
    if (!Array.isArray(pts) || pts.length < 3 || pts.length > 400 || !pts.every((p) => Array.isArray(p) && p.length === 2 && unit(p[0]) && unit(p[1]))) {
      throw unprocessable("lasso: 3..400 points in 0..1", m);
    }
    return { image, comment, kind: "lasso", points: pts };
  }
  if (k.kind === "pin") {
    if (!unit(k.x) || !unit(k.y)) throw unprocessable("pin: x, y in 0..1", m);
    return { image, comment, kind: "pin", x: k.x, y: k.y };
  }
  throw unprocessable("kind: circle, lasso or pin", m);
}

export function conceptRoutes(d: ConceptDeps): Route[] {
  const viewOf = (pid: string): View => {
    const v = d.get().views.get(pid);
    if (v) return v;
    const fresh = conceptFrom({ start: "none", project_id: pid }, pid).views.get(pid) as View;
    d.get().views.set(pid, fresh);
    return fresh;
  };
  const findSketch = (sid: string): { view: View; sketch: Json } => {
    for (const view of d.get().views.values()) {
      const sketch = view.sketches.find((s) => s.sketch_id === sid);
      if (sketch) return { view, sketch };
    }
    throw mises("not_found", `no concept sketch '${sid}'`);
  };
  const lead = (pid: string): AgentRun => {
    const named = d.leadRun ? d.runs().find((r) => r.id === d.leadRun) : undefined;
    return named ?? d.runs().filter((r) => r.provider !== "ui" && r.parent_id === null && r.project_id === pid && !r.archived)
      .sort((x, y) => y.created_at - x.created_at)[0] ?? d.newRun("concept", pid);
  };
  const touch = (view: View, runId: string | null) => {
    view.concept.updated_at = Date.now() / 1000;
    d.emit({ type: "concept_updated", run_id: runId, data: { project_id: view.concept.project_id, concept: view.concept, stream: "view" } });
  };
  const toLead = (pid: string, text: string, extra: Json) => {
    const run = lead(pid);
    d.appendStep({ kind: "message", payload: { role: "user", text, ...extra } }, run.id);
    d.emit({ type: "run_started", run_id: run.id, data: { resumed: true } });
    return { run_id: run.id, queued: false, position: null, message_id: null };
  };
  /** The lead's answer to a feedback round: the next spare version, linked to its parent. */
  const revise = (view: View, parent: Json) => {
    const c = d.get();
    const base = c.spare.shift() ?? clone(parent);
    const version = view.sketches.length + 1;
    const sk: Json = { ...clone(base), sketch_id: base === parent ? `cks_${hex6()}` : base.sketch_id, parent_sketch_id: parent.sketch_id, version, feedback: [], created_at: Date.now() / 1000 };
    view.sketches.push(sk);
    view.concept.sketch_ids = [...view.concept.sketch_ids, sk.sketch_id as string];
    const pid = view.concept.project_id;
    const run = lead(pid);
    const ref = { sketch_id: sk.sketch_id, version, project_id: pid, parent_sketch_id: parent.sketch_id, image_ids: (sk.images as Json[]).map((i) => i.render_id), stream: "view" };
    const ann = { run_id: run.id, kind: "concept_sketch", title: `Concept sketch v${version}`, detail: String(sk.notes ?? ""), ref };
    d.emit({ type: "agent_announce", run_id: run.id, data: ann });   // the fake stores it as a step, like the core
    d.emit({ type: "concept_sketch", run_id: run.id, data: { ...ref, sketch: sk } });
    touch(view, run.id);
  };

  return [
    {
      method: "GET", re: /^\/projects\/([^/]+)\/concept$/, h: (m) => {
        const pid = decodeURIComponent(m[1]);
        if (!d.liveBuild(pid)) throw mises("not_found", `unknown build: ${pid}`);
        return viewOf(pid);
      },
    },
    {
      method: "GET", re: /^\/concept\/sketches\/([^/]+)$/, h: (m) => {
        const { sketch } = findSketch(decodeURIComponent(m[1]));
        return { ...sketch, feedback: sketch.feedback ?? [] };
      },
    },
    {
      method: "POST", re: /^\/concept\/sketches\/([^/]+)\/feedback$/, h: (m, _q, raw) => {
        const { view, sketch } = findSketch(decodeURIComponent(m[1]));
        const b = (raw ?? {}) as Body;
        const marksIn = b.marks ?? [];
        const comment = b.comment ?? "";
        if (!Array.isArray(marksIn) || marksIn.length > 40) throw unprocessable("marks: at most 40", b);
        if (typeof comment !== "string" || comment.length > 4000) throw unprocessable("comment: at most 4000 characters", b);
        const images = sketch.images as Json[];
        const marks = marksIn.map(checkMark).map((mk, i) => {
          const img = images[mk.image as number];
          if (!img) throw bad(`mark ${i + 1} is on image ${String(mk.image)}, but the sketch has ${images.length}`);
          return { ...mk, n: i + 1, view: img.view, camera: img.camera } as Json;
        });
        if (!marks.length && !comment.trim()) throw bad("nothing to send", "add a mark or a comment");
        const fbId = `ckf_${hex6()}`;
        const annotated = images.flatMap((img, i) => (marks.some((mk) => mk.image === i) ? [{ view: img.view, image: i, render_id: `${fbId}_${String(img.view)}` }] : []));
        const version = sketch.version as number;
        const fb = { feedback_id: fbId, version, marks, comment, annotated, project_id: view.concept.project_id, sketch_id: sketch.sketch_id, created_at: Date.now() / 1000 };
        sketch.feedback = [...((sketch.feedback as Json[] | undefined) ?? []), fb];
        const parts = [marks.length ? `${marks.length} mark${marks.length === 1 ? "" : "s"}` : "", comment.trim() ? "a comment" : ""].filter(Boolean);
        const text = `Sketch feedback v${version}: ${parts.join(" and ") || "no marks"}…`;
        const sent = toLead(view.concept.project_id, text, { concept_feedback: { feedback_id: fbId, sketch_id: sketch.sketch_id, version, marks: marks.length, annotated_ids: annotated.map((a) => a.render_id) }, images: annotated.length });
        d.emit({ type: "concept_feedback", run_id: sent.run_id, data: { project_id: view.concept.project_id, sketch_id: sketch.sketch_id, feedback_id: fbId, stream: "view" } });
        const c = d.get();
        if (c.revise !== null) c.timers.push(setTimeout(() => revise(view, sketch), c.revise));
        return { ...sent, feedback_id: fbId, annotated_ids: annotated.map((a) => a.render_id), text, images: annotated.length };
      },
    },
    {
      method: "POST", re: /^\/projects\/([^/]+)\/concept\/approve$/, h: (m, _q, raw) => {
        const pid = decodeURIComponent(m[1]);
        if (!d.liveBuild(pid)) throw mises("not_found", `unknown build: ${pid}`);
        const view = viewOf(pid);
        const sid = (raw as Body | null)?.sketch_id;
        const sketch = typeof sid === "string" ? findSketch(sid).sketch : view.sketches[view.sketches.length - 1];
        if (!sketch) throw bad("there is no sketch to approve", "ask the agent for a concept sketch");
        Object.assign(view.concept, { status: "approved", approved_sketch_id: sketch.sketch_id });
        const sent = toLead(pid, `Approved sketch v${String(sketch.version)}`, { concept_approved: { sketch_id: sketch.sketch_id, version: sketch.version } });
        touch(view, sent.run_id);
        return { concept: view.concept, sent };
      },
    },
    {
      method: "POST", re: /^\/projects\/([^/]+)\/concept\/skip$/, h: (m) => {
        const pid = decodeURIComponent(m[1]);
        if (!d.liveBuild(pid)) throw mises("not_found", `unknown build: ${pid}`);
        const view = viewOf(pid);
        view.concept.status = "skipped";
        const sent = toLead(pid, "Skipped the sketch", { concept_skipped: true });
        touch(view, sent.run_id);
        return { concept: view.concept, sent };
      },
    },
    {
      method: "POST", re: /^\/concept\/pitches\/([^/]+)\/choose$/, h: (m, _q, raw) => {
        const pid0 = decodeURIComponent(m[1]);
        let view: View | undefined; let pitch: Json | undefined;
        for (const v of d.get().views.values()) { pitch = v.pitches.find((p) => p.pitch_id === pid0); if (pitch) { view = v; break; } }
        if (!view || !pitch) throw mises("not_found", `no pitch '${pid0}'`);
        const b = (raw ?? {}) as Body;
        const names = new Set((pitch.options as Json[]).map((o) => o.name as string));
        const mix = Array.isArray(b.mix) ? (b.mix as Json[]) : [];
        const option = typeof b.option === "string" ? b.option : null;
        if (!option && !mix.length) throw bad("pick an option or mix some", "send option, or mix[]");
        for (const n of [option, ...mix.map((x) => x.option)]) if (n !== null && !names.has(String(n))) throw bad(`no option '${String(n)}' in this pitch`);
        const label = option ?? mix.map((x) => (x.take ? `${String(x.option)}'s ${String(x.take)}` : String(x.option))).join(" + ");
        const chosen = { option, mix: mix.map((x) => ({ option: x.option, take: x.take ?? "", comment: x.comment ?? "" })), comment: typeof b.comment === "string" ? b.comment : "", label };
        pitch.chosen = chosen;
        view.concept.chosen_option = label;
        if (view.concept.status !== "approved" && view.concept.status !== "skipped") view.concept.status = "sketching";
        const sent = toLead(view.concept.project_id, `Chose ${label}`, { concept_choice: { pitch_id: pid0, label } });
        touch(view, sent.run_id);
        return { concept: view.concept, chosen, sent };
      },
    },
  ];
}

/** The announcement steps a scenario starts with (the pitch and every sketch version), on the lead. */
export function conceptSeedSteps(c: FakeConcept): { run_hint: string; payload: Json }[] {
  const out: { run_hint: string; payload: Json }[] = [];
  for (const view of c.views.values()) {
    const pid = view.concept.project_id;
    for (const p of view.pitches) {
      const names = (p.options as Json[]).map((o) => o.name);
      out.push({ run_hint: pid, payload: { agent_announce: { run_id: p.run_id, kind: "concept_pitch", title: `${names.length} design directions`, detail: names.join(" · "), ref: { pitch_id: p.pitch_id, project_id: pid, options: names, stream: "view" } } } });
    }
    for (const s of view.sketches) {
      out.push({ run_hint: pid, payload: { agent_announce: { run_id: s.run_id, kind: "concept_sketch", title: `Concept sketch v${String(s.version)}`, detail: String(s.notes ?? ""), ref: { sketch_id: s.sketch_id, version: s.version, project_id: pid, parent_sketch_id: s.parent_sketch_id ?? null,
        image_ids: (s.images as Json[]).map((i) => i.render_id), stream: "view" } } } });   // like the core's announce ref
    }
  }
  return out;
}

export function stopConcept(c: FakeConcept): void { c.timers.forEach(clearTimeout); c.timers = []; }
