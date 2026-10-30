// Chart data → geometry (no DOM): clean ticks, extents, the bars of a histogram, the nearest
// point to the pointer, and which chart a small study card shows. The core sends plain series
// (sim/charts.py); every label in them is data and renders as text.
import type { StudyChart } from "../../../../api/studies";

export interface Extent { lo: number; hi: number }

/** Round tick values (1, 2, 2.5, 5 × 10^k) covering [lo, hi], about `count` of them. */
export function niceTicks(lo: number, hi: number, count = 4): number[] {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [];
  if (hi === lo) { const d = Math.abs(lo) || 1; lo -= d / 2; hi += d / 2; }
  const raw = (hi - lo) / Math.max(1, count);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  const start = Math.floor(lo / step + 1e-9) * step;
  const out: number[] = [];
  for (let v = start; v <= hi + step * 1e-6 && out.length < 50; v += step) out.push(Number(v.toPrecision(12)));
  if (out[out.length - 1] < hi - step * 1e-6) out.push(Number((out[out.length - 1] + step).toPrecision(12)));
  return out;
}

/** "1.2K", "0.05", "1e-6": compact tick text. */
export function tickText(v: number): string {
  const a = Math.abs(v);
  if (v === 0) return "0";
  if (a >= 1e6) return `${Number((v / 1e6).toPrecision(3))}M`;
  if (a >= 1e3) return `${Number((v / 1e3).toPrecision(3))}K`;
  // two significant digits: 2.5e-5 must not round to "3e-5" next to its neighbours
  if (a < 1e-3) return Number(v.toPrecision(2)).toExponential();
  return String(Number(v.toPrecision(3)));
}

export interface Bar { x0: number; x1: number; y: number; label: string; flag: boolean; index: number }
export interface Line { name: string; points: { x: number; y: number; index: number }[] }

export interface Prepared {
  kind: StudyChart["kind"];
  lines: Line[];
  bars: Bar[];
  x: Extent; y: Extent;
  /** Shaded ranges (the excitation band, "buckles" below 1). */
  bands: { label: string; axis: "x" | "y"; from: number; to: number }[];
  /** Vertical reference lines (the allowable stress, the HDT) that fall on the axis. */
  markers: { label: string; x: number }[];
  /** Reference lines too far from the data to draw without squashing it: named at that edge. */
  offscale: { label: string; x: number; side: "left" | "right" }[];
}

/** A marker within this share of the data span past either end still extends the axis to it. */
export const MARKER_REACH = 0.5;

/** Bar height in px: a non-zero count is never thinner than 2px, so one node is still visible. */
export function minBarPx(count: number, px: number): number {
  return count > 0 ? Math.max(2, px) : Math.max(0, px);
}

const finite = (v: number | null | undefined): v is number => typeof v === "number" && Number.isFinite(v);

export function prepare(c: StudyChart): Prepared {
  const bands = (c.bands ?? []).filter((b) => finite(b.from) && finite(b.to));
  const markers = (c.markers ?? []).filter((m): m is { label: string; x: number } => finite(m.x));
  if (c.kind === "histogram") {
    const edges = (c.edges ?? []).filter(finite);
    const counts = c.counts ?? [];
    const bars: Bar[] = counts.slice(0, Math.max(0, edges.length - 1)).map((n, i) => ({
      x0: edges[i], x1: edges[i + 1], y: n, index: i, flag: false, label: `${tickText(edges[i])}–${tickText(edges[i + 1])}`,
    }));
    // the axis follows the data (the bin edges); a reference line far outside it (a 35 MPa
    // allowable over 0.01 MPa of stress) is named at the edge instead of squashing the bars
    let lo = edges.length ? Math.min(...edges) : 0;
    let hi = edges.length ? Math.max(...edges) : 1;
    if (hi === lo) { const d = Math.abs(lo) * 0.5 || 1; lo -= d; hi += d; }
    const reach = (hi - lo) * MARKER_REACH;
    const onAxis = markers.filter((m) => m.x >= lo - reach && m.x <= hi + reach);
    const offscale = markers.filter((m) => !onAxis.includes(m))
      .map((m) => ({ ...m, side: m.x < lo ? "left" as const : "right" as const }));
    const xs = [lo, hi, ...onAxis.map((m) => m.x)];
    return {
      kind: c.kind, lines: [], bars, bands, markers: onAxis, offscale,
      x: { lo: Math.min(...xs), hi: Math.max(...xs) }, y: { lo: 0, hi: Math.max(1, ...counts) },
    };
  }
  const series = c.series ?? [];
  if (c.kind === "bar") {
    const s = series[0];
    const pts = (s?.points ?? []).filter((p): p is [number, number] => finite(p[1]));
    const bars = pts.map(([x, y], i) => ({ x0: x - 0.5, x1: x + 0.5, y, index: i, flag: s?.flags?.[i] ?? false, label: `${c.x.label} ${tickText(x)}` }));
    const ys = [...bars.map((b) => b.y), ...bands.filter((b) => b.axis === "y").flatMap((b) => [b.from, b.to])];
    return {
      kind: c.kind, lines: [], bars, bands, markers, offscale: [],
      x: { lo: Math.min(...bars.map((b) => b.x0), 0.5), hi: Math.max(...bars.map((b) => b.x1), 1.5) },
      y: { lo: 0, hi: Math.max(...ys, 1e-9) },
    };
  }
  const lines: Line[] = series.map((s) => ({
    name: s.name,
    points: s.points.map(([x, y], index) => ({ x, y: y as number, index })).filter((p) => finite(p.x) && finite(p.y)),
  }));
  const all = lines.flatMap((l) => l.points);
  const ys = all.map((p) => p.y);
  const xs = all.map((p) => p.x);
  const yLo = Math.min(...ys); const yHi = Math.max(...ys);
  const pad = (yHi - yLo) * 0.08 || Math.abs(yHi) * 0.1 || 1;
  return {
    kind: c.kind, lines, bars: [], bands, markers, offscale: [],
    x: { lo: Math.min(...xs), hi: Math.max(...xs) }, y: { lo: yLo - pad, hi: yHi + pad },
  };
}

/** Index into `xs` closest to x (the crosshair snaps to it). */
export function nearestIndex(xs: readonly number[], x: number): number {
  let best = 0;
  for (let i = 1; i < xs.length; i++) if (Math.abs(xs[i] - x) < Math.abs(xs[best] - x)) best = i;
  return best;
}

/** The one chart a small card shows, by what the study is about. */
export function cardChart(type: string | null | undefined, charts: readonly StudyChart[]): StudyChart | null {
  const by = (id: string) => charts.find((c) => c.id === id);
  const prefs: Record<string, string[]> = {
    wind: ["cd_vs_yaw", "cd_history"], modal: ["modal_frequencies", "convergence"], buckling: ["buckling_factors", "convergence"],
    static: ["von_mises_histogram", "convergence"], drop: ["von_mises_histogram", "convergence"], thermal: ["temperature_histogram", "convergence"],
  };
  for (const id of prefs[type ?? ""] ?? []) { const c = by(id); if (c) return c; }
  return charts[0] ?? null;
}

/** One sentence a screen reader gets for the chart. */
export function chartSummary(c: StudyChart): string {
  const p = prepare(c);
  if (p.bars.length) {
    const top = p.bars.reduce((a, b) => (b.y > a.y ? b : a), p.bars[0]);
    return `${c.title}: ${p.bars.length} ${c.kind === "histogram" ? "bins" : "bars"}, highest ${tickText(top.y)} at ${top.label}.`;
  }
  const bits = p.lines.map((l) => { const last = l.points[l.points.length - 1]; return last ? `${l.name} ends at ${tickText(last.y)}` : l.name; });
  return `${c.title}: ${bits.join("; ")}.`;
}
