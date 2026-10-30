// The appearance popover: one click on a row's swatch opens it (R20). A finish row — the plain
// drafting look plus the seven material presets, each drawn as a small lit sphere — and the color
// palette with a custom #rrggbb. A pick applies at once (to every selected part when the row is
// in the selection) and the popover stays open so finish and color can be tried together; Esc or
// a press outside closes it.
import type { JSX } from "preact";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import { materialOf, PRESETS, swatchStyle, type Appearance, type PresetId } from "../../lib/appearance";
import { normalizeHex, PALETTE, swatchName } from "./colors";

export interface AppearancePopoverProps {
  /** What the popover shows as current (the row's part). */
  color: string | null;
  appearance: Appearance | null;
  /** "Arm" or "3 parts". */
  title: string;
  /** The swatch's screen rect: the popover opens under it. */
  anchor: { left: number; bottom: number; top: number };
  onFinish(preset: PresetId | null): void;
  onColor(color: string | null): void;
  onClose(): void;
}

const W = 236;

export function AppearancePopover({ color, appearance, title, anchor, onFinish, onColor, onClose }: AppearancePopoverProps): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  const [hex, setHex] = useState(color ?? "");
  const valid = normalizeHex(hex);
  const preset = appearance?.preset ?? null;

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const h = el.offsetHeight;
    const vw = window.innerWidth; const vh = window.innerHeight;
    const left = Math.max(6, Math.min(anchor.left - 8, vw - W - 6));
    const top = anchor.bottom + 4 + h > vh - 6 ? Math.max(6, anchor.top - h - 4) : anchor.bottom + 4;
    // placed straight on the element: a state update here would render it twice
    el.style.left = `${left}px`; el.style.top = `${top}px`;
    (el.querySelector<HTMLButtonElement>("[aria-checked=true]") ?? el.querySelector<HTMLButtonElement>("button"))?.focus();
  }, [anchor.left, anchor.bottom, anchor.top]);

  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      const t = e.target as HTMLElement | null;
      // the swatch that opened it toggles it itself
      if (!t || ref.current?.contains(t) || t.closest("[data-appearance-anchor]")) return;
      onClose();
    };
    document.addEventListener("pointerdown", onDown, true);
    return () => document.removeEventListener("pointerdown", onDown, true);
  }, [onClose]);

  function onKeyDown(e: KeyboardEvent): void {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); return; }
    const group = (document.activeElement as HTMLElement | null)?.closest<HTMLElement>("[role=radiogroup]");
    if (!group) return;
    const btns = [...group.querySelectorAll<HTMLButtonElement>("button")];
    const i = btns.indexOf(document.activeElement as HTMLButtonElement);
    const cols = Number(group.dataset.cols ?? 4);
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : e.key === "ArrowDown" ? cols : e.key === "ArrowUp" ? -cols : 0;
    if (!step || i < 0) return;
    e.preventDefault();
    btns[Math.min(btns.length - 1, Math.max(0, i + step))]?.focus();
  }

  const tint = appearance?.color ?? color;
  return (
    <div ref={ref} class="apop" role="dialog" aria-label={`Appearance of ${title}`} data-testid="appearance-popover"
      style={{ left: `${anchor.left}px`, top: `${anchor.bottom + 4}px`, width: `${W}px` }} onKeyDown={onKeyDown}>
      <div class="apop-head"><span class="apop-title">{title}</span><span class="faint">{presetName(preset, appearance)}</span></div>
      <div class="apop-label">Finish</div>
      <div class="apop-finishes" role="radiogroup" aria-label="Finish" data-cols="4">
        <button type="button" role="radio" class="apop-finish" aria-checked={appearance === null} aria-label="Plain" title="Plain: the drafting look"
          onClick={() => onFinish(null)}>
          <span class="apop-ball" style={swatchStyle(materialOf({ color: tint }))} />
          <span class="apop-fname">Plain</span>
        </button>
        {PRESETS.map((p) => (
          <button key={p.id} type="button" role="radio" class="apop-finish" aria-checked={preset === p.id} aria-label={p.name} title={p.name}
            data-preset={p.id} onClick={() => onFinish(p.id)}>
            <span class="apop-ball" style={swatchStyle({ color: p.color ?? tint, metalness: p.metalness, roughness: p.roughness, opacity: p.opacity, shiny: true })} />
            <span class="apop-fname">{p.name}</span>
          </button>
        ))}
      </div>
      <div class="apop-label">Color</div>
      <div class="apop-colors" role="radiogroup" aria-label="Color" data-cols="7">
        <button type="button" role="radio" class="apop-sw apop-sw-none" aria-checked={tint === null} title="Default grey" aria-label="Default grey"
          onClick={() => { setHex(""); onColor(null); }} />
        {PALETTE.map((s) => (
          <button key={s.hex} type="button" role="radio" class="apop-sw" aria-checked={tint === s.hex} title={s.name} aria-label={s.name}
            data-hex={s.hex} style={{ background: s.hex }} onClick={() => { setHex(s.hex); onColor(s.hex); }} />
        ))}
      </div>
      <form class="apop-custom" onSubmit={(e) => { e.preventDefault(); if (valid) onColor(valid); }}>
        <input type="color" class="apop-native" aria-label="Pick any color" value={valid ?? tint ?? "#a3a8ad"}
          onInput={(e) => setHex((e.target as HTMLInputElement).value)}
          onChange={(e) => onColor((e.target as HTMLInputElement).value)} />
        <input class="apop-hex" aria-label="Hex color" placeholder="#rrggbb" maxLength={7} spellcheck={false} value={hex}
          aria-invalid={hex !== "" && !valid} onInput={(e) => setHex((e.target as HTMLInputElement).value)} />
        <button type="submit" class="btn" disabled={!valid}>Apply</button>
      </form>
      <div class="apop-now faint" aria-live="polite">{swatchName(tint)}</div>
    </div>
  );
}

function presetName(preset: string | null, a: Appearance | null): string {
  if (!a) return "Plain";
  return PRESETS.find((p) => p.id === preset)?.name ?? "Custom";
}
