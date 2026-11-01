// The part menu (right-click a row, its ⋯ button, Shift+F10 or the context-menu key): a floating
// list at the pointer, Onshape-style. ↑/↓ move, Enter picks, Esc closes; a press outside, a
// scroll or the window losing focus closes it too. A destructive item asks once more in place
// ("Delete — click again") instead of opening a dialog.
import type { JSX } from "preact";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";

export interface MenuItem {
  label: string;
  keys?: string;
  run(): void;
  disabled?: boolean;
  /** Shown in place of the label after a first click; the second click runs it. */
  confirm?: string;
  danger?: boolean;
  title?: string;
}
export type MenuEntry = MenuItem | "sep";

export interface PartMenuProps {
  items: readonly MenuEntry[];
  label: string;
  at: { x: number; y: number };
  onClose(refocus: boolean): void;
}

const MARGIN = 6;

export function PartMenu({ items, label, at, onClose }: PartMenuProps): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  const [arming, setArming] = useState<string | null>(null);

  // keep it on screen: flip left / up when it would run past the window's edge
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const w = el.offsetWidth; const h = el.offsetHeight;
    const vw = window.innerWidth; const vh = window.innerHeight;
    // placed straight on the element: a state update here would render it twice
    el.style.left = `${Math.max(MARGIN, at.x + w > vw - MARGIN ? vw - w - MARGIN : at.x)}px`;
    el.style.top = `${Math.max(MARGIN, at.y + h > vh - MARGIN ? at.y - h : at.y)}px`;
    el.querySelector<HTMLButtonElement>("button:not([disabled])")?.focus();
  }, [at.x, at.y]);

  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      const t = e.target as HTMLElement | null;
      // a row's ⋯ button toggles the menu itself (E7): closing here would let its click reopen it
      if (!t || ref.current?.contains(t) || t.closest?.("[data-menu-anchor]")) return;
      onClose(false);
    };
    const onScroll = (e: Event) => { if (!ref.current?.contains(e.target as Node)) onClose(false); };
    const onBlur = () => onClose(false);
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("scroll", onScroll, true);
    window.addEventListener("blur", onBlur);
    window.addEventListener("resize", onBlur);
    return () => {
      document.removeEventListener("pointerdown", onDown, true);
      document.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("blur", onBlur);
      window.removeEventListener("resize", onBlur);
    };
  }, [onClose]);

  const focusables = () => [...(ref.current?.querySelectorAll<HTMLButtonElement>("button:not([disabled])") ?? [])];

  return (
    <div class="pmenu" role="menu" aria-label={label} ref={ref} data-testid="part-menu"
      style={{ left: `${at.x}px`, top: `${at.y}px` }}
      onContextMenu={(e) => e.preventDefault()}
      onKeyDown={(e) => {
        if (e.key === "Escape" || e.key === "Tab") { e.preventDefault(); e.stopPropagation(); onClose(true); return; }
        if (e.key !== "ArrowDown" && e.key !== "ArrowUp" && e.key !== "Home" && e.key !== "End") return;
        e.preventDefault();
        const f = focusables();
        const i = f.indexOf(document.activeElement as HTMLButtonElement);
        const n = e.key === "Home" ? 0 : e.key === "End" ? f.length - 1 : (i + (e.key === "ArrowDown" ? 1 : -1) + f.length) % f.length;
        f[n]?.focus();
      }}>
      {items.map((it, k) => it === "sep"
        ? <div key={`sep${k}`} class="pmenu-sep" role="separator" />
        : (
          <button key={it.label} type="button" role="menuitem" class="pmenu-item" disabled={it.disabled} title={it.title}
            data-danger={it.danger ? "" : undefined} data-armed={arming === it.label ? "" : undefined}
            onClick={() => {
              if (it.confirm && arming !== it.label) { setArming(it.label); return; }
              onClose(true);
              it.run();
            }}>
            <span>{arming === it.label && it.confirm ? it.confirm : it.label}</span>
            {it.keys && <kbd class="pmenu-keys">{it.keys}</kbd>}
          </button>
        ))}
    </div>
  );
}
