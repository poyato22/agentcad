// The agent dock's layout (P10 E6): View | Simulations | Conversation side by side as columns,
// each open or closed on its own (a closed one is a slim vertical tab). The open columns share the
// dock's width by their `share` (the vertical dividers between them move share from one to the
// next); the dock's width depends on how many are open — one width per count, dragged at its left
// edge (defaults 440 / 760 / 1080), plus a slim tab per closed column — and CSS caps it so the
// model always keeps 360 px (base.css --console-w). At least one column stays open. A per-viewer
// convenience kept in localStorage — it may be unavailable, and then the layout just isn't
// remembered. The math is pure; the signal and storage wrap it.
import { signal, type Signal } from "@preact/signals";
import type { StreamKind } from "../../api/types";

export const STREAMS: readonly StreamKind[] = ["view", "sim", "text"];

export interface DockLayout {
  collapsed: Record<StreamKind, boolean>;
  /** The open columns' total width (px) for 1, 2 and 3 open columns. */
  widths: [number, number, number];
  /** Each column's relative share of that width while open. */
  share: Record<StreamKind, number>;
}

/** P10 E6 (was "mises.dock.layout": stacked streams; an old record is not read). */
export const LAYOUT_KEY = "mises.dock.columns";
/** The defaults per number of open columns. */
export const DEFAULT_WIDTHS: readonly [number, number, number] = [440, 760, 1080];
/** One column is never narrower than this; one open column never narrower than WIDTH_MIN. */
export const COL_MIN = 260;
export const WIDTH_MIN = 360;
export const WIDTH_MAX = 1800;
/** A closed column's slim tab. */
export const TAB_W = 30;
/** The model keeps at least this much of the window (the CSS cap; drags stop there too). */
export const VIEWPORT_MIN = 360;

export const DEFAULT_LAYOUT: DockLayout = {
  // the Conversation on its own (the dock's old footprint); View and Simulations wait as tabs
  // with their unread counts until opened
  collapsed: { view: true, sim: true, text: false },
  widths: [...DEFAULT_WIDTHS],
  share: { view: 1, sim: 1, text: 1 },
};

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const num = (v: unknown, d: number) => (typeof v === "number" && Number.isFinite(v) ? v : d);

/** The open columns, in order. */
export function openStreams(l: DockLayout): StreamKind[] { return STREAMS.filter((s) => !l.collapsed[s]); }

/** Smallest width for `n` open columns. */
export const minWidth = (n: number): number => Math.max(WIDTH_MIN, n * COL_MIN);

/** The dock's width (px): its open columns plus a tab per closed one (before the CSS cap). */
export function dockWidth(l: DockLayout): number {
  const n = openStreams(l).length;
  return (n ? l.widths[n - 1] : 0) + (STREAMS.length - n) * TAB_W;
}

/** A stored layout read defensively: bad or missing fields take the default. */
export function parseLayout(raw: unknown): DockLayout {
  const r = typeof raw === "object" && raw !== null ? (raw as Record<string, unknown>) : {};
  const c = typeof r.collapsed === "object" && r.collapsed !== null ? (r.collapsed as Record<string, unknown>) : {};
  const sh = typeof r.share === "object" && r.share !== null ? (r.share as Record<string, unknown>) : {};
  const w = Array.isArray(r.widths) ? r.widths : [];
  const collapsed = {
    view: typeof c.view === "boolean" ? c.view : DEFAULT_LAYOUT.collapsed.view,
    sim: typeof c.sim === "boolean" ? c.sim : DEFAULT_LAYOUT.collapsed.sim,
    text: typeof c.text === "boolean" ? c.text : DEFAULT_LAYOUT.collapsed.text,
  };
  // never all closed: the Conversation comes back
  if (collapsed.view && collapsed.sim && collapsed.text) collapsed.text = false;
  const share = (s: StreamKind) => clamp(num(sh[s], 1), 0.2, 5);
  return {
    collapsed,
    widths: [0, 1, 2].map((i) => clamp(Math.round(num(w[i], DEFAULT_WIDTHS[i])), minWidth(i + 1), WIDTH_MAX)) as [number, number, number],
    share: { view: share("view"), sim: share("sim"), text: share("text") },
  };
}

/** Open or close a column. The last open column stays open. */
export function toggleStream(l: DockLayout, s: StreamKind): DockLayout {
  if (!l.collapsed[s] && openStreams(l).length === 1) return l;
  return { ...l, collapsed: { ...l.collapsed, [s]: !l.collapsed[s] } };
}

/** Drag the dock's left edge by `dx` px (left is wider): the width for the current number of
 *  open columns, never past `max` (the window less the model's room). */
export function resizeWidth(l: DockLayout, dx: number, max: number = WIDTH_MAX): DockLayout {
  const n = openStreams(l).length;
  if (!n) return l;
  const i = n - 1;
  const hi = Math.max(minWidth(n), Math.min(WIDTH_MAX, max));
  const widths = [...l.widths] as [number, number, number];
  widths[i] = Math.round(clamp(l.widths[i] - dx, minWidth(n), hi));
  return { ...l, widths };
}

/** Drag the divider between open columns `a` (left) and `b` (right) by `dx` px, with the open
 *  columns `total` px wide: `a` grows by dx and `b` shrinks, neither below COL_MIN. */
export function resizeColumns(l: DockLayout, a: StreamKind, b: StreamKind, dx: number, total: number): DockLayout {
  const open = openStreams(l);
  if (!open.includes(a) || !open.includes(b) || total <= 0) return l;
  const sum = open.reduce((t, s) => t + l.share[s], 0);
  const px = (s: StreamKind) => (l.share[s] / sum) * total;
  const pair = px(a) + px(b);
  const lo = Math.min(COL_MIN, pair / 2);
  const na = clamp(px(a) + dx, lo, pair - lo);
  const round = (v: number) => Math.round(v * 1000) / 1000;
  return { ...l, share: { ...l.share, [a]: round((na / total) * sum), [b]: round(((pair - na) / total) * sum) } };
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
