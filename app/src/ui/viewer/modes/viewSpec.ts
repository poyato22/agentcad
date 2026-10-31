// An agent-presented view (G-R7): the `view_spec` of a `view_presented` announcement, as the core's
// present_view tool validated it. Parsed defensively (anything malformed is dropped, never thrown)
// and turned into the modes, camera and orbit a live view renders.
//
//   {kind: "orbit"|"internal"|"section"|"exploded"|"subset",
//    target: {kind: "part"|"project", id, part_id | project_id, parts: [shown], ghost: [ghosted]},
//    parts: [shown], ghost: [ghosted], section?: {origin, normal}, xray_parts?: [part_id],
//    explode?: 0..1, camera?: {azimuth, elevation, distance} | "auto", orbit?: {duration_s, turns},
//    caption, thumbnail_id}
//
// (G1's present_view, fix round 1.) Its first cut sent `target` as a bare string with
// `is_project`; that is read too, and a subset with no ghost list ghosts the rest of its build.
// Fields apply whatever the kind (a section view may also X-ray a part); the kind picks the
// defaults: an internal view with no xray_parts X-rays everything, an exploded view with no
// amount explodes fully. Ghosted parts are drawn faintly around the shown ones for context.
// Camera azimuth runs from -Y toward +X, elevation up from XY (Z up), as G1 documents it.
//
// Plan 9 G-D: two kinds show the build's assembly on its poses (the app opens them on the build
// view, not a LiveView): "animation" {animation: {animation_id, name, steps: [{joint_id, from,
// to, duration_s}], loops, duration_s}} plays a motion; "section_sweep" {sweep: {axis, from, to,
// duration_s}} sweeps a cut plane through it (from / to null: the assembly's box).
import type { AnimateStep, PartSummary } from "../../../api/types";
import { parseAnimateSteps } from "../../../state/assemblyAnimation";
import { MODES_OFF, XRAY_OPACITY, type ModesState, type Vec3 } from "./state";

export const VIEW_KINDS = ["orbit", "internal", "section", "exploded", "subset", "animation", "section_sweep"] as const;
export type ViewKind = (typeof VIEW_KINDS)[number];

export interface ViewCamera { azimuth: number; elevation: number; distance: number | null }

/** A presented motion (G-D): its steps, as the core checked them against the build's joints. */
export interface ViewAnimation { animation_id: string | null; name: string | null; steps: AnimateStep[]; loops: number; duration_s: number }

/** A presented sweep (G-D): mm along a world axis; null ends = the assembly's box. */
export interface ViewSweep { axis: "x" | "y" | "z"; from: number | null; to: number | null; duration_s: number }

/** The kinds the build view plays on the assembly's poses. */
export const isMotionKind = (k: ViewKind): k is "animation" | "section_sweep" => k === "animation" || k === "section_sweep";

export interface ViewSpec {
  kind: ViewKind;
  target: { part_id: string | null; project_id: string | null };
  parts: string[] | null;
  section: { origin: Vec3; normal: Vec3 } | null;
  xray_parts: string[] | null;
  explode: number | null;
  camera: ViewCamera | "auto";
  orbit: { duration_s: number; turns: number } | null;
  caption: string;
  /** Parts drawn faintly around the shown ones: these ids, or "build" (the rest of the target's
   *  build: a subset that named none). */
  ghost: string[] | "build";
  /** kind "animation": the motion (null: none that plays). */
  animation: ViewAnimation | null;
  /** kind "section_sweep": the sweep. */
  sweep: ViewSweep | null;
}

/** A sweep lasts this long when the spec names no (or a silly) duration. */
const SWEEP_S = 4;
const NAME_CHARS = 60;

function animationOf(v: unknown): ViewAnimation | null {
  const a = rec(v);
  if (!a) return null;
  const steps = parseAnimateSteps(a.steps);
  if (!steps.length) return null;
  const loops = num(a.loops);
  const name = typeof a.name === "string" ? a.name.replace(/\s+/g, " ").trim().slice(0, NAME_CHARS) : "";
  return {
    animation_id: id(a.animation_id), name: name || null, steps,
    loops: loops !== null && loops >= 1 ? Math.min(10, Math.floor(loops)) : 1,
    duration_s: steps.reduce((t, s) => t + s.duration_s, 0),
  };
}

function sweepOf(v: unknown): ViewSweep | null {
  const s = rec(v);
  const axis = s?.axis === "x" || s?.axis === "y" || s?.axis === "z" ? s.axis : null;
  if (!s || !axis) return null;
  const d = num(s.duration_s);
  return { axis, from: num(s.from), to: num(s.to), duration_s: d !== null && d > 0 ? Math.min(30, d) : SWEEP_S };
}

/** G1's present_view allows 200 characters; a little room for a looser core. */
const CAPTION_CHARS = 400;
/** G1's OrbitSpec default. */
const ORBIT_S = 12;
const ID_RE = /^[A-Za-z0-9_.:-]{1,128}$/;

function rec(v: unknown): Record<string, unknown> | null { return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : null; }
function num(v: unknown): number | null { return typeof v === "number" && Number.isFinite(v) ? v : null; }
function id(v: unknown): string | null { return typeof v === "string" && ID_RE.test(v) ? v : null; }
function ids(v: unknown): string[] | null {
  if (!Array.isArray(v)) return null;
  const out = v.map(id).filter((x): x is string => x !== null);
  return out.length ? [...new Set(out)] : null;
}
function vec(v: unknown): Vec3 | null {
  if (!Array.isArray(v) || v.length !== 3) return null;
  const n = v.map(num);
  return n.every((x) => x !== null) ? (n as Vec3) : null;
}

export function parseViewSpec(raw: unknown): ViewSpec | null {
  const r = rec(raw);
  if (!r) return null;
  const kind = VIEW_KINDS.find((k) => k === r.kind);
  if (!kind) return null;
  const t = rec(r.target);
  const flat = typeof r.target === "string" ? id(r.target) : null;
  const isProject = t ? t.kind === "project" || (t.kind === undefined && t.project_id !== undefined) : r.is_project === true;
  const tid = id(t?.id) ?? flat;
  const target = {
    part_id: id(t?.part_id) ?? (tid && !isProject ? tid : null),
    project_id: id(t?.project_id) ?? (tid && isProject ? tid : null),
  };
  const ghostRaw = r.ghost ?? t?.ghost;
  const ghost: string[] | "build" = Array.isArray(ghostRaw) ? ids(ghostRaw) ?? []
    : ghostRaw === false ? [] : ghostRaw === true || kind === "subset" ? "build" : [];
  const s = rec(r.section);
  const origin = vec(s?.origin);
  const normal = vec(s?.normal);
  const section = origin && normal && Math.hypot(...normal) > 1e-9 ? { origin, normal } : null;
  const c = rec(r.camera);
  const az = num(c?.azimuth); const el = num(c?.elevation);
  const camera: ViewCamera | "auto" = az !== null && el !== null ? { azimuth: az, elevation: el, distance: num(c?.distance) } : "auto";
  const o = rec(r.orbit);
  const orbit = o || kind === "orbit"
    ? { duration_s: Math.min(120, Math.max(2, num(o?.duration_s) ?? ORBIT_S)), turns: Math.min(10, Math.max(0.1, num(o?.turns) ?? 1)) }
    : null;
  const ex = num(r.explode);
  const caption = typeof r.caption === "string" ? r.caption.replace(/\s+/g, " ").trim().slice(0, CAPTION_CHARS) : "";
  const animation = kind === "animation" ? animationOf(r.animation) : null;
  const sweep = kind === "section_sweep" ? sweepOf(r.sweep) : null;
  // a motion card with nothing to play, or a sweep with no axis, is not a card
  if ((kind === "animation" && !animation) || (kind === "section_sweep" && !sweep)) return null;
  return {
    kind, target, parts: ids(r.parts) ?? ids(t?.parts), section, xray_parts: ids(r.xray_parts),
    explode: ex === null ? null : Math.min(1, Math.max(0, ex)), camera, orbit, caption,
    ghost, animation, sweep,
  };
}

function inBuild(s: ViewSpec, all: readonly PartSummary[]): string[] {
  return s.target.project_id
    ? all.filter((p) => p.project_id === s.target.project_id && p.metrics !== null && (p.kind ?? "part") === "part").map((p) => p.part_id)
    : [];
}

/** The parts shown at full strength: `parts`, else the target part, else the whole build. */
export function shownForSpec(s: ViewSpec, all: readonly PartSummary[]): string[] {
  return s.parts ?? (s.target.part_id ? [s.target.part_id] : inBuild(s, all));
}

/** The ghosted context around them. */
export function ghostsForSpec(s: ViewSpec, all: readonly PartSummary[]): string[] {
  const shown = new Set(shownForSpec(s, all));
  return (s.ghost === "build" ? inBuild(s, all) : s.ghost).filter((p) => !shown.has(p));
}

/** Every part the view loads: the shown ones, then the ghosts. */
export function partsForSpec(s: ViewSpec, all: readonly PartSummary[]): string[] {
  return [...new Set([...shownForSpec(s, all), ...ghostsForSpec(s, all)])];
}

export function modesForSpec(s: ViewSpec, all: readonly PartSummary[] = []): ModesState {
  const ghosts = ghostsForSpec(s, all);
  const xrayParts = s.xray_parts ?? (s.kind === "internal" ? null : undefined);
  return {
    xray: xrayParts === undefined ? MODES_OFF.xray : { opacity: XRAY_OPACITY, parts: xrayParts },
    section: s.section
      ? { on: true, normal: s.section.normal, at: dot(unitV(s.section.normal), s.section.origin) }
      : s.kind === "section" ? { on: true, normal: [1, 0, 0], at: null } : MODES_OFF.section,
    // an exploded view with no amount (or 0) goes fully apart; other kinds only when asked
    explode: s.kind === "exploded" ? (s.explode || 1) : s.explode ?? 0,
    // ghosts at all: the shown parts are the isolated set (a subset names its parts; with no
    // build at hand its ghosts are not known yet, so it still isolates)
    isolate: ghosts.length || (s.kind === "subset" && s.ghost === "build") ? shownForSpec(s, all) : null,
  };
}

function unitV(v: Vec3): Vec3 { const l = Math.hypot(...v) || 1; return [v[0] / l, v[1] / l, v[2] / l]; }
function dot(a: Vec3, b: Vec3): number { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }

/** Plain words for the kind (the card's label). */
export const VIEW_KIND_TEXT: Record<ViewKind, string> = {
  orbit: "Orbit", internal: "Inside (X-ray)", section: "Section", exploded: "Exploded", subset: "Selected parts",
  animation: "Animation", section_sweep: "Section sweep",
};
