// The agent dock's layout (P11 F2): the Conversation on top, full width; View and Simulations
// underneath, side by side, 50/50. Hovering View or Simulations widens it to ~90% of the dock
// (the other keeps ~10%, never 0) and leaving restores 50/50 (`rowGrow`). Each stream opens or
// closes on its own: a closed Conversation is a slim bar at the top, a closed View or
// Simulations a slim vertical tab in the row (both closed: a slim bar of two tabs); at least one
// stream stays open. The dock has one width (dragged at its left edge) and the Conversation's
// share of the height (dragged at the bar between it and the row). The model always keeps
// VIEWPORT_MIN (base.css --console-w; ui/shell/room.ts also counts the Parts surface). A
// per-viewer convenience kept in localStorage — it may be unavailable, and then the layout just
// isn't remembered. The math is pure; the signal and storage wrap it.
import { signal, type Signal } from "@preact/signals";
import type { StreamKind } from "../../api/types";

export const STREAMS: readonly StreamKind[] = ["view", "sim", "text"];
/** The row under the Conversation, in order. */
export const ROW: readonly StreamKind[] = ["view", "sim"];

export interface DockLayout {
  collapsed: Record<StreamKind, boolean>;
  /** The dock's width (px). */
  width: number;
  /** The Conversation's share of the dock's height while it and the row are both open. */
  split: number;
}

/** P11 F2 (was "mises.dock.columns": three side-by-side columns; an old record is not read). */
export const LAYOUT_KEY = "mises.dock.stack";
export const DEFAULT_WIDTH = 560;
export const WIDTH_MIN = 360;
export const WIDTH_MAX = 1800;
export const SPLIT_DEFAULT = 0.56;
export const SPLIT_MIN = 0.25;
export const SPLIT_MAX = 0.8;
/** A closed stream's slim tab (a bar's height, a vertical tab's width). */
export const TAB_W = 30;
/** Hover: the hovered half's flex-grow against the other's 1 (~90% / ~10%). */
export const HOVER_GROW = 9;
/** The model keeps at least this much of the window (the CSS cap; drags stop there too). */
export const VIEWPORT_MIN = 520;

export const DEFAULT_LAYOUT: DockLayout = {
  collapsed: { view: false, sim: false, text: false },
  width: DEFAULT_WIDTH,
  split: SPLIT_DEFAULT,
};

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const num = (v: unknown, d: number) => (typeof v === "number" && Number.isFinite(v) ? v : d);

/** The open streams, in order. */
export function openStreams(l: DockLayout): StreamKind[] { return STREAMS.filter((s) => !l.collapsed[s]); }

/** The row's open streams (View, Simulations). */
export function openRow(l: DockLayout): StreamKind[] { return ROW.filter((s) => !l.collapsed[s]); }

/** Smallest dock width (any layout). */
export const minWidth = (): number => WIDTH_MIN;

/** The dock's width (px, before the CSS cap). */
export function dockWidth(l: DockLayout): number { return l.width; }

/** Each row stream's flex-grow: 50/50, or the hovered one ~90% while both are open. */
export function rowGrow(l: DockLayout, hover: StreamKind | null): Record<"view" | "sim", number> {
  const both = openRow(l).length === 2;
  if (!both || (hover !== "view" && hover !== "sim")) return { view: 1, sim: 1 };
  return hover === "view" ? { view: HOVER_GROW, sim: 1 } : { view: 1, sim: HOVER_GROW };
}

/** A stored layout read defensively: bad or missing fields take the default. */
export function parseLayout(raw: unknown): DockLayout {
  const r = typeof raw === "object" && raw !== null ? (raw as Record<string, unknown>) : {};
  const c = typeof r.collapsed === "object" && r.collapsed !== null ? (r.collapsed as Record<string, unknown>) : {};
  const collapsed = {
    view: typeof c.view === "boolean" ? c.view : DEFAULT_LAYOUT.collapsed.view,
    sim: typeof c.sim === "boolean" ? c.sim : DEFAULT_LAYOUT.collapsed.sim,
    text: typeof c.text === "boolean" ? c.text : DEFAULT_LAYOUT.collapsed.text,
  };
  // never all closed: the Conversation comes back
  if (collapsed.view && collapsed.sim && collapsed.text) collapsed.text = false;
  return {
    collapsed,
    width: clamp(Math.round(num(r.width, DEFAULT_WIDTH)), WIDTH_MIN, WIDTH_MAX),
    split: clamp(num(r.split, SPLIT_DEFAULT), SPLIT_MIN, SPLIT_MAX),
  };
}

/** Open or close a stream. The last open stream stays open. */
export function toggleStream(l: DockLayout, s: StreamKind): DockLayout {
  if (!l.collapsed[s] && openStreams(l).length === 1) return l;
  return { ...l, collapsed: { ...l.collapsed, [s]: !l.collapsed[s] } };
}

/** Drag the dock's left edge by `dx` px (left is wider), never past `max` (the window less the
 *  model's room). */
export function resizeWidth(l: DockLayout, dx: number, max: number = WIDTH_MAX): DockLayout {
  const hi = Math.max(WIDTH_MIN, Math.min(WIDTH_MAX, max));
  return { ...l, width: Math.round(clamp(l.width - dx, WIDTH_MIN, hi)) };
}

/** Drag the bar between the Conversation and the row by `dy` px (down: a taller Conversation)
 *  in a dock `total` px tall. */
export function resizeSplit(l: DockLayout, dy: number, total: number): DockLayout {
  if (total <= 0) return l;
  return { ...l, split: Math.round(clamp(l.split + dy / total, SPLIT_MIN, SPLIT_MAX) * 1000) / 1000 };
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

/** Tests. */
export function resetDockLayout(): void { dockLayout.value = read(); }
