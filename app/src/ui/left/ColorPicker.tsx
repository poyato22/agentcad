import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { normalizeHex, PALETTE, swatchName } from "./colors";

export interface ColorPickerProps {
  value: string | null;
  /** A chosen color (null = default grey). The picker stays open for the custom field only. */
  onPick(color: string | null): void;
  onClose(): void;
  partName: string;
}

/** Twelve swatches, the default grey and a custom #rrggbb field. ←/→/↑/↓ move between swatches,
 *  Enter picks, Esc closes (focus goes back to the part's chip). */
export function ColorPicker({ value, onPick, onClose, partName }: ColorPickerProps): JSX.Element {
  const [hex, setHex] = useState(value ?? "");
  const grid = useRef<HTMLDivElement>(null);
  const valid = normalizeHex(hex);

  useEffect(() => {
    const el = grid.current?.querySelector<HTMLButtonElement>("[aria-checked=true]") ?? grid.current?.querySelector<HTMLButtonElement>("button");
    el?.focus();
  }, []);

  function onKeyDown(e: KeyboardEvent): void {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); return; }
    const btns = [...(grid.current?.querySelectorAll<HTMLButtonElement>("button") ?? [])];
    const i = btns.indexOf(document.activeElement as HTMLButtonElement);
    if (i < 0) return;
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : e.key === "ArrowDown" ? 6 : e.key === "ArrowUp" ? -6 : 0;
    if (!step) return;
    e.preventDefault();
    btns[Math.min(btns.length - 1, Math.max(0, i + step))]?.focus();
  }

  return (
    <div class="cpick" role="dialog" aria-label={`Color of ${partName}`} onKeyDown={onKeyDown} data-testid="color-picker">
      <div class="cpick-grid" ref={grid} role="radiogroup" aria-label="Palette">
        {PALETTE.map((s) => (
          <button key={s.hex} type="button" role="radio" class="cpick-sw" aria-checked={value === s.hex} title={s.name} aria-label={s.name}
            data-hex={s.hex} style={{ background: s.hex }} onClick={() => onPick(s.hex)} />
        ))}
      </div>
      <div class="cpick-row">
        <button type="button" class="cpick-none" aria-pressed={value === null} onClick={() => onPick(null)}>Default grey</button>
        <span class="cpick-now faint">{swatchName(value)}</span>
      </div>
      <form class="cpick-row" onSubmit={(e) => { e.preventDefault(); if (valid) onPick(valid); }}>
        <input type="color" class="cpick-native" aria-label="Pick any color" value={valid ?? value ?? "#a3a8ad"}
          onInput={(e) => setHex((e.target as HTMLInputElement).value)} />
        <input class="cpick-hex" aria-label="Hex color" placeholder="#rrggbb" maxLength={7} spellcheck={false} value={hex}
          aria-invalid={hex !== "" && !valid} onInput={(e) => setHex((e.target as HTMLInputElement).value)} />
        <button type="submit" class="btn" disabled={!valid}>Apply</button>
      </form>
    </div>
  );
}
