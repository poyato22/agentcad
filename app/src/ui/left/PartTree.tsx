// The part tree (top left): the active build's parts and groups, nested. Drag a row to reorder or
// re-parent it (a dimension-line drop indicator shows where it lands); Alt+↑/↓/→/← and the row's
// Move menu do the same by keyboard. Each row has a color chip (palette + custom hex, applied in
// the viewer), its name (F2 or double-click renames), its mass and a visibility toggle. A part an
// agent just changed flashes; one an agent is changing now is marked "changing".
import type { JSX } from "preact";
import { useRef, useState } from "preact/hooks";
import type { PartSummary } from "../../api/types";
import { fmtGrams } from "../../lib/format";
import { partName } from "../../lib/names";
import { agentFlash } from "../../state/partEdits";
import { actions, parts, selectedPartId } from "../../state/store";
import { ColorPicker } from "./ColorPicker";
import { swatchName } from "./colors";
import { changingParts } from "./derived";
import { dropPosAt, flatten, planDrop, planIndent, planOutdent, planStep, type DropPos, type Move } from "./tree";
import { commitMove, renamePart, setColor, treeError } from "./treeActions";
import { hiddenParts, setHidden } from "./visibility";

const DRAG_TYPE = "application/x-mises-part";

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
  const root = useRef<HTMLUListElement>(null);

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

  function onDragOver(e: DragEvent, id: string): void {
    if (!drag || !e.dataTransfer?.types.includes(DRAG_TYPE)) return;
    const el = e.currentTarget as HTMLElement;
    const r = el.getBoundingClientRect();
    const pos = dropPosAt(e.clientY - r.top, r.height);
    if (!planDrop(list, drag, id, pos)) { setDrop(null); return; }   // no move / would nest in itself
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (drop?.id !== id || drop.pos !== pos) setDrop({ id, pos });
  }

  function onDrop(e: DragEvent, id: string): void {
    e.preventDefault();
    const from = drag ?? e.dataTransfer?.getData(DRAG_TYPE) ?? null;
    const pos = drop?.id === id ? drop.pos : null;
    setDrag(null);
    setDrop(null);
    if (from && pos) move(planDrop(list, from, id, pos), from);
  }

  return (
    <div class="tree-wrap">
      <ul class="tree" role="tree" aria-label="Parts in this build" ref={root} data-testid="part-tree">
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
              <div class={cls} style={{ paddingLeft: `${4 + r.depth * 14}px` }} draggable={editing !== id}
                data-selected={isSel ? "" : undefined} data-changing={busy ? "" : undefined} data-flash={flashing.has(id) ? "" : undefined}
                data-drop={dropHere ?? undefined}
                onDragStart={(e) => { setDrag(id); setMenu(null); e.dataTransfer?.setData(DRAG_TYPE, id); e.dataTransfer?.setData("text/plain", name); if (e.dataTransfer) e.dataTransfer.effectAllowed = "move"; }}
                onDragEnd={() => { setDrag(null); setDrop(null); }}
                onDragOver={(e) => onDragOver(e, id)}
                onDragLeave={(e) => { if (drop?.id === id && !(e.currentTarget as HTMLElement).contains(e.relatedTarget as Node)) setDrop(null); }}
                onDrop={(e) => onDrop(e, id)}>
                {r.hasChildren
                  ? <button type="button" class="tree-twisty" tabIndex={-1} aria-label={collapsed.has(id) ? `Expand ${name}` : `Collapse ${name}`} onClick={() => toggle(id)}>{collapsed.has(id) ? "▸" : "▾"}</button>
                  : <span class="tree-twisty" aria-hidden="true" />}
                <button type="button" class="tree-chip" data-color={p.color ?? ""} style={p.color ? { background: p.color } : undefined}
                  title={`Color: ${swatchName(p.color)}`} aria-label={`Color of ${name}: ${swatchName(p.color)}`} aria-expanded={coloring === id}
                  onClick={() => setColoring(coloring === id ? null : id)} />
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
                  onClick={() => setMenu(menu === id ? null : id)}>⋯</button>
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
