// The model's room (P10 FWA, after the final review's I1): however the dock is laid out, the
// build view keeps at least VIEWPORT_MIN px between the floating surfaces. The dock gives way
// first — it shrinks toward the narrowest its open columns allow — and when even that leaves the
// model too little, the Parts surface folds to its slim tab by itself (router.partsAutoFold) and
// unfolds once the room comes back (a narrower dock, fewer columns, a wider window). The CSS cap in
// base.css (--console-w) is the same rule without the Parts surface; App sets --dock-w from
// `dockMax` here, and the dock's edge drag stops there too. The math is pure; the signals wrap it.
import { computed, effect, signal, type ReadonlySignal, type Signal } from "@preact/signals";
import { atHome } from "../../state/home";
import { dockLayout, minWidth, VIEWPORT_MIN, type DockLayout } from "../console/dockLayout";
import { activePage, consoleCollapsed, leftFolded, setPartsAutoFold } from "../pages/router";

/** The shell's --gap: a floating surface sits this far in from the window edge and the model. */
export const GAP = 12;
/** The Parts surface open (tokens.css --left-w) and folded to its tab (base.css .surface-tab). */
export const LEFT_W = 240;
export const LEFT_TAB_W = 34;
/** Never squeeze the dock narrower than this, whatever the window (it would be unusable). */
const DOCK_FLOOR = 300;
/** base.css .app min-width. */
const APP_MIN_W = 1280;

/** The shell's width (CSS px), kept current by installRoom(); 0 before (no cap then). */
export const windowWidth: Signal<number> = signal<number>(0);

/** What the Parts surface takes from the left of the build view (0 off the Design page). */
export function leftOuter(parts: "open" | "tab" | "none"): number {
  return parts === "none" ? 0 : GAP + (parts === "open" ? LEFT_W : LEFT_TAB_W) + GAP;
}

/** The widest the dock (its columns and closed tabs) may be in a `win`-wide window with `left` px
 *  taken by the Parts surface, so the model keeps VIEWPORT_MIN. */
export function dockCap(win: number, left: number): number {
  return Math.max(DOCK_FLOOR, win - left - VIEWPORT_MIN - 2 * GAP);
}

/** The narrowest the dock can be with this layout (P11 F2: one width, whatever is open). */
export function dockMin(_l: DockLayout): number {
  return minWidth();
}

/** Whether the Parts surface must fold so the dock and the model both fit: even at its narrowest
 *  the dock would not leave the model VIEWPORT_MIN beside the open Parts surface. */
export function partsMustFold(win: number, l: DockLayout): boolean {
  return win > 0 && dockCap(win, leftOuter("open")) < dockMin(l);
}

/** The Design page's Parts surface as it shows now. */
function partsNow(): "open" | "tab" | "none" {
  if (atHome.value || activePage.value !== "design") return "none";
  return leftFolded.value ? "tab" : "open";
}

/** The dock's width cap now (px), for --dock-w and the edge drag. */
export const dockMax: ReadonlySignal<number> = computed(() => {
  const win = windowWidth.value;
  return win > 0 ? dockCap(win, leftOuter(partsNow())) : Number.POSITIVE_INFINITY;
});

/** Keeps `windowWidth` current and folds / unfolds the Parts surface as the room requires.
 *  Returns the cleanup. */
export function installRoom(): () => void {
  // the shell lays out at least APP_MIN_W wide (base.css .app min-width) in any window
  const onResize = () => { windowWidth.value = globalThis.innerWidth ? Math.max(APP_MIN_W, globalThis.innerWidth) : 0; };
  onResize();
  globalThis.addEventListener?.("resize", onResize);
  const stop = effect(() => {
    const design = !atHome.value && activePage.value === "design";
    setPartsAutoFold(design && !consoleCollapsed.value && partsMustFold(windowWidth.value, dockLayout.value));
  });
  return () => {
    globalThis.removeEventListener?.("resize", onResize);
    stop();
    setPartsAutoFold(false);
  };
}
