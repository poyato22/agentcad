// Global stop-all: POST /runs/cancel_all from the top-bar button and ⌘⇧. (works from any focus,
// including text inputs). One request at a time; a toast reports the result.
import { signal } from "@preact/signals";
import { registerShortcut } from "../../lib/keys";
import { actions } from "../../state/store";

export const stopBusy = signal(false);
/** Transient message under the top bar ("stopped 2"). */
export const toast = signal<string | null>(null);

let toastTimer: ReturnType<typeof setTimeout> | undefined;
function showToast(msg: string): void {
  toast.value = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.value = null; }, 3000);
}

/** Cancels every running run. Resolves with the cancelled run ids; a call while busy is a no-op. */
export async function stopAll(): Promise<string[]> {
  if (stopBusy.peek()) return [];
  stopBusy.value = true;
  try {
    const res = await actions.client().cancelAll();
    // The core is adding `failed`; the client schema may not carry it yet.
    const failed = (res as { failed?: unknown }).failed;
    const nFailed = Array.isArray(failed) ? failed.length : 0;
    showToast(nFailed ? `stopped ${res.cancelled.length} · ${nFailed} failed` : `stopped ${res.cancelled.length}`);
    return res.cancelled;
  } catch {
    showToast("stop all failed");
    return [];
  } finally {
    stopBusy.value = false;
  }
}

export function useStopAll(): { stopAll(): Promise<string[]>; busy: boolean } {
  return { stopAll, busy: stopBusy.value };
}

/** Test helper: back to idle. */
export function resetStopAll(): void {
  stopBusy.value = false;
  toast.value = null;
}

/** Registers ⌘⇧. globally. Returns the unregister function. */
export function installStopAllShortcut(): () => void {
  return registerShortcut("Meta+Shift+.", () => { void stopAll(); });
}
