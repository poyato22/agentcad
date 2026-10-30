// A small "⋯" overflow menu: a menu button and a list of actions. Arrow keys move, Esc and a
// click outside close it, and focus returns to the button.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import "./overflowMenu.css";

export interface OverflowItem { label: string; onSelect: () => void }

export function OverflowMenu({ label, items, class: cls }: { label: string; items: readonly OverflowItem[]; class?: string }): JSX.Element {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLSpanElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    root.current?.querySelector<HTMLElement>("[role=menuitem]")?.focus();
    const away = (e: MouseEvent) => { if (root.current && !root.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);
  const onKey = (e: KeyboardEvent) => {
    if (!open) return;
    const all = [...(root.current?.querySelectorAll<HTMLElement>("[role=menuitem]") ?? [])];
    const i = all.indexOf(document.activeElement as HTMLElement);
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setOpen(false); btn.current?.focus(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); all[(i + 1) % all.length]?.focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); all[(i - 1 + all.length) % all.length]?.focus(); }
  };
  return (
    <span class={`ovm${cls ? ` ${cls}` : ""}`} ref={root} onKeyDown={onKey}>
      <button type="button" ref={btn} class="ovm-btn" aria-haspopup="menu" aria-expanded={open} aria-label={label} onClick={() => setOpen(!open)}>⋯</button>
      {open && (
        <span class="ovm-menu" role="menu" aria-label={label}>
          {items.map((it) => (
            <button key={it.label} type="button" role="menuitem" class="ovm-item"
              onClick={() => { setOpen(false); btn.current?.focus(); it.onSelect(); }}>{it.label}</button>
          ))}
        </span>
      )}
    </span>
  );
}
