// A small menu button and a list of actions: the "⋯" overflow menu by default, or any trigger (the
// Home Settings gear, Plan 10 E2). Arrow keys move, Esc, a click outside or focus leaving it close
// it, and focus returns to the button (Esc / a pick). The button is a toggle (E7): clicking it
// again closes the menu. An item may be disabled (shown, skipped by the arrows), checked (a
// menuitemcheckbox: the view it opens is showing), carry a quiet count, or start a new group.
import type { ComponentChildren, JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import "./overflowMenu.css";

export interface OverflowItem {
  label: string;
  onSelect: () => void;
  disabled?: boolean;
  /** Shown as a checkable item (role menuitemcheckbox) in this state. */
  checked?: boolean;
  /** A quiet trailing note (a count). */
  note?: string;
  /** A hairline above this item: it starts a new group. */
  divider?: boolean;
}

export interface OverflowMenuProps {
  label: string;
  items: readonly OverflowItem[];
  class?: string;
  /** The button's content (default "⋯"); the button keeps `label` as its accessible name. */
  trigger?: ComponentChildren;
  /** Extra class on the button. */
  btnClass?: string;
  title?: string;
}

export function OverflowMenu({ label, items, class: cls, trigger, btnClass, title }: OverflowMenuProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLSpanElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const enabled = () => [...(root.current?.querySelectorAll<HTMLElement>("[role^=menuitem]:not([disabled])") ?? [])];
  useEffect(() => {
    if (!open) return;
    enabled()[0]?.focus();
    const away = (e: MouseEvent) => { if (root.current && !root.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);
  const onKey = (e: KeyboardEvent) => {
    if (!open) return;
    const all = enabled();
    const i = all.indexOf(document.activeElement as HTMLElement);
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setOpen(false); btn.current?.focus(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); all[(i + 1) % all.length]?.focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); all[(i - 1 + all.length) % all.length]?.focus(); }
  };
  // Tab / Shift+Tab out of the menu (or any focus move elsewhere) closes it
  const onFocusOut = (e: FocusEvent) => {
    const to = e.relatedTarget as Node | null;
    if (open && to && root.current && !root.current.contains(to)) setOpen(false);
  };
  return (
    <span class={`ovm${cls ? ` ${cls}` : ""}`} ref={root} onKeyDown={onKey} onFocusOut={onFocusOut}>
      <button type="button" ref={btn} class={`ovm-btn${btnClass ? ` ${btnClass}` : ""}`} aria-haspopup="menu" aria-expanded={open}
        aria-label={label} title={title} onClick={() => setOpen(!open)}>{trigger ?? "⋯"}</button>
      {open && (
        <span class="ovm-menu" role="menu" aria-label={label}>
          {items.map((it) => (
            <button key={it.label} type="button" role={it.checked === undefined ? "menuitem" : "menuitemcheckbox"}
              aria-checked={it.checked}
              class={`ovm-item${it.divider ? " ovm-divided" : ""}`} disabled={it.disabled}
              onClick={() => { setOpen(false); btn.current?.focus(); it.onSelect(); }}>
              <span class="ovm-label">{it.label}</span>
              {it.note && <span class="ovm-note num">{it.note}</span>}
            </button>
          ))}
        </span>
      )}
    </span>
  );
}
