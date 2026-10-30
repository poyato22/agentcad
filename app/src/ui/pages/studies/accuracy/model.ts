// Plan 6 pure logic (no DOM, no core): what each calibration test implies on its own (the
// modulus or strength the table shows as you type, before the core's least-squares fit), the
// fatigue cycle shapes and Basquin / Goodman arithmetic the S-N chart draws, cycle counts as
// words, and print directions. The core's numbers always win; these only preview and draw.
import type { CalibrationTest, Confidence, SnCurve } from "../../../../api/accuracy";

const pos = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v > 0;

export interface Implied { e_mpa: number | null; strength_mpa: number | null }

/**
 * One test's own reading. Tensile: strength = F / (b h). Three-point bend: E = F L³ / (4 b h³ δ)
 * (δ = F L³ / 48 E I with I = b h³ / 12). A part load test needs the study's own deflection to
 * compare against, so only the core can turn it into a modulus.
 */
export function implied(t: CalibrationTest): Implied {
  if (t.kind === "tensile") {
    if (!pos(t.break_n) || !pos(t.width_mm) || !pos(t.thickness_mm)) return { e_mpa: null, strength_mpa: null };
    const strength = t.break_n / (t.width_mm * t.thickness_mm);
    // with an elongation at break, a rough secant modulus (brittle prints: close to E)
    const e = pos(t.elongation_pct) ? strength / (t.elongation_pct / 100) : null;
    return { e_mpa: e, strength_mpa: strength };
  }
  if (t.kind === "bend") {
    if (!pos(t.load_n) || !pos(t.span_mm) || !pos(t.width_mm) || !pos(t.thickness_mm) || !pos(t.deflection_mm)) return { e_mpa: null, strength_mpa: null };
    return { e_mpa: (t.load_n * t.span_mm ** 3) / (4 * t.width_mm * t.thickness_mm ** 3 * t.deflection_mm), strength_mpa: null };
  }
  return { e_mpa: null, strength_mpa: null };
}

/** Mean and coefficient of variation (sample, %) of the finite values; null when there are none. */
export function meanCv(xs: readonly (number | null | undefined)[]): { mean: number; cvPct: number | null; n: number } | null {
  const v = xs.filter((x): x is number => typeof x === "number" && Number.isFinite(x));
  if (!v.length) return null;
  const mean = v.reduce((a, b) => a + b, 0) / v.length;
  if (v.length < 2 || mean === 0) return { mean, cvPct: null, n: v.length };
  const sd = Math.sqrt(v.reduce((a, b) => a + (b - mean) ** 2, 0) / (v.length - 1));
  return { mean, cvPct: (sd / Math.abs(mean)) * 100, n: v.length };
}

/** A fit's confidence from its test count and scatter (the core reports its own; this is the rule the fake uses). */
export function confidenceOf(n: number, cvPct: number | null): Confidence {
  if (n >= 5 && cvPct !== null && cvPct <= 8) return "high";
  if (n >= 3 && (cvPct === null || cvPct <= 15)) return "medium";
  return "low";
}

export const CONFIDENCE_TEXT: Record<Confidence, string> = {
  low: "low confidence: add tests",
  medium: "medium confidence",
  high: "high confidence",
};

export const TEST_TEXT: Record<CalibrationTest["kind"], string> = {
  tensile: "tensile bar", bend: "3-point bend", part_load: "part load test",
};

// ---- fatigue

export type CycleMode = "fully_reversed" | "zero_to_max" | "min_max";

/** The load over one cycle as fractions of the case's load: [min, max]. R = min / max. */
export function cycleRange(mode: string, minRatio: number | null | undefined): [number, number] {
  if (mode === "zero_to_max") return [0, 1];
  if (mode === "min_max") {
    const r = typeof minRatio === "number" && Number.isFinite(minRatio) ? Math.max(-1, Math.min(1, minRatio)) : 0;
    return [r, 1];
  }
  return [-1, 1];
}

/** Stress amplitude and mean for a peak stress and a cycle shape. */
export function ampMean(peak: number, mode: string, minRatio?: number | null): { amp: number; mean: number } {
  const [lo, hi] = cycleRange(mode, minRatio);
  return { amp: (peak * (hi - lo)) / 2, mean: (peak * (hi + lo)) / 2 };
}

/** Goodman: the fully reversed amplitude equivalent to (σa, σm) with ultimate strength σu. */
export function goodman(amp: number, mean: number, uts: number): number | null {
  if (!(uts > 0)) return null;
  const k = 1 - Math.max(0, mean) / uts;
  return k > 0 ? amp / k : null;
}

/** Basquin: σa = sf (2N)^b → the stress amplitude at N cycles. */
export function snStress(sn: Pick<SnCurve, "sf_mpa" | "b">, cycles: number): number {
  return sn.sf_mpa * (2 * cycles) ** sn.b;
}

/** Basquin inverted: the cycles to failure at a fully reversed amplitude. */
export function snCycles(sn: Pick<SnCurve, "sf_mpa" | "b">, amp: number): number | null {
  if (!(amp > 0) || !(sn.sf_mpa > 0) || !(sn.b < 0)) return null;
  return 0.5 * (amp / sn.sf_mpa) ** (1 / sn.b);
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
