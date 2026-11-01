// Plan 10 E11 (undo): one command stack for the user's own actions on the build — a part or an
// instance deleted, an instance moved or reset to the origin, a rename, a color or finish, hide /
// show, a material, assembly parts removed. Each action records how to undo and redo itself
// through the core's existing routes (restore, patch). ⌘Z / ⇧⌘Z run it when focus is not in a
// text field (text fields keep their own native undo) and no dialog is open; a toast says what
// happened ("Undid: Rename Bracket"). Agents' edits are not on it: they are the agent's.
import { signal } from "@preact/signals";

export interface UndoCommand {
  /** What the user did, in a few words ("Delete Bracket", "Reset arm to the origin"). */
  label: string;
  undo(): Promise<unknown> | unknown;
  redo(): Promise<unknown> | unknown;
}

/** The most commands kept (the oldest drop off). */
export const UNDO_DEPTH = 60;
const TOAST_MS = 4000;

const past: UndoCommand[] = [];
const future: UndoCommand[] = [];
/** Bumps on every change (what the UI reads to enable Undo / Redo). */
export const undoTick = signal(0);
/** The last undo / redo, for the toast (null: none showing). */
export const undoToast = signal<{ text: string; error: boolean; at: number } | null>(null);
let busy = false;
let toastTimer: ReturnType<typeof setTimeout> | null = null;

function toast(text: string, error = false): void {
  undoToast.value = { text, error, at: Date.now() };
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { undoToast.value = null; toastTimer = null; }, TOAST_MS);
}

/** Records an action the user just did (it already happened). A new action clears redo. */
export function pushUndo(cmd: UndoCommand): void {
  past.push(cmd);
  if (past.length > UNDO_DEPTH) past.shift();
  future.length = 0;
  undoTick.value++;
}

export function canUndo(): boolean { return past.length > 0; }
export function canRedo(): boolean { return future.length > 0; }
/** The command ⌘Z would undo next (null: none). */
export function topUndo(): UndoCommand | null { return past[past.length - 1] ?? null; }
export function undoLabel(): string | null { return past[past.length - 1]?.label ?? null; }
export function redoLabel(): string | null { return future[future.length - 1]?.label ?? null; }

async function run(from: UndoCommand[], to: UndoCommand[], which: "undo" | "redo"): Promise<boolean> {
  if (busy) return false;
  const cmd = from.pop();
  if (!cmd) { toast(which === "undo" ? "Nothing to undo" : "Nothing to redo"); undoTick.value++; return false; }
  busy = true;
  try {
    await cmd[which]();
    to.push(cmd);
    toast(`${which === "undo" ? "Undid" : "Redid"}: ${cmd.label}`);
    return true;
  } catch (e) {
    // it could not be undone (the thing is gone, the core refused): it leaves the stack
    toast(`Couldn't ${which} ${cmd.label}: ${e instanceof Error ? e.message : String(e)}`, true);
    return false;
  } finally {
    busy = false;
    undoTick.value++;
  }
}

export function undo(): Promise<boolean> { return run(past, future, "undo"); }
export function redo(): Promise<boolean> { return run(future, past, "redo"); }

/** Tests: an empty stack. */
export function clearUndo(): void {
  past.length = 0; future.length = 0; busy = false;
  undoToast.value = null;
  undoTick.value++;
}

/** Focus in a place that has its own undo (a text field, an editor). */
export function inTextField(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el || typeof el.tagName !== "string") return false;
  if (el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable) return true;
  if (el.tagName === "INPUT") {
    const type = ((el as HTMLInputElement).type || "text").toLowerCase();
    return !["button", "checkbox", "radio", "range", "color", "submit", "reset", "file"].includes(type);
  }
  return !!el.closest?.(".cm-editor");
}

export interface UndoKeyDeps { trusted?(e: KeyboardEvent): boolean; modal?(): boolean }

/** ⌘Z / ⇧⌘Z (and ⌘Y) on the window, outside text fields and dialogs. Returns the remover. */
export function installUndoKeys(deps: UndoKeyDeps = {}): () => void {
  const trusted = deps.trusted ?? ((e: KeyboardEvent) => e.isTrusted && !e.isComposing);
  const modal = deps.modal ?? (() => typeof document !== "undefined" && document.querySelector('[aria-modal="true"]') !== null);
  const onKey = (e: KeyboardEvent) => {
    if (!e.metaKey || e.ctrlKey || e.altKey || !trusted(e) || e.defaultPrevented) return;
    const z = e.code === "KeyZ" || e.key.toLowerCase() === "z";
    const y = e.code === "KeyY" || e.key.toLowerCase() === "y";
    if (!z && !y) return;
    if (inTextField(e.target) || inTextField(typeof document !== "undefined" ? document.activeElement : null) || modal()) return;
    e.preventDefault();
    if (z && !e.shiftKey) void undo();
    else void redo();
  };
  window.addEventListener("keydown", onKey, true);
  return () => window.removeEventListener("keydown", onKey, true);
}
