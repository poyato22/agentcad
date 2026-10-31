// Print's two modes (D21): "3D print" (slice and send) and "Drawing" (the part's 2D drawing,
// once its own tab). A segmented switch in the Print page header flips them; the choice is a
// per-device convenience kept in localStorage.
import { signal, type Signal } from "@preact/signals";
import type { JSX } from "preact";

export type PrintMode = "print" | "drawing";
const KEY = "mises.print.mode";

function initial(): PrintMode {
  try { return localStorage.getItem(KEY) === "drawing" ? "drawing" : "print"; } catch { return "print"; }
}

export const printMode: Signal<PrintMode> = signal<PrintMode>(initial());

export function setPrintMode(m: PrintMode): void {
  printMode.value = m;
  try { localStorage.setItem(KEY, m); } catch { /* storage may be unavailable */ }
}

const MODES: readonly { id: PrintMode; label: string }[] = [
  { id: "print", label: "3D print" },
  { id: "drawing", label: "Drawing" },
];

/** The `3D print | Drawing` segmented switch (radio semantics: one is always chosen). */
export function PrintModeSwitch(): JSX.Element {
  const cur = printMode.value;
  return (
    <div class="seg" role="radiogroup" aria-label="Print mode">
      {MODES.map((m) => (
        <button key={m.id} type="button" role="radio" class={m.id === cur ? "seg-btn on" : "seg-btn"}
          aria-checked={m.id === cur} tabIndex={m.id === cur ? 0 : -1}
          onKeyDown={(e) => {
            if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
            e.preventDefault();
            const next = MODES[(MODES.findIndex((x) => x.id === cur) + 1) % MODES.length].id;
            setPrintMode(next);
            (e.currentTarget.parentElement?.querySelector<HTMLButtonElement>(`[aria-checked="false"]`))?.focus();
          }}
          onClick={() => setPrintMode(m.id)}>{m.label}</button>
      ))}
    </div>
  );
}
