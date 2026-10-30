// The Design viewer's "View" menu (Plan 7 U2b, R13). One button at the sheet's top right opens a
// menu with every display control, so the sheet itself only carries Part | Assembly (top left):
//   Display  Shaded / Wireframe             (radio)
//   Show     This part / Whole build        (radio)
//   Tools    `extra` items — the slot other tasks fill: U2a's Measure, G4's X-ray / section
//   ——       Add a feature…  ·  Part script (the Code view, a power-user view)
// Keyboard: ↓ / ↑ / Enter / Space on the button open it on the first / last item; ↑ ↓ Home End move
// (wrapping); Enter / Space pick; Escape, Tab or a click outside close it and focus returns to View.
import type { JSX } from "preact";
import { useLayoutEffect, useRef, useState } from "preact/hooks";
import { useDismiss } from "../common/dismiss";

export type ViewMode = "shaded" | "wire";
/** This part only, or every visible part of the build. */
export type ViewScope = "part" | "build";

/** A tool another task adds to the View menu (its own "Tools" group). `checked` makes it a toggle
 *  (menuitemcheckbox); leave it out for a one-shot action. `hint` is the key shown at the right. */
export interface ViewMenuItem {
  id: string; label: string; onSelect(): void;
  checked?: boolean; disabled?: boolean; hint?: string;
}

export interface ViewerToolsProps {
  mode: ViewMode; onMode(m: ViewMode): void;
  scope?: ViewScope; onScope?(s: ViewScope): void;
  /** "Add a feature…": the ops form without a pick (choose the face / edge from a list). */
  canEdit?: boolean; onEdit?(): void;
  /** The part's script instead of the model (the Code view). */
  code?: boolean; onCode?(on: boolean): void;
  /** Extra tools (U2a: Measure; G4: X-ray / section), shown in their own group. */
  extra?: readonly ViewMenuItem[];
  /** Controls shown in the same row, left of View ▾ (the "All parts" toggle — I3: in the toolbar
   *  flow, so it never floats over a panel). */
  lead?: JSX.Element | null;
}

interface Row { key: string; role: "menuitemradio" | "menuitemcheckbox" | "menuitem"; label: string; checked?: boolean; disabled?: boolean; hint?: string; run(): void }

export function ViewerTools({ mode, onMode, scope, onScope, canEdit, onEdit, code, onCode, extra = [], lead = null }: ViewerToolsProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  useDismiss(wrap, () => { if (open) setOpen(false); });

  const display: Row[] = [
    { key: "shaded", role: "menuitemradio", label: "Shaded", checked: mode === "shaded", run: () => onMode("shaded") },
    { key: "wire", role: "menuitemradio", label: "Wireframe", checked: mode === "wire", run: () => onMode("wire") },
  ];
  const scopeRows: Row[] = scope === undefined ? [] : [
    { key: "part", role: "menuitemradio", label: "This part", checked: scope === "part", run: () => onScope?.("part") },
    { key: "build", role: "menuitemradio", label: "Whole build", checked: scope === "build", run: () => onScope?.("build") },
  ];
  const tools: Row[] = extra.map((x) => ({ key: `x-${x.id}`, role: x.checked === undefined ? "menuitem" : "menuitemcheckbox", label: x.label,
    checked: x.checked, disabled: x.disabled, hint: x.hint, run: () => x.onSelect() }));
  const part: Row[] = [];
  if (onEdit) part.push({ key: "features", role: "menuitem", label: "Add a feature…", disabled: !canEdit, run: () => onEdit() });
  if (onCode) part.push({ key: "code", role: "menuitemcheckbox", label: "Part script", checked: !!code, run: () => onCode(!code) });
  const groups = [display, scopeRows, tools, part].filter((g) => g.length > 0);

  const items = (): HTMLElement[] => Array.from(menu.current?.querySelectorAll<HTMLElement>("[role^=menuitem]") ?? []);
  // where focus lands once the menu has rendered
  const landing = useRef<"first" | "last" | null>(null);
  useLayoutEffect(() => {
    if (!open || !landing.current) return;
    const l = items();
    (landing.current === "first" ? l[0] : l[l.length - 1])?.focus();
    landing.current = null;
  }, [open]);
  const show = (at: "first" | "last") => { landing.current = at; setOpen(true); };
  const close = (refocus: boolean) => { setOpen(false); if (refocus) btn.current?.focus(); };
  const pick = (r: Row) => { if (r.disabled) return; close(true); r.run(); };

  function onMenuKey(e: KeyboardEvent): void {
    const l = items();
    const i = l.indexOf(document.activeElement as HTMLElement);
    const go = (n: number) => { e.preventDefault(); l[(n + l.length) % l.length]?.focus(); };
    if (e.key === "ArrowDown") go(i + 1);
    else if (e.key === "ArrowUp") go(i - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(l.length - 1);
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(true); }
    else if (e.key === "Tab") close(false);
  }

  return (
    <div class="viewer-tools view-menu" ref={wrap}>
      {lead}
      <button ref={btn} type="button" class="vt-btn view-btn" aria-haspopup="menu" aria-expanded={open} aria-controls="view-menu"
        aria-label="View options" title="Shading, what to show, features and the part script"
        onClick={() => (open ? close(false) : show("first"))}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); show("first"); }
          else if (e.key === "ArrowUp") { e.preventDefault(); show("last"); }
        }}>
        View <span aria-hidden="true">▾</span>
      </button>
      {open && (
        <div id="view-menu" ref={menu} class="view-list" role="menu" aria-label="View" onKeyDown={onMenuKey}>
          {groups.map((g, gi) => (
            <div key={gi} role="group" class="view-group">
              {g.map((r) => (
                <button key={r.key} type="button" role={r.role} tabIndex={-1} class="view-item" data-item={r.key}
                  aria-checked={r.role === "menuitem" ? undefined : !!r.checked} aria-disabled={r.disabled || undefined}
                  onClick={() => pick(r)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(r); } }}>
                  <span class="view-mark" aria-hidden="true">{r.checked ? (r.role === "menuitemradio" ? "●" : "✓") : ""}</span>
                  <span class="view-label">{r.label}</span>
                  {r.hint && <span class="view-hint faint" aria-hidden="true">{r.hint}</span>}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
