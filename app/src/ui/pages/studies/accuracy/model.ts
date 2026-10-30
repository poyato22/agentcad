// Plan 6 pure logic (no DOM, no core): the calibration sheet (rows ↔ the core's request body,
// what each test implies on its own as you type, which fitted residual belongs to which row), the
// fatigue cycle and S-N arithmetic the chart draws (core sim/fatigue.py: σa = A·N^b, Goodman),
// cycle counts as words, and print directions. The core's numbers always win; these only preview
// and draw.
import type { BendTest, Calibration, CalibrationBody, CalibrationFit, CalibrationTestKind, Confidence, PartTest, SnCurve, TensileTest } from "../../../../api/accuracy";

const pos = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v > 0;

// ---- the calibration sheet

export type SheetRow =
  | { kind: "tensile"; t: TensileTest }
  | { kind: "bend"; t: BendTest }
  | { kind: "part"; t: PartTest };

export interface Implied { e_mpa: number | null; strength_mpa: number | null }

/** Tensile cross-section: the area, else width × thickness. */
export function tensileArea(t: TensileTest): number | null {
  if (pos(t.area_mm2)) return t.area_mm2;
  return pos(t.width_mm) && pos(t.thickness_mm) ? t.width_mm * t.thickness_mm : null;
}

/**
 * One test's own reading, as core sim/calibration.py computes it. Tensile: strength = F / A (with an
 * elongation, a secant modulus). Three-point bend: E = P L³ / (48 δ I), I = b h³ / 12. A part load
 * test needs its study's deflection, so only the core can turn it into a modulus.
 */
export function implied(r: SheetRow): Implied {
  if (r.kind === "tensile") {
    const a = tensileArea(r.t);
    if (!pos(r.t.force_n) || a === null) return { e_mpa: null, strength_mpa: null };
    const s = r.t.force_n / a;
    return { e_mpa: pos(r.t.elongation_pct) ? s / (r.t.elongation_pct / 100) : null, strength_mpa: s };
  }
  if (r.kind === "bend") {
    const t = r.t;
    if (!pos(t.load_n) || !pos(t.span_mm) || !pos(t.width_mm) || !pos(t.thickness_mm) || !pos(t.deflection_mm)) return { e_mpa: null, strength_mpa: null };
    const inertia = (t.width_mm * t.thickness_mm ** 3) / 12;
    return { e_mpa: (t.load_n * t.span_mm ** 3) / (48 * t.deflection_mm * inertia), strength_mpa: null };
  }
  return { e_mpa: null, strength_mpa: null };
}

const clean = <T extends Record<string, unknown>>(o: T, keys: readonly string[]): T => {
  const out: Record<string, unknown> = {};
  for (const k of keys) {
    const v = o[k];
    if (v === null || v === undefined || v === "" || (typeof v === "number" && Number.isNaN(v))) continue;
    out[k] = v;
  }
  return out as T;
};
const KEYS: Record<CalibrationTestKind, readonly string[]> = {
  tensile: ["force_n", "width_mm", "thickness_mm", "area_mm2", "elongation_pct", "orientation", "note"],
  bend: ["span_mm", "width_mm", "thickness_mm", "load_n", "deflection_mm", "break_load_n", "orientation", "note"],
  part: ["sim_id", "point", "load_n", "deflection_mm", "direction", "study_load_n", "note"],
};

/** The request body of a sheet: only the core's keys, empty optionals left out (its models forbid extras). */
export function bodyOf(material: string, printer: string, rows: readonly SheetRow[]): CalibrationBody {
  const pick = <K extends CalibrationTestKind>(k: K) => rows.filter((r) => r.kind === k).map((r) => clean(r.t as Record<string, unknown>, KEYS[k]));
  return {
    material: material.trim(), printer: printer.trim(),
    tensile: pick("tensile") as TensileTest[], bend: pick("bend") as BendTest[], part: pick("part") as PartTest[],
  };
}

/** A saved record back into sheet rows (the core's stored defaults are dropped by bodyOf). */
export function rowsOf(c: Calibration): SheetRow[] {
  return [
    ...c.tests.tensile.map((t) => ({ kind: "tensile" as const, t })),
    ...c.tests.bend.map((t) => ({ kind: "bend" as const, t })),
    ...c.tests.part.map((t) => ({ kind: "part" as const, t })),
  ];
}

/** Rows in the order the core sends them back (tensile, bend, part): a sheet compared by body is order-free per kind. */
export const bodyKey = (b: CalibrationBody): string => JSON.stringify(b);

/**
 * The sheet rows behind each fitted E residual (core calibration.fit: E tests are the "xy" bends,
 * then the part tests, less the outliers the screen dropped; a tensile secant modulus is never an
 * E test, it is reported as fit.e_secant only).
 */
export function eRows(rows: readonly SheetRow[], fit: CalibrationFit): number[] {
  const dropped = new Set((fit.outliers ?? []).filter((o) => o.property === "e").map((o) => `${o.test}:${o.index}`));
  const out: number[] = [];
  let bend = 0; let part = 0;
  const bends: number[] = []; const parts: number[] = [];
  rows.forEach((r, i) => {
    if (r.kind === "bend") { const k = bend++; if ((r.t.orientation ?? "xy") === "xy" && !dropped.has(`bend:${k}`)) bends.push(i); }
    if (r.kind === "part") { const k = part++; if (!dropped.has(`part:${k}`)) parts.push(i); }
  });
  out.push(...bends, ...parts);
  return out.slice(0, fit.e?.residuals.length ?? 0);
}

/** Each E test's own value from the fit: residual r_i = E_i / E − 1, so E_i = E (1 + r_i). */
export function eFromResidual(e: number, r: number): number | null {
  return Number.isFinite(r) ? e * (1 + r) : null;
}

/** The sheet row of an outlier (its kind and index within that kind), or −1. */
export function outlierRow(rows: readonly SheetRow[], o: { test: string; index: number }): number {
  let k = -1;
  return rows.findIndex((r) => r.kind === o.test && ++k === o.index);
}

/** Problems with one row before it is sent (the core checks again). */
export function rowProblems(r: SheetRow): string[] {
  const out: string[] = [];
  const need = (v: unknown, what: string) => { if (!pos(v)) out.push(`${what} above 0`); };
  if (r.kind === "tensile") {
    need(r.t.force_n, "break force");
    if (tensileArea(r.t) === null) out.push("width and thickness (or the area)");
  } else if (r.kind === "bend") {
    need(r.t.span_mm, "span"); need(r.t.width_mm, "width"); need(r.t.thickness_mm, "thickness");
    need(r.t.load_n, "load"); need(r.t.deflection_mm, "deflection");
  } else {
    if (!/^sim_[0-9a-f]{12}$/.test(r.t.sim_id)) out.push("the study of the loaded part");
    if (!Array.isArray(r.t.point) || r.t.point.length !== 3 || r.t.point.some((x) => typeof x !== "number" || !Number.isFinite(x))) out.push("the point where you measured");
    need(r.t.load_n, "load"); need(r.t.deflection_mm, "measured deflection");
  }
  return out;
}

/** The fit's property keys (core calibration.py) as words. */
const PROP_WORDS: Record<string, string> = { e: "Stiffness (E)", e_z: "Stiffness across the layers (E_z)", strength: "Strength", z_strength: "Strength across the layers" };

/** A fit warning in plain words (m7: "e: only 2 test(s): low confidence" →
 *  "Stiffness (E): only 2 tests, low confidence"). Unknown shapes pass through. */
export function calWarningText(w: string): string {
  const m = /^([a-z_]+): (.*)$/.exec(w.trim());
  let out = m && PROP_WORDS[m[1]] ? `${PROP_WORDS[m[1]]}: ${m[2]}` : w;
  out = out.replace(/\b(\d+) test\(s\)/g, (_s, n: string) => `${n} test${n === "1" ? "" : "s"}`).replace(/: low confidence$/, ", low confidence");
  return out;
}

export const CONFIDENCE_TEXT: Record<Confidence, string> = {
  low: "low confidence: add tests",
  medium: "medium confidence",
  high: "high confidence",
};

export const TEST_TEXT: Record<CalibrationTestKind, string> = {
  tensile: "tensile bar", bend: "3-point bend", part: "part load test",
};

// ---- fatigue (core sim/fatigue.py)

export type CycleMode = "fully_reversed" | "zero_to_max" | "min_max";

/** The load over one cycle as fractions of the case's load: [min, max]. min_max: min_pct % of the load. */
export function cycleRange(mode: string, minPct: number | null | undefined): [number, number] {
  if (mode === "fully_reversed") return [-1, 1];
  if (mode === "min_max") {
    const r = typeof minPct === "number" && Number.isFinite(minPct) ? Math.max(-100, Math.min(100, minPct)) / 100 : 0;
    return [r, 1];
  }
  return [0, 1];
}

/** Stress amplitude and mean for a peak stress and a cycle shape. */
export function ampMean(peak: number, mode: string, minPct?: number | null): { amp: number; mean: number } {
  const [lo, hi] = cycleRange(mode, minPct);
  return { amp: (Math.abs(peak) * (hi - lo)) / 2, mean: (peak * (hi + lo)) / 2 };
}

/** Goodman: the fully reversed amplitude equivalent to (σa, σm) with ultimate strength σu (no credit for a compressive mean). */
export function goodman(amp: number, mean: number, uts: number): number | null {
  if (!(uts > 0)) return null;
  const k = 1 - Math.max(0, mean) / uts;
  return k > 0 ? amp / k : null;
}

/** Basquin as the core writes it: σa = A · N^b (at most the curve's UTS). */
export function snStress(sn: Pick<SnCurve, "a_mpa" | "b" | "uts_mpa">, cycles: number): number {
  const s = sn.a_mpa * Math.max(1, cycles) ** sn.b;
  return typeof sn.uts_mpa === "number" ? Math.min(sn.uts_mpa, s) : s;
}

/** Basquin inverted: the cycles to failure at a fully reversed amplitude. */
export function snCycles(sn: Pick<SnCurve, "a_mpa" | "b">, amp: number): number | null {
  if (!(amp > 0) || !(sn.a_mpa > 0) || !(sn.b < 0)) return null;
  return Math.max(1, (amp / sn.a_mpa) ** (1 / sn.b));
}

const WORDS: [number, string][] = [[1e12, "trillion"], [1e9, "billion"], [1e6, "million"], [1e3, "thousand"]];

/** "2.5 million", "40 thousand", "350", "∞" (beyond the curve). */
export function cyclesText(n: number | null | undefined): string {
  if (n === null || n === undefined) return "beyond the curve";
  if (!Number.isFinite(n)) return "∞";
  for (const [v, w] of WORDS) if (n >= v) return `${Number((n / v).toPrecision(3))} ${w}`;
  return String(Math.round(n));
}

/** "10⁶" for an exact power of ten, else the words. */
export function cyclesShort(n: number): string {
  const e = Math.log10(n);
  if (Number.isInteger(e) && e >= 1) return `10${String(e).split("").map((d) => "⁰¹²³⁴⁵⁶⁷⁸⁹"[Number(d)]).join("")}`;
  return cyclesText(n);
}

/** Typical required lives, for the quick picks next to the field. */
export const LIFE_PRESETS: readonly { cycles: number; text: string }[] = [
  { cycles: 1e4, text: "10⁴ · occasional use" },
  { cycles: 1e6, text: "10⁶ · daily use, years" },
  { cycles: 1e7, text: "10⁷ · motor mount, hours" },
  { cycles: 1e9, text: "10⁹ · always vibrating" },
];

// ---- print direction

export const AXES = ["+x", "-x", "+y", "-y", "+z", "-z"] as const;
export type Axis = (typeof AXES)[number];
export const isAxis = (v: unknown): v is Axis => typeof v === "string" && (AXES as readonly string[]).includes(v);

/** "layers stack along +z (printed flat)" and friends. */
export function printDirectionText(v: unknown): string {
  if (v === null || v === undefined) return "isotropic (no print direction)";
  if (isAxis(v)) {
    const flat = v === "+z" || v === "-z";
    return `layers stack along ${v.replace("-", "−")}${flat ? " (printed flat)" : " (printed on its side)"}`;
  }
  if (Array.isArray(v) && v.length === 3) return `layers stack along [${v.map((x) => (typeof x === "number" ? Number(x.toPrecision(3)) : "?")).join(", ")}]`;
  return "layers: unknown direction";
}
