// Generative design panel logic (pure, no DOM): the form, its checks, the POST /topopt body,
// the live state of a run from its events, and how a variant card reads. Everything a card
// shows comes from the core's record; numbers are CalculiX's (verify), never the optimizer's.
import type { Direction, TopoptEvent, TopoptRecord, TopoptStartBody, TopoptVariant } from "../../api/topopt";

export type Goal = "volume" | "deflection";
export type Process = "none" | "print_3d" | "machined";
export type Axis = "x" | "y" | "z";

export interface GdLoad { face: string; newtons: number; direction: Direction }

export interface GdForm {
  partId: string;
  designSpace: "part" | "bbox";
  fixtures: string[];
  loads: GdLoad[];
  /** Faces that stay solid (a band of keepThickness mm around each). */
  keepFaces: string[];
  keepThickness: number;
  goal: Goal;
  volumeFraction: number;
  maxDeflection: number | null;
  variants: number;
  process: Process;
  symmetry: Axis[];
}

export const MIN_VF = 0.05;
export const MAX_VF = 0.75;
export const MAX_VARIANTS = 6;
export const DIRECTIONS: readonly Direction[] = ["-z", "+z", "-x", "+x", "-y", "+y"];
export const PROCESS_LABEL: Record<Process, string> = { none: "None", print_3d: "3D print", machined: "Machined" };

export function defaultForm(partId: string): GdForm {
  return {
    partId, designSpace: "part", fixtures: ["bottom"], loads: [{ face: "top", newtons: 100, direction: "-z" }],
    keepFaces: [], keepThickness: 2, goal: "volume", volumeFraction: 0.25, maxDeflection: null, variants: 3,
    process: "none", symmetry: [],
  };
}

export interface FormError { field: string; message: string }

export function validate(f: GdForm): FormError[] {
  const out: FormError[] = [];
  if (f.fixtures.length === 0) out.push({ field: "fixtures", message: "Hold the part somewhere: add a fixed face." });
  if (f.loads.length === 0) out.push({ field: "loads", message: "Add a load." });
  f.loads.forEach((l, i) => {
    if (!(l.newtons > 0)) out.push({ field: `loads.${i}`, message: "A load needs a force above 0 N." });
    if (f.fixtures.includes(l.face)) out.push({ field: `loads.${i}`, message: `${l.face} is both held and loaded.` });
  });
  if (f.goal === "volume" && !(f.volumeFraction >= MIN_VF && f.volumeFraction <= MAX_VF)) {
    out.push({ field: "volumeFraction", message: `Keep ${pct(MIN_VF)} to ${pct(MAX_VF)} of the material.` });
  }
  if (f.goal === "deflection" && !(f.maxDeflection !== null && f.maxDeflection > 0)) {
    out.push({ field: "maxDeflection", message: "Give the largest deflection you accept, in mm." });
  }
  if (!(f.variants >= 1 && f.variants <= MAX_VARIANTS)) out.push({ field: "variants", message: `1 to ${MAX_VARIANTS} variants.` });
  return out;
}

const round2 = (v: number): number => Math.round(v * 100) / 100;

/** n volume fractions centred on vf (one step 0.08), kept inside the allowed range. */
export function vfSpread(vf: number, n: number): number[] {
  if (n <= 1) return [round2(vf)];
  const step = 0.08;
  const lo = Math.max(MIN_VF + 0.03, Math.min(vf - (step * (n - 1)) / 2, MAX_VF - 0.03 - step * (n - 1)));
  return Array.from({ length: n }, (_, i) => round2(lo + i * step));
}

export function toBody(f: GdForm): TopoptStartBody {
  const body: TopoptStartBody = {
    part_id: f.partId, design_space: f.designSpace,
    fixtures: f.fixtures.map((face) => ({ kind: "fixed", face })),
    load_cases: [{ name: "case 1", loads: f.loads.map((l) => ({ ...l })) }],
    manufacturing: { process: f.process, ...(f.symmetry.length ? { symmetry: [...f.symmetry] } : {}) },
    reason: "generative design from the panel",
  };
  if (f.keepFaces.length) body.keep = [{ kind: "faces", faces: [...f.keepFaces], thickness_mm: f.keepThickness }];
  if (f.goal === "deflection") {
    // the lightest variant within a deflection needs several amounts of material: at least 3
    const n = Math.max(3, f.variants);
    body.objective = "min_mass";
    if (f.maxDeflection !== null) body.max_deflection_mm = f.maxDeflection;
    body.sweep = { volume_fraction: vfSpread(0.25, n) };
    body.max_variants = n;
  } else {
    body.volume_fraction = round2(f.volumeFraction);
    if (f.variants > 1) {
      body.sweep = { volume_fraction: vfSpread(f.volumeFraction, f.variants) };
      body.max_variants = f.variants;
    }
  }
  return body;
}

// ---- live state from events

export interface LiveVariant { phase: string; pct: number; it: number | null; maxIter: number | null; preview: number | null; compliance: number | null; status: string }
export type LiveRun = ReadonlyMap<string, LiveVariant>;

export function applyEvent(live: ReadonlyMap<string, LiveRun>, type: string, d: TopoptEvent): ReadonlyMap<string, LiveRun> {
  if (type !== "topopt_progress" || !d.variant) return live;
  const next = new Map(live);
  const run = new Map(next.get(d.topopt_id) ?? []);
  const old = run.get(d.variant);
  run.set(d.variant, {
    phase: d.phase ?? old?.phase ?? "queued", pct: d.pct ?? old?.pct ?? 0, it: d.it ?? old?.it ?? null,
    maxIter: d.max_iter ?? old?.maxIter ?? null, preview: d.preview ?? old?.preview ?? null,
    compliance: d.compliance ?? old?.compliance ?? null, status: d.status ?? old?.status ?? "running",
  });
  next.set(d.topopt_id, run);
  return next;
}

/** The record with each running variant's latest live progress laid over it. */
export function merged(rec: TopoptRecord, live: LiveRun | undefined): TopoptRecord {
  if (!live) return rec;
  return {
    ...rec,
    variants: rec.variants.map((v) => {
      const l = live.get(v.id);
      if (!l || v.status === "done" || v.status === "failed" || v.status === "cancelled") return v;
      return { ...v, phase: l.phase, pct: Math.max(v.pct, l.pct), it: l.it ?? v.it, max_iter: l.maxIter ?? v.max_iter, preview: l.preview ?? v.preview };
    }),
  };
}

export const isRunning = (rec: TopoptRecord): boolean => rec.status === "queued" || rec.status === "running";

/** Overall progress 0..100 (mean of the variants). */
export function overallPct(rec: TopoptRecord): number {
  if (!rec.variants.length) return 0;
  return Math.round(rec.variants.reduce((s, v) => s + (v.status === "done" ? 100 : v.pct), 0) / rec.variants.length);
}

/** The variant whose density preview to show while running (most advanced with a preview). */
export function previewVariant(rec: TopoptRecord): TopoptVariant | null {
  const withPreview = rec.variants.filter((v) => (v.preview ?? 0) > 0 && v.status !== "failed");
  if (!withPreview.length) return null;
  return withPreview.reduce((a, b) => ((b.it ?? 0) > (a.it ?? 0) ? b : a));
}

export const PHASE_LABEL: Record<string, string> = {
  queued: "Queued", setup: "Setting up", voxelize: "Voxelizing", optimize: "Optimizing", geometry: "Building the surface",
  export: "Exporting", render: "Rendering", verify: "Verifying in CalculiX", mesh: "Meshing", solve: "Solving", done: "Done",
  failed: "Failed", cancelled: "Cancelled",
};

export function phaseLine(v: TopoptVariant): string {
  const label = PHASE_LABEL[v.phase] ?? v.phase;
  if (v.phase === "optimize" && v.it) return `${label} · iteration ${v.it}${v.max_iter ? ` of ${v.max_iter}` : ""}`;
  return label;
}

// ---- variant cards

export type Badge = { kind: "pass" | "warn" | "fail"; text: string };

/** Verified ✓ / fails ✗ / unverified, from CalculiX (never the optimizer). */
export function verdict(v: TopoptVariant): Badge {
  if (v.status === "failed") return { kind: "fail", text: "Failed" };
  if (v.status !== "done") return { kind: "warn", text: PHASE_LABEL[v.phase] ?? v.phase };
  if (!v.verified) return { kind: "warn", text: v.verify?.error ? "Not verified" : "Unverified" };
  return v.passes ? { kind: "pass", text: "✓ Verified" } : { kind: "fail", text: "✗ Fails target" };
}

export function pct(v: number): string { return `${Math.round(v * 100)}%`; }

export function fmtMass(g: number | null | undefined): string {
  if (g === null || g === undefined) return "—";
  return g >= 1000 ? `${(g / 1000).toFixed(2)} kg` : `${g.toFixed(g < 10 ? 2 : 1)} g`;
}

export function fmtNum(v: number | null | undefined, unit: string, digits = 3): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${Number(v.toPrecision(digits))} ${unit}`;
}

export interface VariantRow { label: string; value: string }

export function variantRows(v: TopoptVariant): VariantRow[] {
  const ver = v.verify ?? null;
  return [
    { label: "Mass", value: fmtMass(v.mass_g) },
    { label: "Deflection", value: fmtNum(ver?.max_deflection_mm, "mm") },
    { label: "Peak stress", value: fmtNum(ver?.max_von_mises_incl_fixture_mpa ?? ver?.max_von_mises_mpa, "MPa") },
    { label: "Safety factor", value: ver?.safety_factor ? ver.safety_factor.toFixed(2) : "—" },
    ...(ver?.max_von_mises_incl_fixture_mpa !== undefined && ver.max_von_mises_mpa !== undefined
      && ver.max_von_mises_incl_fixture_mpa > ver.max_von_mises_mpa
      ? [{ label: "Away from the fixture", value: fmtNum(ver.max_von_mises_mpa, "MPa") }] : []),
  ];
}

/** Variants in the core's rank order (unranked last, by id). */
export function ranked(rec: TopoptRecord): TopoptVariant[] {
  return [...rec.variants].sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99) || a.id.localeCompare(b.id));
}

export function statusLine(rec: TopoptRecord): string {
  const done = rec.variants.filter((v) => v.status === "done").length;
  if (isRunning(rec)) return `Running · ${done} of ${rec.variants.length} variants done · ${overallPct(rec)}%`;
  if (rec.status === "done") {
    const best = rec.variants.find((v) => v.id === rec.recommended);
    return best ? `Done · recommended ${best.name}` : "Done · no variant passed verification";
  }
  return rec.status === "interrupted" ? "Interrupted (AgentCAD restarted)" : rec.status[0]?.toUpperCase() + rec.status.slice(1);
}
