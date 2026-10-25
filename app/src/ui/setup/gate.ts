// Open/close state of the setup wizard, and the per-session "skip" choices.
// The Profile screen re-opens the wizard with openSetupWizard().
import { signal, type Signal } from "@preact/signals";

/** True while the wizard was opened on purpose (Profile → "Run setup again"). */
export const setupWizardOpen: Signal<boolean> = signal<boolean>(false);

export function openSetupWizard(): void { setupWizardOpen.value = true; }
export function closeSetupWizard(): void { setupWizardOpen.value = false; }

export type SkipFlag = "model" | "onshape";

const key = (f: SkipFlag) => `mises.setup.skip.${f}`;

/** A skip lasts for this app session (survives a reload, not a relaunch). Storage may be unavailable. */
export function skipped(f: SkipFlag): boolean {
  try { return sessionStorage.getItem(key(f)) === "1"; } catch { return false; }
}

export function setSkipped(f: SkipFlag, on: boolean): void {
  try {
    if (on) sessionStorage.setItem(key(f), "1");
    else sessionStorage.removeItem(key(f));
  } catch { /* storage unavailable: the skip only lasts until reload */ }
}
