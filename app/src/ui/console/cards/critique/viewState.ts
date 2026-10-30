// Which presented view card holds the one live WebGL context, and which view the immersive view
// shows (with the rest of that reply's views, for ⌘[ / ⌘]).
import { signal, type Signal } from "@preact/signals";
import type { PresentedView } from "./announce";

/** The card that renders live (key); every other card shows a still. */
export const liveCard: Signal<string | null> = signal<string | null>(null);

export interface ViewImmersiveTarget { views: readonly PresentedView[]; index: number }

export const viewImmersive: Signal<ViewImmersiveTarget | null> = signal<ViewImmersiveTarget | null>(null);

let returnFocus: HTMLElement | null = null;

export function openViewImmersive(views: readonly PresentedView[], index: number): void {
  if (!views.length) return;
  returnFocus = typeof document !== "undefined" && document.activeElement instanceof HTMLElement ? document.activeElement : null;
  viewImmersive.value = { views, index: Math.min(views.length - 1, Math.max(0, index)) };
}

export function closeViewImmersive(): void {
  viewImmersive.value = null;
  const el = returnFocus;
  returnFocus = null;
  if (el && el.isConnected) el.focus();
}

/** ⌘] (+1) / ⌘[ (−1): the next / previous view of the same reply (wraps around). */
export function stepViewImmersive(delta: 1 | -1): void {
  const t = viewImmersive.peek();
  if (!t || t.views.length < 2) return;
  viewImmersive.value = { ...t, index: (t.index + delta + t.views.length) % t.views.length };
}
