// Print page state that outlives the page (switching pages while a slice runs or a print card
// waits keeps both), and the polling hooks. Every poll is a timeout chain: one request in
// flight at a time, stopped on unmount or when the thing it watches is finished.
import { signal, type Signal } from "@preact/signals";
import { useEffect, useState } from "preact/hooks";
import type { PrintCall, PrinterStatus, PrintStart, Slice } from "../../../api/types";
import { actions } from "../../../state/store";

/** Poll intervals (ms). Tests shorten them. */
export const POLL = { statusMs: 4000, sliceMs: 600, printMs: 1000 };

export interface PrintRequest { start: PrintStart; call: PrintCall | null }

/** The current slice (running or finished) and the current print request, if any. */
export const currentSlice: Signal<Slice | null> = signal<Slice | null>(null);
export const currentPrint: Signal<PrintRequest | null> = signal<PrintRequest | null>(null);

export function resetPrintSession(): void {
  currentSlice.value = null;
  currentPrint.value = null;
}

/** Repeats `tick` every `ms` while `active`; a tick returning false stops the chain. */
export function usePoll(key: unknown, active: boolean, ms: () => number, tick: () => Promise<boolean | void>): void {
  useEffect(() => {
    if (!active) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const run = async () => {
      let again: boolean | void;
      try {
        again = await tick();
      } catch {
        again = true; // a failed poll is retried at the next tick
      }
      if (live && again !== false) timer = setTimeout(() => { void run(); }, ms());
    };
    void run();
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
  }, [key, active]); // `tick` is the one from when `key` last changed: it must only depend on `key`
}

/** The selected printer's status, refreshed every POLL.statusMs. null until the first answer. */
export function usePrinterStatus(printerId: string | null): PrinterStatus | null {
  const [st, setSt] = useState<{ id: string; status: PrinterStatus } | null>(null);
  usePoll(printerId, printerId !== null, () => POLL.statusMs, async () => {
    if (!printerId) return false;
    const status = await actions.client().printerStatus(printerId);
    setSt({ id: printerId, status });
  });
  return st && st.id === printerId ? st.status : null;
}
