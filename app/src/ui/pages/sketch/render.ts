// A standalone picture of the sketch (black on white, mm grid, dimension labels) for vision-capable
// models: built as an SVG string from the model (never from the DOM), rasterised to PNG in the
// webview. Every piece of text is XML-escaped.
import { labelAnchor, SHEET, toSvg, type Dimension, type SketchDoc, type Stroke } from "./model";

export const RENDER_PX = { width: 1600, height: 1200 } as const;
const RENDER_TIMEOUT_MS = 3000;

export function escapeXml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;" })[c] as string);
}

const fmt = (v: number) => String(Math.round(v * 100) / 100);

export function strokePoints(s: Stroke): string {
  return s.points.map((p) => toSvg(p).map(fmt).join(",")).join(" ");
}

/** The label shown for a stroke: its dimension text, then its own label. */
export function strokeCaption(s: Stroke, dims: readonly Dimension[]): string {
  const d = dims.find((x) => x.stroke_id === s.id);
  return [d?.text, s.label].filter(Boolean).join(" · ");
}

export function sketchSvg(doc: SketchDoc): string {
  const W = SHEET.width_mm;
  const H = SHEET.height_mm;
  const grid: string[] = [];
  for (let x = 10; x < W; x += 10) grid.push(`<line x1="${x}" y1="0" x2="${x}" y2="${H}"/>`);
  for (let y = 10; y < H; y += 10) grid.push(`<line x1="0" y1="${y}" x2="${W}" y2="${y}"/>`);
  const shapes = doc.strokes.map((s) => {
    const tag = s.closed ? "polygon" : "polyline";
    return `<${tag} points="${strokePoints(s)}"/>`;
  });
  const labels = doc.strokes.flatMap((s) => {
    const text = strokeCaption(s, doc.dimensions);
    if (!text) return [];
    const [x, y] = toSvg(labelAnchor(s));
    return [`<text x="${fmt(x)}" y="${fmt(y - 2)}">${escapeXml(text)}</text>`];
  });
  return [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${RENDER_PX.width}" height="${RENDER_PX.height}" viewBox="0 0 ${W} ${H}">`,
    `<rect width="${W}" height="${H}" fill="#ffffff"/>`,
    `<g stroke="#d0d0d0" stroke-width="0.15">${grid.join("")}</g>`,
    `<g fill="none" stroke="#000000" stroke-width="0.6" stroke-linejoin="round" stroke-linecap="round">${shapes.join("")}</g>`,
    `<g fill="#000000" font-family="monospace" font-size="4" text-anchor="middle">${labels.join("")}</g>`,
    `</svg>`,
  ].join("");
}

/** PNG (base64, no data: prefix) of the sketch, or null when the webview can't rasterise. */
export async function renderPng(doc: SketchDoc): Promise<string | null> {
  if (typeof document === "undefined" || typeof Image === "undefined" || typeof URL.createObjectURL !== "function") return null;
  const canvas = document.createElement("canvas");
  let ctx: CanvasRenderingContext2D | null = null;
  try { ctx = canvas.getContext("2d"); } catch { /* no canvas */ }
  if (!ctx) return null;
  const url = URL.createObjectURL(new Blob([sketchSvg(doc)], { type: "image/svg+xml" }));
  try {
    const img = new Image();
    await new Promise<void>((ok, fail) => {
      const timer = setTimeout(() => fail(new Error("render timed out")), RENDER_TIMEOUT_MS);
      img.onload = () => { clearTimeout(timer); ok(); };
      img.onerror = () => { clearTimeout(timer); fail(new Error("render failed")); };
      img.src = url;
    });
    canvas.width = RENDER_PX.width;
    canvas.height = RENDER_PX.height;
    ctx.drawImage(img, 0, 0, RENDER_PX.width, RENDER_PX.height);
    const data = canvas.toDataURL("image/png");
    const i = data.indexOf(",");
    return data.startsWith("data:image/png") && i > 0 ? data.slice(i + 1) : null;
  } catch {
    return null;
  } finally {
    URL.revokeObjectURL(url);
  }
}
