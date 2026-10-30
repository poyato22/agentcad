// The viewer's background picker: a short list of backgrounds plus a custom color. A pick shows at
// once and is saved to the profile. It opens from View ▸ Background… (the sheet keeps only Part |
// Assembly and View, U2b), so it is controlled: `open` / `onOpenChange`.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { normalizeHex } from "../left/colors";
import { backgroundStyle, BACKGROUNDS, choiceOf, saveBackground, viewerBackground } from "./background";
import "./background.css";

export function BackgroundMenu({ open, onOpenChange }: { open: boolean; onOpenChange(open: boolean): void }): JSX.Element | null {
  const setOpen = onOpenChange;
  const [failed, setFailed] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const bg = viewerBackground.value;
  const current = choiceOf(bg);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", onDown, true);
    (ref.current?.querySelector<HTMLButtonElement>("[aria-checked=true]") ?? ref.current?.querySelector<HTMLButtonElement>("button"))?.focus();
    return () => document.removeEventListener("pointerdown", onDown, true);
  }, [open]);

  const pick = (b: typeof bg) => { setFailed(false); void saveBackground(b).then((ok) => setFailed(!ok)); };

  if (!open) return null;
  return (
    <div class="vp-bg" ref={ref} onKeyDown={(e) => { if (e.key === "Escape" && open) { e.preventDefault(); e.stopPropagation(); setOpen(false); document.querySelector<HTMLButtonElement>(".view-btn")?.focus(); } }}>
      {open && (
        <div class="vp-bg-pop" role="radiogroup" aria-label="Viewer background">
          {BACKGROUNDS.map((c) => (
            <button key={c.id} type="button" role="radio" class="vp-bg-opt" aria-checked={current?.id === c.id} onClick={() => pick(c.bg)}>
              <span class="vp-bg-chip" data-mode={c.bg.mode} style={backgroundStyle(c.bg)} />
              <span>{c.name}</span>
            </button>
          ))}
          <label class="vp-bg-opt vp-bg-custom">
            <input type="color" aria-label="Custom background color" value={bg.mode === "solid" ? bg.colors[0] : "#808080"}
              onChange={(e) => { const v = normalizeHex((e.target as HTMLInputElement).value); if (v) pick({ mode: "solid", colors: [v] }); }} />
            <span>Custom color…</span>
          </label>
          {failed && <div class="vp-bg-err" role="alert">Couldn't save the background</div>}
        </div>
      )}
    </div>
  );
}
