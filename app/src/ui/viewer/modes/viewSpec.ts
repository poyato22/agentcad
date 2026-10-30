// An agent-presented view (G-R7): the `view_spec` of a `view_presented` announcement, as the core's
// present_view tool validated it. Parsed defensively (anything malformed is dropped, never thrown)
// and turned into the modes, camera and orbit a live view renders.
//
//   {target: {part_id} | {project_id}, ghost?: bool, kind: "orbit"|"internal"|"section"|"exploded"|"subset",
//    parts?: [part_id], section?: {origin, normal}, xray_parts?: [part_id], explode?: 0..1,
//    camera?: {azimuth, elevation, distance} | "auto", orbit?: {duration_s, turns}, caption}
//
// G1's first cut sent `target` as a string with `is_project`; that is read too.
// Fields apply whatever the kind (a section view may also X-ray a part); the kind picks the
// defaults: an internal view with no xray_parts X-rays everything, an exploded view with no
// amount explodes fully, a subset isolates `parts` and (unless `ghost` is false) shows the rest of
// its build as faint ghosts. Camera azimuth runs from -Y toward +X, elevation up from XY (Z up).
import type { PartSummary } from "../../../api/types";
import { MODES_OFF, XRAY_OPACITY, type ModesState, type Vec3 } from "./state";

export const VIEW_KINDS = ["orbit", "internal", "section", "exploded", "subset"] as const;
export type ViewKind = (typeof VIEW_KINDS)[number];

export interface ViewCamera { azimuth: number; elevation: number; distance: number | null }

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
  /** A subset shows the rest of its build as ghosts (default) or alone. */
  ghost: boolean;
}

/** G1's present_view allows 400 characters. */
const CAPTION_CHARS = 400;
/** G1's OrbitSpec default. */
const ORBIT_S = 6;
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
  const flatIsProject = r.is_project === true;
  const target = {
    part_id: id(t?.part_id) ?? (flat && !flatIsProject ? flat : null),
    project_id: id(t?.project_id) ?? (flat && flatIsProject ? flat : null),
  };
  const ghostRaw = r.ghost ?? t?.ghost;
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
  return {
    kind, target, parts: ids(r.parts), section, xray_parts: ids(r.xray_parts),
    explode: ex === null ? null : Math.min(1, Math.max(0, ex)), camera, orbit, caption,
    ghost: ghostRaw !== false,
  };
}

/** The parts a view shows: the named subset's build (ghosted context) or `parts`, else the target. */
export function partsForSpec(s: ViewSpec, all: readonly PartSummary[]): string[] {
  const inBuild = s.target.project_id
    ? all.filter((p) => p.project_id === s.target.project_id && p.metrics !== null && (p.kind ?? "part") === "part").map((p) => p.part_id)
    : [];
  if (s.kind === "subset" && s.ghost && inBuild.length) return [...new Set([...inBuild, ...(s.parts ?? [])])];
  if (s.parts) return s.parts;
  if (s.target.part_id) return [s.target.part_id];
  return inBuild;
}

export function modesForSpec(s: ViewSpec): ModesState {
  const xrayParts = s.xray_parts ?? (s.kind === "internal" ? null : undefined);
  return {
    xray: xrayParts === undefined ? MODES_OFF.xray : { opacity: XRAY_OPACITY, parts: xrayParts },
    section: s.section
      ? { on: true, normal: s.section.normal, at: dot(unitV(s.section.normal), s.section.origin) }
      : s.kind === "section" ? { on: true, normal: [1, 0, 0], at: null } : MODES_OFF.section,
    // an exploded view with no amount (or 0) goes fully apart; other kinds only when asked
    explode: s.kind === "exploded" ? (s.explode || 1) : s.explode ?? 0,
    isolate: s.kind === "subset" && s.parts && s.ghost ? s.parts : null,
  };
}

function unitV(v: Vec3): Vec3 { const l = Math.hypot(...v) || 1; return [v[0] / l, v[1] / l, v[2] / l]; }
function dot(a: Vec3, b: Vec3): number { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }

/** Plain words for the kind (the card's label). */
export const VIEW_KIND_TEXT: Record<ViewKind, string> = {
  orbit: "Orbit", internal: "Inside (X-ray)", section: "Section", exploded: "Exploded", subset: "Selected parts",
};
