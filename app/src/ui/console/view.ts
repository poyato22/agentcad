// Which console view shows: the conversation (default) or the dense step log ("Details").
// The choice is a per-viewer convenience kept in localStorage (it may be unavailable: then it
// just isn't remembered). The inline new-run composer's open state lives here too.
import { signal, type Signal } from "@preact/signals";

export type ConsoleView = "chat" | "details";

export const VIEW_KEY = "mises.console.view";

function readView(): ConsoleView {
  try {
    return globalThis.localStorage?.getItem(VIEW_KEY) === "details" ? "details" : "chat";
  } catch {
    return "chat";
  }
}

export const consoleView: Signal<ConsoleView> = signal<ConsoleView>(readView());

/** Switch views. `remember: false` for a switch the user didn't ask for (an evidence link). */
export function setConsoleView(v: ConsoleView, remember = true): void {
  consoleView.value = v;
  if (!remember) return;
  try { globalThis.localStorage?.setItem(VIEW_KEY, v); } catch { /* not remembered */ }
}

/** The inline composer ("+ New" in the conversation view). */
export const composerOpen: Signal<boolean> = signal<boolean>(false);

/** Tests. */
export function resetConsoleView(): void {
  consoleView.value = readView();
  composerOpen.value = false;
}
