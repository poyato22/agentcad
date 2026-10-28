// Pan/zoom math for the drawing view. The sheet is laid out at its size in CSS px (sheet mm ×
// SHEET_PX_PER_MM) and shown with `translate(x, y) scale(scale)` from the top-left corner.
export interface View { scale: number; x: number; y: number }
export interface Size { w: number; h: number }

export const MIN_SCALE = 0.05;
export const MAX_SCALE = 40;
/** CSS px per sheet mm at scale 1 (A4 landscape: 1188 × 840 px). */
export const SHEET_PX_PER_MM = 4;

export function clampScale(s: number): number {
  if (!Number.isFinite(s)) return 1;
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, s));
}

/** The whole sheet, centred, inside `pad` px of the viewport. */
export function fitView(viewport: Size, content: Size, pad = 16): View {
  const w = viewport.w - 2 * pad;
  const h = viewport.h - 2 * pad;
  if (w <= 0 || h <= 0 || content.w <= 0 || content.h <= 0) return { scale: 1, x: 0, y: 0 };
  const scale = clampScale(Math.min(w / content.w, h / content.h));
  return { scale, x: (viewport.w - content.w * scale) / 2, y: (viewport.h - content.h * scale) / 2 };
}

/** Zoom by `factor` keeping the sheet point under (px, py) (viewport px) where it is. */
export function zoomAt(v: View, factor: number, px: number, py: number): View {
  const scale = clampScale(v.scale * factor);
  if (scale === v.scale) return v;
  const k = scale / v.scale;
  return { scale, x: px - (px - v.x) * k, y: py - (py - v.y) * k };
}

export function panBy(v: View, dx: number, dy: number): View {
  return { scale: v.scale, x: v.x + dx, y: v.y + dy };
}

/** "100%" = the fitted view. */
export function zoomPercent(v: View, fit: View): string {
  return `${Math.round((v.scale / fit.scale) * 100)}%`;
}
