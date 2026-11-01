// How much of the build view the floating surfaces cover (FW-layout, D20). The viewport is
// full-bleed under the top bar; the Parts surface floats over its left edge and the agent dock over
// its right. The 3D views centre the model in what is left (assembly/scene.ts, viewer/scene.ts
// shift their frustum by these insets), so folding a surface slides the model to the new centre.
import { signal, type Signal } from "@preact/signals";

export interface ViewInsets {
  /** CSS px of the viewer covered from its left edge (the Parts surface and its gap). */
  left: number;
  /** CSS px covered from its right edge (the agent dock and its gap). */
  right: number;
}

export const NO_INSETS: ViewInsets = { left: 0, right: 0 };

export const viewInsets: Signal<ViewInsets> = signal<ViewInsets>(NO_INSETS);

/** The insets of `viewer` from the floating `left` / `right` surfaces (null when not shown). A
 *  surface counts up to its far edge plus the same gap it keeps from the window edge; one that
 *  doesn't overlap the viewer counts as 0. Pure (rects in). */
export function insetsOf(viewer: DOMRect, left: DOMRect | null, right: DOMRect | null): ViewInsets {
  const l = left && left.width > 0 ? Math.max(0, left.right + (left.left - viewer.left) - viewer.left) : 0;
  const r = right && right.width > 0 ? Math.max(0, viewer.right - right.left + (viewer.right - right.right)) : 0;
  const cap = Math.max(0, viewer.width - 120);   // never leave the model less than 120px
  const clamp = (v: number) => Math.round(Math.min(v, cap));
  return l + r > cap ? { left: clamp((l * cap) / (l + r)), right: clamp((r * cap) / (l + r)) } : { left: clamp(l), right: clamp(r) };
}

/** The open surface is its region; a folded one is the tab inside it. */
const LEFT = "[data-region=left][data-surface], [data-region=left] [data-surface]";
const RIGHT = "[data-region=console][data-surface], [data-region=console] [data-surface]";

const WATCHED = ["[data-region=viewer]", LEFT, RIGHT] as const;

/** Measures the insets inside `app` now and whenever it or a surface resizes. Plan 10 E37: also
 *  when a surface mounts, unmounts or is replaced (the dock mounting after the viewer, a fold, the
 *  dock's columns), so the empty-assembly note and the model never sit under a surface that the
 *  last measure missed. Returns the cleanup. */
export function watchInsets(app: HTMLElement): () => void {
  const q = (s: string) => app.querySelector<HTMLElement>(s);
  const measure = () => {
    const vp = q("[data-region=viewer]");
    if (!vp) { if (viewInsets.peek() !== NO_INSETS) viewInsets.value = NO_INSETS; return; }
    const l = q(LEFT);
    const r = q(RIGHT);
    const next = insetsOf(vp.getBoundingClientRect(), l?.getBoundingClientRect() ?? null, r?.getBoundingClientRect() ?? null);
    const cur = viewInsets.peek();
    if (cur.left !== next.left || cur.right !== next.right) viewInsets.value = next;
  };
  measure();
  if (typeof ResizeObserver === "undefined") return () => {};
  const ro = new ResizeObserver(measure);
  ro.observe(app);
  let seen: (HTMLElement | null)[] = [];
  const observeSurfaces = () => {
    const now = WATCHED.map(q);
    if (now.every((el, i) => el === seen[i])) return false;
    for (const el of seen) if (el && !now.includes(el)) ro.unobserve(el);
    for (const el of now) if (el && !seen.includes(el)) ro.observe(el);
    seen = now;
    return true;
  };
  observeSurfaces();
  // a surface element swapped in or out: observe the new one and measure (once per frame at most)
  let pending: ReturnType<typeof setTimeout> | null = null;
  const onMutate = () => {
    if (pending !== null) return;
    pending = setTimeout(() => { pending = null; if (observeSurfaces()) measure(); }, 16);
  };
  const mo = typeof MutationObserver === "undefined" ? null : new MutationObserver(onMutate);
  mo?.observe(app, { childList: true, subtree: true });
  return () => {
    ro.disconnect();
    mo?.disconnect();
    if (pending !== null) clearTimeout(pending);
  };
}
