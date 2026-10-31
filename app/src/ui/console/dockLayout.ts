// The agent dock's layout (Plan 9 D4 / D20): its width (360–640 px, ≈440 by default), the heights
// of the View and Simulations streams (the Conversation takes the rest) and which streams are
// collapsed. A per-viewer convenience kept in localStorage — it may be unavailable, and then the
// layout just isn't remembered. The math is pure; the signal and storage wrap it.
import { signal, type Signal } from "@preact/signals";
import type { StreamKind } from "../../api/types";

export interface DockLayout {
  width: number;
  /** Heights (px) of the two streams above the Conversation. */
  view: number;
  sim: number;
  collapsed: Record<StreamKind, boolean>;
}

export const LAYOUT_KEY = "mises.dock.layout";
export const WIDTH_MIN = 360;
export const WIDTH_MAX = 640;
/** Smallest height of an open stream's body, and what the Conversation always keeps. */
export const STREAM_MIN = 96;
export const TEXT_MIN = 200;

export const DEFAULT_LAYOUT: DockLayout = { width: 440, view: 300, sim: 180, collapsed: { view: false, sim: false, text: false } };

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const num = (v: unknown, d: number) => (typeof v === "number" && Number.isFinite(v) ? v : d);

/** A stored layout read defensively: bad or missing fields take the default. */
export function parseLayout(raw: unknown): DockLayout {
  const r = typeof raw === "object" && raw !== null ? (raw as Record<string, unknown>) : {};
  const c = typeof r.collapsed === "object" && r.collapsed !== null ? (r.collapsed as Record<string, unknown>) : {};
  return {
    width: clamp(Math.round(num(r.width, DEFAULT_LAYOUT.width)), WIDTH_MIN, WIDTH_MAX),
    view: Math.max(STREAM_MIN, Math.round(num(r.view, DEFAULT_LAYOUT.view))),
    sim: Math.max(STREAM_MIN, Math.round(num(r.sim, DEFAULT_LAYOUT.sim))),
    collapsed: { view: c.view === true, sim: c.sim === true, text: c.text === true },
  };
}

/** Drag the divider under `which` by `dy` px. The stream grows or shrinks, never below
 *  STREAM_MIN, and never so far that the Conversation (of a dock `total` px tall) drops below
 *  TEXT_MIN. */
export function resizeStream(l: DockLayout, which: "view" | "sim", dy: number, total: number): DockLayout {
  const other = which === "view" ? l.sim : l.view;
  const max = Math.max(STREAM_MIN, total - TEXT_MIN - other);
  return { ...l, [which]: Math.round(clamp(l[which] + dy, STREAM_MIN, max)) };
}

/** Drag the dock's left edge by `dx` px (left is wider). */
export function resizeWidth(l: DockLayout, dx: number): DockLayout {
  return { ...l, width: Math.round(clamp(l.width - dx, WIDTH_MIN, WIDTH_MAX)) };
}

export function toggleStream(l: DockLayout, s: StreamKind): DockLayout {
  return { ...l, collapsed: { ...l.collapsed, [s]: !l.collapsed[s] } };
}

function read(): DockLayout {
  try {
    const s = globalThis.localStorage?.getItem(LAYOUT_KEY);
    return s ? parseLayout(JSON.parse(s)) : DEFAULT_LAYOUT;
  } catch {
    return DEFAULT_LAYOUT;
  }
}

export const dockLayout: Signal<DockLayout> = signal<DockLayout>(read());

/** Show a layout; `save: false` while a drag is in progress (saved once at its end). */
export function setDockLayout(l: DockLayout, save = true): void {
  dockLayout.value = l;
  if (!save) return;
  try { globalThis.localStorage?.setItem(LAYOUT_KEY, JSON.stringify(l)); } catch { /* not remembered */ }
}

/** Tests: re-read storage. */
export function resetDockLayout(): void { dockLayout.value = read(); }
