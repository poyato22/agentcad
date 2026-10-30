// The part tree (top left): the active build's parts and groups, nested. Drag a row (from anywhere
// on it, its name included) to reorder or re-parent it: a dimension-line drop indicator shows where
// it lands and the list scrolls near its edges. The drag is plain pointer events (a 4 px threshold,
// hit-testing the rows), not HTML5 drag-and-drop, which a webview's own file-drop handling breaks.
// Alt+↑/↓/→/← and the row's Move menu do the same by keyboard. Each row has a color chip (palette + custom hex, applied in
// the viewer), its name (F2 or double-click renames), its mass and a visibility toggle. A part an
// agent just changed flashes; one an agent is changing now is marked "changing".
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { PartSummary } from "../../api/types";
import { fmtGrams } from "../../lib/format";
import { partName } from "../../lib/names";
import { agentFlash } from "../../state/partEdits";
import { actions, parts, selectedPartId } from "../../state/store";
import { ColorPicker } from "./ColorPicker";
import { useDismissRow } from "./dismissRow";
import { swatchName } from "./colors";
import { changingParts } from "./derived";
import { dropPosAt, flatten, planDrop, planIndent, planOutdent, planStep, type DropPos, type Move } from "./tree";
import { commitMove, renamePart, setColor, treeError } from "./treeActions";
import { hiddenParts, setHidden } from "./visibility";

/** Pointer travel before a press becomes a drag (a shorter one stays a click). */
export const DRAG_THRESHOLD_PX = 4;
/** Within this distance of the list's top / bottom edge a drag scrolls it. */
const EDGE_PX = 24;

type MenuItem = { label: string; keys?: string; run: () => void; disabled?: boolean };

export function PartTree(): JSX.Element {
  const list = parts.value;
  const sel = selectedPartId.value;
  const changing = changingParts.value;
  const flashing = agentFlash.value;
  const hidden = hiddenParts.value;
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const [drag, setDrag] = useState<string | null>(null);
  const [drop, setDrop] = useState<{ id: string; pos: DropPos } | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [coloring, setColoring] = useState<string | null>(null);
  const [menu, setMenu] = useState<string | null>(null);
  const [ghost, setGhost] = useState<{ x: number; y: number; name: string } | null>(null);
  const root = useRef<HTMLUListElement>(null);
  // the drag runs in window listeners: they read the latest values through refs
  const listRef = useRef(list);
  listRef.current = list;
  const press = useRef<{ id: string; name: string; x: number; y: number; started: boolean } | null>(null);
  const dropRef = useRef<{ id: string; pos: DropPos } | null>(null);
  const suppressClick = useRef(false);
  const scrollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const endDrag = useRef<(() => void) | null>(null);
  useEffect(() => () => endDrag.current?.(), []);

  if (list.length === 0) return <div class="pane-empty">No parts in this build yet. Ask an agent to make one.</div>;
  const rows = flatten(list, collapsed);

  const focusRow = (id: string) => {
    // after a move the row re-renders in its new place: keep the keyboard on it
    requestAnimationFrame(() => root.current?.querySelector<HTMLElement>(`[data-part="${CSS.escape(id)}"] .tree-name`)?.focus());
  };
  const move = (m: Move | null, id: string) => {
    if (!m) return;
    void commitMove(m).then(() => focusRow(id));
  };
  const toggle = (id: string) => setCollapsed((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  function onNameKey(e: KeyboardEvent, p: PartSummary, i: number): void {
    const id = p.part_id;
    if (e.altKey && e.key === "ArrowUp") { e.preventDefault(); move(planStep(list, id, -1), id); return; }
    if (e.altKey && e.key === "ArrowDown") { e.preventDefault(); move(planStep(list, id, 1), id); return; }
    if (e.altKey && e.key === "ArrowRight") { e.preventDefault(); move(planIndent(list, id), id); return; }
    if (e.altKey && e.key === "ArrowLeft") { e.preventDefault(); move(planOutdent(list, id), id); return; }
    if (e.key === "F2") { e.preventDefault(); setEditing(id); return; }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = rows[i + (e.key === "ArrowDown" ? 1 : -1)];
      if (next) root.current?.querySelector<HTMLElement>(`[data-part="${CSS.escape(next.part.part_id)}"] .tree-name`)?.focus();
      return;
    }
    if ((e.key === "ArrowRight" || e.key === "ArrowLeft") && rows[i].hasChildren) {
      const isOpen = !collapsed.has(id);
      if ((e.key === "ArrowLeft") === isOpen) { e.preventDefault(); toggle(id); }
    }
  }

  /** The row under a pointer height, and which zone of it (before / inside / after). */
  function hitRow(y: number): { id: string; pos: DropPos } | null {
    for (const el of root.current?.querySelectorAll<HTMLElement>("[data-part] > .tree-row") ?? []) {
      const r = el.getBoundingClientRect();
      if (y >= r.top && y < r.bottom) {
        const id = el.parentElement?.getAttribute("data-part");
        return id ? { id, pos: dropPosAt(y - r.top, r.height) } : null;
      }
    }
    return null;
  }

  function stopScroll(): void {
    if (scrollTimer.current) clearInterval(scrollTimer.current);
    scrollTimer.current = null;
  }

  /** Near the list's top / bottom edge, keep scrolling while the pointer stays there. */
  function autoScroll(y: number): void {
    const sc = root.current?.closest<HTMLElement>(".pane-body");
    if (!sc) return;
    const r = sc.getBoundingClientRect();
    const dir = y < r.top + EDGE_PX ? -1 : y > r.bottom - EDGE_PX ? 1 : 0;
    stopScroll();
    if (dir) scrollTimer.current = setInterval(() => { sc.scrollTop += dir * 6; }, 16);
  }

  function onRowPointerDown(e: PointerEvent, id: string, name: string): void {
    if (e.button !== 0 || editing === id || (e.target as HTMLElement).closest("input")) return;
    endDrag.current?.();
    press.current = { id, name, x: e.clientX, y: e.clientY, started: false };
    const onMove = (ev: PointerEvent) => {
      const p = press.current;
      if (!p) return;
      if (!p.started) {
        if (Math.hypot(ev.clientX - p.x, ev.clientY - p.y) < DRAG_THRESHOLD_PX) return;
        p.started = true;
        setDrag(p.id);
        setMenu(null);
        setColoring(null);
      }
      ev.preventDefault();
      setGhost({ x: ev.clientX, y: ev.clientY, name: p.name });
      const h = hitRow(ev.clientY);
      // only a real move is offered: never into the part itself or its own subtree
      const next = h && planDrop(listRef.current, p.id, h.id, h.pos) ? h : null;
      if (next?.id !== dropRef.current?.id || next?.pos !== dropRef.current?.pos) { dropRef.current = next; setDrop(next); }
      autoScroll(ev.clientY);
    };
    const finish = (commit: boolean) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onCancel);
      window.removeEventListener("keydown", onKey, true);
      stopScroll();
      const p = press.current;
      const d = dropRef.current;
      press.current = null;
      dropRef.current = null;
      endDrag.current = null;
      if (!p?.started) return;
      // the press ends on some row's button: that click is the drag's, not a selection
      suppressClick.current = true;
      setTimeout(() => { suppressClick.current = false; }, 0);
      setDrag(null);
      setDrop(null);
      setGhost(null);
      if (commit && d) move(planDrop(listRef.current, p.id, d.id, d.pos), p.id);
    };
    const onUp = () => finish(true);
    const onCancel = () => finish(false);
    const onKey = (ev: KeyboardEvent) => { if (ev.key === "Escape" && press.current?.started) { ev.preventDefault(); ev.stopPropagation(); finish(false); } };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onCancel);
    window.addEventListener("keydown", onKey, true);
    endDrag.current = () => finish(false);
  }

  return (
    <div class="tree-wrap">
      <ul class={drag ? "tree is-dragging" : "tree"} role="tree" aria-label="Parts in this build" ref={root} data-testid="part-tree"
        onClickCapture={(e) => { if (suppressClick.current) { e.preventDefault(); e.stopPropagation(); suppressClick.current = false; } }}>
        {rows.map((r, i) => {
          const p = r.part;
          const id = p.part_id;
          const name = partName(p);
          const isSel = id === sel;
          const busy = changing.has(id);
          const isHidden = hidden.has(id);
          const dropHere = drop?.id === id ? drop.pos : null;
          const items: MenuItem[] = [
            { label: "Move up", keys: "⌥↑", run: () => move(planStep(list, id, -1), id), disabled: !planStep(list, id, -1) },
            { label: "Move down", keys: "⌥↓", run: () => move(planStep(list, id, 1), id), disabled: !planStep(list, id, 1) },
            { label: "Move into the part above", keys: "⌥→", run: () => move(planIndent(list, id), id), disabled: !planIndent(list, id) },
            { label: "Move out of its parent", keys: "⌥←", run: () => move(planOutdent(list, id), id), disabled: !planOutdent(list, id) },
            { label: "Rename", keys: "F2", run: () => setEditing(id) },
            { label: "Color…", run: () => setColoring(id) },
            { label: isHidden ? "Show in the viewer" : "Hide in the viewer", run: () => { setHidden(id, !isHidden); focusRow(id); } },
          ];
          const cls = ["tree-row", isSel ? "sel" : "", busy ? "changing" : "", flashing.has(id) ? "flash" : "",
            drag === id ? "dragging" : "", isHidden ? "is-hidden" : "", dropHere ? `drop-${dropHere}` : ""].filter(Boolean).join(" ");
          return (
            <li key={id} role="treeitem" aria-level={r.depth + 1} aria-selected={isSel}
              aria-expanded={r.hasChildren ? !collapsed.has(id) : undefined} data-part={id} data-depth={r.depth}>
              <div class={cls} style={{ paddingLeft: `${4 + r.depth * 14}px` }}
                data-selected={isSel ? "" : undefined} data-changing={busy ? "" : undefined} data-flash={flashing.has(id) ? "" : undefined}
                data-drop={dropHere ?? undefined}
                onPointerDown={(e) => onRowPointerDown(e, id, name)}>
                {r.hasChildren
                  ? <button type="button" class="tree-twisty" tabIndex={-1} aria-label={collapsed.has(id) ? `Expand ${name}` : `Collapse ${name}`} onClick={() => toggle(id)}>{collapsed.has(id) ? "▸" : "▾"}</button>
                  : <span class="tree-twisty" aria-hidden="true" />}
                <button type="button" class="tree-chip" data-color={p.color ?? ""} style={p.color ? { background: p.color } : undefined}
                  title={`Color: ${swatchName(p.color)}`} aria-label={`Color of ${name}: ${swatchName(p.color)}`} aria-expanded={coloring === id}
                  onClick={() => { setMenu(null); setColoring(coloring === id ? null : id); }} />
                {editing === id
                  ? <RenameField name={p.label ?? name} onDone={(v) => { setEditing(null); if (v !== null) void renamePart(id, v); focusRow(id); }} />
                  : (
                    <button type="button" class="tree-name" aria-pressed={isSel} title={`${name} — drag to move; ⌥↑↓ reorder, ⌥→← nest`}
                      onClick={() => actions.selectPart(id)} onDblClick={() => setEditing(id)} onKeyDown={(e) => onNameKey(e, p, i)}>
                      <span class="row-name" data-label>{name}</span>
                      {p.kind === "catalog" && <span class="tree-kind faint">catalog</span>}
                      {busy && <span class="tag-changing">changing</span>}
                    </button>
                  )}
                <span class="row-right faint">{p.metrics ? fmtGrams(p.metrics.mass_g, 1) : ""}</span>
                <button type="button" class="tree-eye" aria-pressed={!isHidden} aria-label={isHidden ? `Show ${name} in the viewer` : `Hide ${name} in the viewer`}
                  title={isHidden ? "Hidden in the viewer" : "Shown in the viewer"} onClick={() => setHidden(id, !isHidden)}>{isHidden ? "◌" : "●"}</button>
                <button type="button" class="tree-more" aria-haspopup="menu" aria-expanded={menu === id} aria-label={`Move or edit ${name}`}
                  onClick={() => { setColoring(null); setMenu(menu === id ? null : id); }}>⋯</button>
              </div>
              {menu === id && <RowMenu items={items} label={`${name} actions`} onPicked={() => setMenu(null)} onClose={() => { setMenu(null); focusRow(id); }} />}
              {coloring === id && (
                <ColorPicker value={p.color ?? null} partName={name}
                  onPick={(c) => { setColoring(null); void setColor(id, c); focusRow(id); }}
                  onClose={() => { setColoring(null); focusRow(id); }} />
              )}
            </li>
          );
        })}
      </ul>
      {ghost && <div class="tree-ghost" aria-hidden="true" style={{ left: `${ghost.x + 12}px`, top: `${ghost.y + 8}px` }}>{ghost.name}</div>}
      {treeError.value && <div class="tree-error" role="alert">{treeError.value}</div>}
    </div>
  );
}

function RenameField({ name, onDone }: { name: string; onDone(v: string | null): void }): JSX.Element {
  const [v, setV] = useState(name);
  const done = useRef(false);
  const finish = (val: string | null) => { if (done.current) return; done.current = true; onDone(val); };
  return (
    <input class="tree-rename" aria-label="Part name" value={v} ref={(el) => { if (el && !done.current && document.activeElement !== el) { el.focus(); el.select(); } }}
      onInput={(e) => setV((e.target as HTMLInputElement).value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") { e.preventDefault(); finish(v.trim() && v.trim() !== name ? v : null); }
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(null); }
      }}
      onBlur={() => finish(v.trim() && v.trim() !== name ? v : null)} />
  );
}

function RowMenu({ items, label, onPicked, onClose }: { items: MenuItem[]; label: string; onPicked(): void; onClose(): void }): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  useDismissRow(ref, onPicked);
  const focusables = () => [...(ref.current?.querySelectorAll<HTMLButtonElement>("button:not([disabled])") ?? [])];
  return (
    <div class="tree-menu" role="menu" aria-label={label} ref={(el) => { ref.current = el; if (el && !el.contains(document.activeElement)) el.querySelector<HTMLButtonElement>("button:not([disabled])")?.focus(); }}
      onKeyDown={(e) => {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); return; }
        if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
        e.preventDefault();
        const f = focusables();
        const i = f.indexOf(document.activeElement as HTMLButtonElement);
        f[(i + (e.key === "ArrowDown" ? 1 : -1) + f.length) % f.length]?.focus();
      }}>
      {items.map((it) => (
        <button key={it.label} type="button" role="menuitem" class="tree-menu-item" disabled={it.disabled}
          onClick={() => { onPicked(); it.run(); }}>
          <span>{it.label}</span>{it.keys && <span class="faint">{it.keys}</span>}
        </button>
      ))}
    </div>
  );
}
