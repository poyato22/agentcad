// The agent dock's layout (G1, replacing P11 F2's 50/50 row and its hover-expand): the top area
// shows ONE of View or Simulations at a time (`active`, switched by the header's segmented
// control), the Conversation sits under it, and a draggable horizontal divider trades their
// heights (`split`: the top area's share). Either part can be closed to a slim bar (never both).
// The dock has one width (dragged at its left edge). The model always keeps VIEWPORT_MIN
// (base.css --console-w; ui/shell/room.ts also counts the Parts surface). A per-viewer
// convenience kept in localStorage — it may be unavailable, and then the layout just isn't
// remembered. The math is pure; the signal and storage wrap it.
import { signal, type Signal } from "@preact/signals";
import type { StreamKind } from "../../api/types";

export type TopStream = "view" | "sim";
export const TOP: readonly TopStream[] = ["view", "sim"];

export interface DockLayout {
  /** The stream the top area shows. */
  active: TopStream;
  /** `top`: the View / Simulations area; `text`: the Conversation. */
  collapsed: { top: boolean; text: boolean };
  /** The dock's width (px). */
  width: number;
  /** The top area's share of the dock's height while it and the Conversation are both open. */
  split: number;
}

/** The stored record (P11 F2's key; its {view, sim, text} collapsed shape is still read). */
export const LAYOUT_KEY = "mises.dock.stack";
export const DEFAULT_WIDTH = 560;
export const WIDTH_MIN = 360;
export const WIDTH_MAX = 1800;
export const SPLIT_DEFAULT = 0.5;
export const SPLIT_MIN = 0.2;
export const SPLIT_MAX = 0.8;
/** A closed part's slim bar (its height). */
export const TAB_W = 30;
/** The model keeps at least this much of the window (the CSS cap; drags stop there too). */
export const VIEWPORT_MIN = 520;

export const DEFAULT_LAYOUT: DockLayout = {
  active: "view",
  collapsed: { top: false, text: false },
  width: DEFAULT_WIDTH,
  split: SPLIT_DEFAULT,
};

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const num = (v: unknown, d: number) => (typeof v === "number" && Number.isFinite(v) ? v : d);
const bool = (v: unknown, d: boolean) => (typeof v === "boolean" ? v : d);

/** The open streams, in order (the top area counts as its active stream). */
export function openStreams(l: DockLayout): StreamKind[] {
  return [...(l.collapsed.top ? [] : [l.active]), ...(l.collapsed.text ? [] : ["text" as const])];
}

/** Smallest dock width (any layout). */
export const minWidth = (): number => WIDTH_MIN;

/** The dock's width (px, before the CSS cap). */
export function dockWidth(l: DockLayout): number { return l.width; }

/** A stored layout read defensively: bad or missing fields take the default. P11 F2's
 *  {collapsed: {view, sim, text}} reads as: the top area open when either stream was, showing
 *  View unless only Simulations was open. */
export function parseLayout(raw: unknown): DockLayout {
  const r = typeof raw === "object" && raw !== null ? (raw as Record<string, unknown>) : {};
  const c = typeof r.collapsed === "object" && r.collapsed !== null ? (r.collapsed as Record<string, unknown>) : {};
  const legacy = "view" in c || "sim" in c;
  const viewOpen = !bool(c.view, false); const simOpen = !bool(c.sim, false);
  const top = legacy && !("top" in c) ? !(viewOpen || simOpen) : bool(c.top, DEFAULT_LAYOUT.collapsed.top);
  let active: TopStream = r.active === "sim" || r.active === "view" ? r.active : DEFAULT_LAYOUT.active;
  if (!(r.active === "sim" || r.active === "view") && legacy && !viewOpen && simOpen) active = "sim";
  const collapsed = { top, text: bool(c.text, DEFAULT_LAYOUT.collapsed.text) };
  if (collapsed.top && collapsed.text) collapsed.text = false;          // never both closed
  return {
    active, collapsed,
    width: clamp(Math.round(num(r.width, DEFAULT_WIDTH)), WIDTH_MIN, WIDTH_MAX),
    split: clamp(num(r.split, SPLIT_DEFAULT), SPLIT_MIN, SPLIT_MAX),
  };
}

/** Show `s` in the top area (opening it when it was closed). */
export function showStream(l: DockLayout, s: TopStream): DockLayout {
  return { ...l, active: s, collapsed: { ...l.collapsed, top: false } };
}

/** Open or close the top area or the Conversation. The last open part stays open. */
export function togglePart(l: DockLayout, part: "top" | "text"): DockLayout {
  const other = part === "top" ? "text" : "top";
  if (!l.collapsed[part] && l.collapsed[other]) return l;
  return { ...l, collapsed: { ...l.collapsed, [part]: !l.collapsed[part] } };
}

/** Drag the dock's left edge by `dx` px (left is wider), never past `max` (the window less the
 *  model's room). */
export function resizeWidth(l: DockLayout, dx: number, max: number = WIDTH_MAX): DockLayout {
  const hi = Math.max(WIDTH_MIN, Math.min(WIDTH_MAX, max));
  return { ...l, width: Math.round(clamp(l.width - dx, WIDTH_MIN, hi)) };
}

/** Drag the divider between the top area and the Conversation by `dy` px (down: a taller top
 *  area) in a dock `total` px tall. */
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
