// The canvas view (pure): which part of the sheet is shown (zoom / pan, mm, +y up), the
// screen ↔ mm mapping for an SVG laid out with preserveAspectRatio="xMidYMin meet", the adaptive
// grid spacing and the ruler ticks.
import { SHEET, type Box } from "./model";
import type { Pt } from "./geom";

/** The shown window in sheet mm: bottom-left corner (x, y) and size. */
export interface View { x: number; y: number; w: number; h: number }

/** The whole sheet with a margin for the origin, the axes and the rulers. */
export const DEFAULT_VIEW: View = { x: -12, y: -10, w: SHEET.width_mm + 22, h: SHEET.height_mm + 20 };
const MIN_W = 10;
const MAX_W = SHEET.width_mm * 4;

/** SVG viewBox (y down: SVG y = sheet height − mm y). */
export function viewBox(v: View): string {
  return `${v.x} ${SHEET.height_mm - (v.y + v.h)} ${v.w} ${v.h}`;
}

export function placement(rect: Box, v: View): { scale: number; x0: number; y0: number } {
  const scale = Math.min(rect.width / v.w, rect.height / v.h);
  return { scale, x0: rect.left + (rect.width - v.w * scale) / 2, y0: rect.top };
}

/** Screen (client) px → sheet mm (not clamped). */
export function screenToMm(cx: number, cy: number, rect: Box, v: View): Pt {
  const { scale, x0, y0 } = placement(rect, v);
  if (scale <= 0) return [v.x, v.y];
  return [v.x + (cx - x0) / scale, v.y + v.h - (cy - y0) / scale];
}

export function mmToScreen(p: Pt, rect: Box, v: View): Pt {
  const { scale, x0, y0 } = placement(rect, v);
  return [x0 + (p[0] - v.x) * scale, y0 + (v.y + v.h - p[1]) * scale];
}

/** What is actually visible (the box may be wider or taller than the view's aspect). */
export function visible(rect: Box, v: View): View {
  const { scale } = placement(rect, v);
  if (scale <= 0) return v;
  const w = rect.width / scale;
  const h = rect.height / scale;
  return { x: v.x - (w - v.w) / 2, y: v.y + v.h - h, w, h };
}

/** Zoom by `factor` (> 1 zooms in) keeping the mm point `at` under the pointer. */
export function zoomAt(v: View, at: Pt, factor: number): View {
  const w = Math.min(MAX_W, Math.max(MIN_W, v.w / factor));
  const k = w / v.w;
  const h = v.h * k;
  return { x: at[0] - (at[0] - v.x) * k, y: at[1] - (at[1] - v.y) * k, w, h };
}

export function pan(v: View, dxMm: number, dyMm: number): View {
  return { ...v, x: v.x + dxMm, y: v.y + dyMm };
}

/** View that fits the box [x0, y0, x1, y1] (mm) with a margin, same aspect as `like`. */
export function fitBox(b: [number, number, number, number], like: View, margin = 0.12): View {
  const bw = Math.max(b[2] - b[0], 10);
  const bh = Math.max(b[3] - b[1], 10);
  const aspect = like.w / like.h;
  const w = Math.min(MAX_W, Math.max(MIN_W, Math.max(bw * (1 + 2 * margin), bh * (1 + 2 * margin) * aspect)));
  const h = w / aspect;
  return { x: (b[0] + b[2]) / 2 - w / 2, y: (b[1] + b[3]) / 2 - h / 2, w, h };
}

const STEPS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100] as const;

/** Grid spacing for the zoom: minor lines at least `minPx` apart, major lines on a power of ten. */
export function gridSpacing(pxPerMm: number, minPx = 8): { minor: number; major: number } {
  const minor = STEPS.find((s) => s * pxPerMm >= minPx) ?? 100;
  const decade = 10 ** Math.floor(Math.log10(minor) + 1e-9);
  const lead = Math.round(minor / decade);
  return { minor, major: lead === 1 ? minor * 10 : lead === 2 ? minor * 5 : minor * 2 };
}

/** Grid line positions in [lo, hi] on multiples of step (rounded to cut float noise). */
export function gridLines(lo: number, hi: number, step: number, limit = 400): number[] {
  const out: number[] = [];
  const start = Math.ceil(lo / step - 1e-9);
  for (let k = start; k * step <= hi + 1e-9 && out.length < limit; k++) out.push(Math.round(k * step * 1e6) / 1e6 + 0);
  return out;
}

/** Labelled ruler ticks: at least `minPx` between labels. */
export function rulerTicks(lo: number, hi: number, pxPerMm: number, minPx = 48): { at: number; label: string }[] {
  const step = STEPS.find((s) => s * pxPerMm >= minPx) ?? 100;
  return gridLines(lo, hi, step).map((at) => ({ at, label: String(at) }));
}
