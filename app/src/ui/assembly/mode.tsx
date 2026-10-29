// Design page mode: one part on the sheet, or the build's assembly.
//
// Ruling (A4): Assembly is a toggle on the Design page, not a sixth tab. It keeps the left pane
// (the build's parts, which is where you add from) and the console beside it, and it leaves ⌘6 to
// the agent manager (A2) with no shortcut shuffle. The switch sits at the top centre of the sheet;
// ⌥⌘A flips it from anywhere on the Design page. The choice is remembered per Mac.
import { signal, type Signal } from "@preact/signals";
import type { JSX } from "preact";
import { isTrustedKey, registerShortcut } from "../../lib/keys";
import { modalOpen } from "../pages/router";
import "./assembly.css";

export type DesignMode = "part" | "assembly";

const KEY = "mises.design.mode";

function remembered(): DesignMode {
  try { return localStorage.getItem(KEY) === "assembly" ? "assembly" : "part"; } catch { return "part"; }
}

export const designMode: Signal<DesignMode> = signal<DesignMode>(remembered());

export function setDesignMode(m: DesignMode): void {
  designMode.value = m;
  try { localStorage.setItem(KEY, m); } catch { /* private window: not remembered */ }
}

export function toggleDesignMode(): void { setDesignMode(designMode.value === "part" ? "assembly" : "part"); }

/** ⌥⌘A flips Part / Assembly while the Design page shows (real key presses only, never under a
 *  modal). Returns unregister. */
export function installModeShortcut(isDesign: () => boolean): () => void {
  return registerShortcut("Meta+Alt+A", (e) => {
    if (!isTrustedKey(e) || modalOpen() || !isDesign()) return;
    toggleDesignMode();
  });
}

/** The Part | Assembly switch at the top centre of the Design sheet. */
export function ModeSwitch(): JSX.Element {
  const mode = designMode.value;
  const btn = (m: DesignMode, text: string) => (
    <button type="button" role="radio" class="vt-btn" aria-checked={mode === m} data-active={mode === m}
      tabIndex={mode === m ? 0 : -1} onClick={() => setDesignMode(m)}
      onKeyDown={(e) => {
        if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
          e.preventDefault();
          const next = m === "part" ? "assembly" : "part";
          setDesignMode(next);
          (e.currentTarget.parentElement?.querySelector(`[data-mode=${next}]`) as HTMLButtonElement | null)?.focus();
        }
      }}
      data-mode={m} title={`${text} (⌥⌘A)`}>{text}</button>
  );
  return (
    <div class="mode-switch" role="radiogroup" aria-label="Design mode" data-testid="mode-switch">
      {btn("part", "part")}
      {btn("assembly", "assembly")}
    </div>
  );
}
