// The printer list changes in two places (Profile → Printers, the Print page's add form); both
// bump this so the top bar's PRINTER light re-reads GET /printers.
import { signal, type Signal } from "@preact/signals";

export const printersRev: Signal<number> = signal(0);

export function printersChanged(): void {
  printersRev.value++;
}
