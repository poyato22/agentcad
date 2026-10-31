// Global stop-all: POST /runs/cancel_all from ⌘⇧. (works from any focus, including text inputs)
// and the /stopall command. FW-layout removed the top bar's red button: the composer's Stop is the
// visible control. One request at a time; a toast reports the result.
import { signal } from "@preact/signals";
import { registerShortcut } from "../../lib/keys";
import { actions } from "../../state/store";

export const stopBusy = signal(false);
/** Transient message under the top bar ("stopped 2"). */
export const toast = signal<string | null>(null);

let toastTimer: ReturnType<typeof setTimeout> | undefined;
export function showToast(msg: string, ms = 3000): void {
  toast.value = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.value = null; }, ms);
}

/** Cancels every running run. Resolves with the cancelled run ids; a call while busy is a no-op. */
export async function stopAll(): Promise<string[]> {
  if (stopBusy.peek()) return [];
  stopBusy.value = true;
  try {
    const res = await actions.client().cancelAll();
    const nFailed = res.failed.length;
    showToast(nFailed ? `stopped ${res.cancelled.length} · ${nFailed} failed` : `stopped ${res.cancelled.length}`);
    return res.cancelled;
  } catch {
    showToast("stop all failed");
    return [];
  } finally {
    stopBusy.value = false;
  }
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
