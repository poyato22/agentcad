// Boot flow state: which full-screen gate (if any) sits over the app shell.
//   loading      — the core is starting (ui/boot/Loading)
//   intro        — the cube-assembling welcome (first launch, or Profile → "Replay intro")
//   systemcheck  — the one-time dependency check after the first intro
//   ready        — the shell is live, no gate
// The "seen" flags are per-device conveniences in localStorage (storage may be blocked): the core
// profile stays the source of truth for setup. They only decide whether the intro and the one-time
// system check play again on this machine.
import { signal, type Signal } from "@preact/signals";

export type BootPhase = "loading" | "intro" | "systemcheck" | "ready";

const INTRO_KEY = "mises.intro.seen";
const SYSCHECK_KEY = "mises.systemcheck.seen";

function flag(key: string): boolean {
  try { return globalThis.localStorage?.getItem(key) === "1"; } catch { return false; }
}
function setFlag(key: string, on: boolean): void {
  try {
    if (on) globalThis.localStorage?.setItem(key, "1");
    else globalThis.localStorage?.removeItem(key);
  } catch { /* private mode / blocked: the gate just plays again next launch */ }
}

export function introSeen(): boolean { return flag(INTRO_KEY); }
export function markIntroSeen(): void { setFlag(INTRO_KEY, true); }
export function systemCheckSeen(): boolean { return flag(SYSCHECK_KEY); }
export function markSystemCheckSeen(): void { setFlag(SYSCHECK_KEY, true); }

/** Raised by Profile → "Replay intro"; BootOverlay plays the intro again and clears it. */
export const replayRequested: Signal<boolean> = signal<boolean>(false);
export function replayIntro(): void { replayRequested.value = true; }
export function clearReplay(): void { replayRequested.value = false; }
