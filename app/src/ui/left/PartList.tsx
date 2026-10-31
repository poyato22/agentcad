// The parts manager (top left, Plan 7 U8 / R20), Onshape-style: compact rows of eye · appearance
// swatch · name, with the mass and look on hover. One click on the swatch opens the appearance
// popover; right-click (or ⋯, Shift+F10) opens the part menu: Rename, Appearance, Hide/Show,
// Isolate, Measure, Add to assembly, Duplicate, Export, Properties, Delete. ⌘-click and
// Shift-click select several parts, and the menu then acts on all of them.
//
// Drag a row (from anywhere on it, its name included) to reorder or re-parent it: a dimension-line
// drop indicator shows where it lands and the list scrolls near its edges. The drag is plain
// pointer events (a 4 px threshold, hit-testing the rows), not HTML5 drag-and-drop, which a
// webview's own file-drop handling breaks. Alt+↑/↓/→/← on a row do the same by keyboard.
//
// Speed (measured in test/e2e/parts-perf.spec.ts): rows are memoized on plain values, so a click
// re-renders the rows it changed, not the list; the move plans behind the menu are computed only
// when it opens; and past VIRTUAL_AT rows only the rows in view are rendered.
import type { JSX } from "preact";
import { memo } from "preact/compat";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { appearanceName, materialOf, swatchStyle, type MaterialParams } from "../../lib/appearance";
import { partName } from "../../lib/names";
import { activeBuildId } from "../../state/builds";
import { agentFlash } from "../../state/partEdits";
import { parts, selectedPartId } from "../../state/store";
import { closePart, openPart } from "../assembly/focus";
import { ConfirmDelete } from "./ConfirmDelete";
import { masses, rowMassText } from "./mass";
import { AppearancePopover } from "./AppearancePopover";
import { swatchName } from "./colors";
import { changingParts } from "./derived";
import { extraPartMenuItems, measureHook } from "./partMenuHooks";
import { PartMenu, type MenuEntry } from "./PartMenu";
import { PartProperties } from "./PartProperties";
import { clickSelect, selectedIds, targetsFor } from "./partSelection";
import { dropPosAt, flatten, planDrop, planIndent, planOutdent, planStep, type DropPos, type Move } from "./tree";
import {
  addToAssembly, commitMove, deleteParts, duplicateParts, exportPart, instanceCount, removalOf, removeFromAssembly, renamePart, setColor, setFinish, treeError, treeNote,
} from "./treeActions";
import { hiddenParts, isolate, setHidden, showAll } from "./visibility";
import "./partlist.css";

/** Pointer travel before a press becomes a drag (a shorter one stays a click). */
export const DRAG_THRESHOLD_PX = 4;
/** Within this distance of the list's top / bottom edge a drag scrolls it. */
const EDGE_PX = 24;
/** From this many rows on, only the rows in view (plus OVERSCAN) are rendered. */
export const VIRTUAL_AT = 150;
/** Every row's height (left.css .tree-row), for the virtual window. */
export const ROW_PX = 24;
const OVERSCAN = 12;

type Pop =
  | { kind: "menu"; id: string; at: { x: number; y: number } }
  | { kind: "look"; id: string; anchor: { left: number; top: number; bottom: number } }
  | { kind: "props"; id: string; anchor: { left: number; top: number } }
  | { kind: "delete" | "remove"; id: string; ids: string[]; anchor: { left: number; top: number } }
  | null;

/** The handlers every row calls; one stable object, so rows can be memoized on their values. */
interface RowCtl {
  pointerDown(e: PointerEvent, id: string, name: string): void;
  click(e: MouseEvent, id: string): void;
  rename(id: string): void;
  renamed(id: string, v: string | null): void;
  key(e: KeyboardEvent, id: string): void;
  toggle(id: string): void;
  eye(id: string): void;
  swatch(e: MouseEvent, id: string): void;
  menu(e: MouseEvent, id: string): void;
}

interface RowProps {
  ctl: { current: RowCtl };
  id: string; name: string; label: string; depth: number; hasChildren: boolean; collapsed: boolean;
  selected: boolean; primary: boolean; busy: boolean; flash: boolean; hidden: boolean; catalog: boolean;
  /** Plan 8 G3: the build's layout skeleton (shared dimensions and datums), not a real part. */
  skeleton?: boolean;
  editing: boolean; drop: DropPos | null; dragging: boolean; lookOpen: boolean; menuOpen: boolean;
  /** materialKey() of the part's look. */
  mat: string; lookName: string; mass: string;
  /** Plan 9 B2: why the mass is what it is (a group's sum, or the reason it is unknown). */
  massNote?: string | null;
}

const materialKey = (m: MaterialParams) => `${m.color ?? ""}|${m.metalness}|${m.roughness}|${m.opacity}|${m.shiny ? 1 : 0}`;
function parseMaterial(k: string): MaterialParams {
  const [c, me, r, o, s] = k.split("|");
  return { color: c || null, metalness: Number(me), roughness: Number(r), opacity: Number(o), shiny: s === "1" };
}

const EyeOpen = () => (
  <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M1.5 8s2.4-4.5 6.5-4.5S14.5 8 14.5 8 12.1 12.5 8 12.5 1.5 8 1.5 8z" fill="none" stroke="currentColor" stroke-width="1.2" /><circle cx="8" cy="8" r="2" fill="currentColor" /></svg>
);
const EyeShut = () => (
  <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M1.5 8s2.4 4.5 6.5 4.5S14.5 8 14.5 8M3 3l10 10" fill="none" stroke="currentColor" stroke-width="1.2" /></svg>
);

const PartRow = memo(function PartRow(p: RowProps): JSX.Element {
  const c = p.ctl;
  const cls = ["tree-row", p.selected ? "sel" : "", p.busy ? "changing" : "", p.flash ? "flash" : "",
    p.dragging ? "dragging" : "", p.hidden ? "is-hidden" : "", p.drop ? `drop-${p.drop}` : ""].filter(Boolean).join(" ");
  const swatch = useMemo(() => swatchStyle(parseMaterial(p.mat)), [p.mat]);
  const hint = [p.mass, p.lookName].filter(Boolean).join(" · ");
  return (
    <li role="treeitem" aria-level={p.depth + 1} aria-selected={p.selected}
      aria-expanded={p.hasChildren ? !p.collapsed : undefined} data-part={p.id} data-depth={p.depth}>
      <div class={cls} style={{ paddingLeft: `${4 + p.depth * 14}px` }} title={hint ? `${p.name} · ${hint}` : p.name}
        data-selected={p.selected ? "" : undefined} data-changing={p.busy ? "" : undefined} data-flash={p.flash ? "" : undefined}
        data-drop={p.drop ?? undefined}
        onPointerDown={(e) => c.current.pointerDown(e, p.id, p.name)}
        onContextMenu={(e) => c.current.menu(e, p.id)}>
        {p.hasChildren
          ? <button type="button" class="tree-twisty" tabIndex={-1} aria-label={p.collapsed ? `Expand ${p.name}` : `Collapse ${p.name}`} onClick={() => c.current.toggle(p.id)}>{p.collapsed ? "▸" : "▾"}</button>
          : <span class="tree-twisty" aria-hidden="true" />}
        {/* the layout skeleton has no model: nothing to hide or colour (I4b) */}
        {p.skeleton
          ? <><span class="tree-eye" aria-hidden="true" /><span class="tree-swatch tree-swatch-none" aria-hidden="true" /></>
          : <>
          <button type="button" class="tree-eye" tabIndex={-1} aria-pressed={!p.hidden} aria-label={p.hidden ? `Show ${p.name} in the viewer` : `Hide ${p.name} in the viewer`}
            title={p.hidden ? "Hidden in the viewer: click to show" : "Shown in the viewer: click to hide"} onClick={() => c.current.eye(p.id)}>{p.hidden ? <EyeShut /> : <EyeOpen />}</button>
          <button type="button" class="tree-swatch" tabIndex={-1} data-appearance-anchor="" style={swatch} aria-haspopup="dialog" aria-expanded={p.lookOpen}
            aria-label={`Appearance of ${p.name}: ${p.lookName || swatchName(parseMaterial(p.mat).color)}`}
            title={`Appearance: ${p.lookName || swatchName(parseMaterial(p.mat).color)}`} onClick={(e) => c.current.swatch(e, p.id)} />
          </>}
        {p.editing
          ? <RenameField name={p.label} onDone={(v) => c.current.renamed(p.id, v)} />
          : (
            <button type="button" class="tree-name" aria-pressed={p.primary} title={`${p.name} — drag to move; ⌥↑↓ reorder, ⌥→← nest`}
              onClick={(e) => c.current.click(e, p.id)} onDblClick={() => c.current.rename(p.id)} onKeyDown={(e) => c.current.key(e, p.id)}>
              <span class="row-name" data-label>{p.name}</span>
              {p.catalog && <span class="tree-kind faint">catalog</span>}
              {p.skeleton && <span class="tree-kind faint" title="The build's layout skeleton: shared dimensions and datums the parts read">skeleton</span>}
              {p.busy && <span class="tag-changing">changing</span>}
            </button>
          )}
        <span class="row-hint num" data-testid="row-mass" title={p.massNote ?? undefined}>{p.mass}</span>
        <button type="button" class="tree-more" tabIndex={-1} aria-haspopup="menu" aria-expanded={p.menuOpen} aria-label={`Actions for ${p.name}`}
          title="Actions (right-click the row)" onClick={(e) => c.current.menu(e, p.id)}>⋯</button>
      </div>
    </li>
  );
});

function RenameField({ name, onDone }: { name: string; onDone(v: string | null): void }): JSX.Element {
  const [v, setV] = useState(name);
  const done = useRef(false);
  const finish = (val: string | null) => { if (done.current) return; done.current = true; onDone(val); };
  return (
    <input class="tree-rename" aria-label="Part name" value={v} ref={(el) => { if (el && !done.current && document.activeElement !== el) { el.focus(); el.select(); } }}
      onInput={(e) => setV((e.target as HTMLInputElement).value)}
      onPointerDown={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        if (e.key === "Enter") { e.preventDefault(); finish(v.trim() && v.trim() !== name ? v : null); }
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(null); }
      }}
      onBlur={() => finish(v.trim() && v.trim() !== name ? v : null)} />
  );
}

/** A stable Set while its contents stay the same (a new identity would re-render every row). */
function useStableSet(s: ReadonlySet<string>): ReadonlySet<string> {
  const ref = useRef(s);
  const prev = ref.current;
  if (prev !== s && (prev.size !== s.size || [...s].some((x) => !prev.has(x)))) ref.current = s;
  return ref.current;
}

export function PartList(): JSX.Element {
  const list = parts.value;
  const primary = selectedPartId.value;
  const selection = selectedIds.value;
  const changing = useStableSet(changingParts.value);
  const flashing = agentFlash.value;
  const hidden = hiddenParts.value;
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const [drag, setDrag] = useState<string | null>(null);
  const [drop, setDrop] = useState<{ id: string; pos: DropPos } | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [pop, setPop] = useState<Pop>(null);
  const [ghost, setGhost] = useState<{ x: number; y: number; name: string } | null>(null);
  const [win, setWin] = useState({ top: 0, height: 800 });
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

  const rows = useMemo(() => flatten(list, collapsed), [list, collapsed]);
  const massOf = useMemo(() => masses(list), [list]);
  const order = useMemo(() => rows.map((r) => r.part.part_id), [rows]);
  const orderRef = useRef(order);
  orderRef.current = order;
  const virtual = rows.length >= VIRTUAL_AT;

  // the virtual window follows the scrolling pane
  useEffect(() => {
    if (!virtual) return;
    const sc = root.current?.closest<HTMLElement>(".pane-body");
    if (!sc) return;
    const read = () => setWin({ top: Math.max(0, sc.scrollTop - (root.current?.offsetTop ?? 0)), height: sc.clientHeight || 800 });
    read();
    sc.addEventListener("scroll", read, { passive: true });
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(read);
    ro?.observe(sc);
    return () => { sc.removeEventListener("scroll", read); ro?.disconnect(); };
  }, [virtual]);

  const byId = (id: string) => listRef.current.find((p) => p.part_id === id);

  /** Scroll a row into the virtual window if needed, then put the keyboard on its name. */
  const focusRow = (id: string, onlyIfLost = false) => {
    const i = orderRef.current.indexOf(id);
    const sc = root.current?.closest<HTMLElement>(".pane-body");
    if (virtual && sc && i >= 0) {
      const y = (root.current?.offsetTop ?? 0) + i * ROW_PX;
      if (y < sc.scrollTop || y + ROW_PX > sc.scrollTop + sc.clientHeight) sc.scrollTop = y - sc.clientHeight / 2;
    }
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const a = document.activeElement;
      if (onlyIfLost && a && a !== document.body && document.contains(a)) return;
      root.current?.querySelector<HTMLElement>(`[data-part="${CSS.escape(id)}"] .tree-name`)?.focus();
    }));
  };
  const move = (m: Move | null, id: string) => {
    if (!m) return;
    void commitMove(m).then(() => focusRow(id));
  };
  const toggle = (id: string) => setCollapsed((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const closePop = (refocus: boolean, id?: string) => { setPop(null); if (refocus && id) focusRow(id, true); };

  function menuItems(id: string): MenuEntry[] {
    const all = listRef.current;
    const targets = targetsFor(id);
    const single = targets.length === 1;
    const many = single ? "" : ` ${targets.length} parts`;
    const anyShown = targets.some((t) => !hidden.has(t));
    const isBuilt = (x: string) => { const q = byId(x); return q?.metrics !== null && q?.metrics !== undefined; };
    const built = isBuilt(id);
    // a selection acts as a whole: an action some part can't take is off for all, with the reason
    const unbuilt = targets.filter((t) => !isBuilt(t));
    const allBuilt = unbuilt.length === 0;
    const whyNot = allBuilt ? undefined : `${unbuilt.map((t) => partName(byId(t) ?? { part_id: t })).join(", ")} ${unbuilt.length === 1 ? "isn't" : "aren't"} built yet`;
    const busy = targets.filter((t) => changing.has(t));
    const buildIds = all.map((x) => x.part_id);
    const extras = extraPartMenuItems.value.filter((x) => single || !x.single);
    const items: MenuEntry[] = [];
    if (single) items.push({ label: "Rename", keys: "F2", run: () => setEditing(id) });
    items.push(
      { label: "Appearance…", run: () => openLook(id) },
      { label: anyShown ? `Hide${many}` : `Show${many}`, keys: "H", run: () => setHidden(targets, anyShown) },
      // on the build view (the assembly): a part open alone goes back to it (FW-app I1)
      { label: single ? "Isolate" : `Isolate${many}`, title: "Show only this in the build view", run: () => { isolate(targets, buildIds); closePart(); } },
    );
    if (hidden.size > 0) items.push({ label: "Show all parts", run: () => showAll() });
    items.push({
      label: "Measure", disabled: !built, title: measureHook.value ? undefined : "Its size, volume and mass",
      run: () => { const h = measureHook.peek(); if (h) h(targets); else openProps(id); },
    });
    items.push("sep",
      { label: `Add to assembly${many}`, disabled: !allBuilt, title: whyNot, run: () => { void addToAssembly(targets); } },
      ...(instanceCount(targets) > 0 ? [{ label: `Remove from assembly${many}`, title: "Takes its instances out of the assembly; the part stays",
        run: () => askRemove(id) }] : []),
      { label: `Duplicate${many}`, keys: "⌘D", disabled: !allBuilt, title: whyNot, run: () => { void duplicateParts(targets); } });
    if (single) {
      items.push(
        { label: "Export STEP", disabled: !built, run: () => { void exportPart(id, "step"); } },
        { label: "Export STL", disabled: !built, run: () => { void exportPart(id, "stl"); } },
        { label: "Properties", keys: "⌘I", run: () => openProps(id) },
      );
    }
    for (const x of extras) items.push({ label: x.label, disabled: x.disabled?.(targets) ?? false, run: () => x.run(targets) });
    items.push("sep", {
      label: `Delete${many}…`, keys: "⌫", danger: true,
      // an agent changing it now would rebuild it half-deleted (the core refuses too: 409)
      disabled: busy.length > 0,
      title: busy.length ? "An agent is changing it: wait for it to finish" : "Moves its files to the trash (and its instances out of the assembly)",
      run: () => askDelete(id),
    });
    return items;
  }

  function rowRect(id: string): DOMRect | null {
    return root.current?.querySelector<HTMLElement>(`[data-part="${CSS.escape(id)}"] .tree-row`)?.getBoundingClientRect() ?? null;
  }
  function openLook(id: string): void {
    const r = root.current?.querySelector<HTMLElement>(`[data-part="${CSS.escape(id)}"] .tree-swatch`)?.getBoundingClientRect();
    setPop({ kind: "look", id, anchor: { left: r?.left ?? 0, top: r?.top ?? 0, bottom: r?.bottom ?? 0 } });
  }
  function openProps(id: string): void {
    const r = rowRect(id);
    setPop({ kind: "props", id, anchor: { left: (r?.right ?? 0) + 6, top: r?.top ?? 0 } });
  }
  /** D29: ⌫ or the menu's Delete… asks first, naming the assembly instances that go with it. */
  function askDelete(id: string): void {
    const ids = targetsFor(id);
    if (ids.some((t) => changing.has(t))) return;
    const r = rowRect(id);
    setPop({ kind: "delete", id, ids, anchor: { left: (r?.left ?? 0) + 12, top: (r?.bottom ?? 0) + 2 } });
  }
  /** Remove from assembly: straight away (Undo follows), but asked first when joints come loose. */
  function askRemove(id: string): void {
    const ids = targetsFor(id);
    if (removalOf(ids).joints === 0) { void removeFromAssembly(ids); return; }
    const r = rowRect(id);
    setPop({ kind: "remove", id, ids, anchor: { left: (r?.left ?? 0) + 12, top: (r?.bottom ?? 0) + 2 } });
  }
  function openMenu(id: string, at?: { x: number; y: number }): void {
    if (!at) {
      const r = root.current?.querySelector<HTMLElement>(`[data-part="${CSS.escape(id)}"] .tree-more`)?.getBoundingClientRect() ?? rowRect(id);
      at = { x: r ? r.left : 0, y: r ? r.bottom : 0 };
    }
    setPop({ kind: "menu", id, at });
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

  const ctl = useRef<RowCtl>(null as unknown as RowCtl);
  ctl.current = {
    pointerDown(e, id, name) {
      const t = e.target as HTMLElement;
      if (e.button !== 0 || editing === id || t.closest("input") || t.closest(".tree-swatch, .tree-eye, .tree-more, .tree-twisty")) return;
      endDrag.current?.();
      press.current = { id, name, x: e.clientX, y: e.clientY, started: false };
      const onMove = (ev: PointerEvent) => {
        const p = press.current;
        if (!p) return;
        if (!p.started) {
          if (Math.hypot(ev.clientX - p.x, ev.clientY - p.y) < DRAG_THRESHOLD_PX) return;
          p.started = true;
          setDrag(p.id);
          setPop(null);
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
    },
    click(e, id) {
      const multi = e.shiftKey || e.metaKey || e.ctrlKey;
      clickSelect(id, { shift: e.shiftKey, toggle: e.metaKey || e.ctrlKey }, orderRef.current);
      // Plan 9 D7: a plain click opens the part alone (a group has no model of its own)
      if (!multi && (byId(id)?.kind ?? "part") !== "group") openPart(id);
    },
    rename(id) { setEditing(id); },
    renamed(id, v) { setEditing(null); if (v !== null) void renamePart(id, v); focusRow(id); },
    key(e, id) {
      const l = listRef.current;
      const i = orderRef.current.indexOf(id);
      if (e.altKey && e.key === "ArrowUp") { e.preventDefault(); move(planStep(l, id, -1), id); return; }
      if (e.altKey && e.key === "ArrowDown") { e.preventDefault(); move(planStep(l, id, 1), id); return; }
      if (e.altKey && e.key === "ArrowRight") { e.preventDefault(); move(planIndent(l, id), id); return; }
      if (e.altKey && e.key === "ArrowLeft") { e.preventDefault(); move(planOutdent(l, id), id); return; }
      if (e.key === "F2") { e.preventDefault(); setEditing(id); return; }
      if (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) { e.preventDefault(); openMenu(id); return; }
      if ((e.key === "h" || e.key === "H") && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault();
        const t = targetsFor(id);
        setHidden(t, t.some((x) => !hiddenParts.peek().has(x)));
        return;
      }
      if ((e.metaKey || e.ctrlKey) && (e.key === "d" || e.key === "D")) { e.preventDefault(); void duplicateParts(targetsFor(id)); return; }
      if ((e.metaKey || e.ctrlKey) && (e.key === "i" || e.key === "I")) { e.preventDefault(); openProps(id); return; }
      if ((e.key === "Backspace" || e.key === "Delete") && !e.metaKey && !e.ctrlKey && !e.altKey) { e.preventDefault(); askDelete(id); return; }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const next = orderRef.current[i + (e.key === "ArrowDown" ? 1 : -1)];
        if (next) {
          if (e.shiftKey) clickSelect(next, { shift: true }, orderRef.current);
          focusRow(next);
        }
        return;
      }
      const r = rows[i];
      if ((e.key === "ArrowRight" || e.key === "ArrowLeft") && r?.hasChildren) {
        const isOpen = !collapsed.has(id);
        if ((e.key === "ArrowLeft") === isOpen) { e.preventDefault(); toggle(id); }
      }
    },
    toggle,
    // the eye is its own row's (Onshape); the menu and H act on the selection
    eye(id) { setHidden(id, !hiddenParts.peek().has(id)); },
    swatch(e, id) {
      e.stopPropagation();
      if (pop?.kind === "look" && pop.id === id) { setPop(null); return; }
      openLook(id);
    },
    menu(e, id) {
      e.preventDefault();
      e.stopPropagation();
      if (pop?.kind === "menu" && pop.id === id && e.type === "click") { setPop(null); return; }
      // a right-click on a row outside the selection selects it first (Finder / Onshape)
      if (!selectedIds.peek().has(id)) clickSelect(id, {}, orderRef.current);
      openMenu(id, e.type === "contextmenu" ? { x: e.clientX, y: e.clientY } : undefined);
    },
  };

  if (list.length === 0) return <div class="pane-empty">No parts in this build yet. Ask an agent to make one.</div>;

  const start = virtual ? Math.max(0, Math.floor(win.top / ROW_PX) - OVERSCAN) : 0;
  const end = virtual ? Math.min(rows.length, Math.ceil((win.top + win.height) / ROW_PX) + OVERSCAN) : rows.length;
  const popPart = pop ? byId(pop.id) ?? null : null;
  const lookTargets = pop?.kind === "look" ? targetsFor(pop.id) : [];
  const note = treeError.value ?? treeNote.value;

  return (
    <div class="tree-wrap" data-build={activeBuildId.value ?? undefined}>
      <ul class={drag ? "tree is-dragging" : "tree"} role="tree" aria-label="Parts in this build" aria-multiselectable="true" ref={root} data-testid="part-tree"
        data-virtual={virtual ? "" : undefined}
        onClickCapture={(e) => { if (suppressClick.current) { e.preventDefault(); e.stopPropagation(); suppressClick.current = false; } }}>
        {start > 0 && <li class="tree-spacer" aria-hidden="true" style={{ height: `${start * ROW_PX}px` }} />}
        {rows.slice(start, end).map((r) => {
          const p = r.part;
          const id = p.part_id;
          const m = materialOf(p);
          return (
            <PartRow key={id} ctl={ctl} id={id} name={partName(p)} label={p.label ?? partName(p)} depth={r.depth} hasChildren={r.hasChildren}
              collapsed={collapsed.has(id)} selected={selection.has(id)} primary={id === primary} busy={changing.has(id)} flash={flashing.has(id)}
              hidden={hidden.has(id)} catalog={p.kind === "catalog"} skeleton={p.kind === "skeleton"} editing={editing === id} drop={drop?.id === id ? drop.pos : null}
              dragging={drag === id} lookOpen={pop?.kind === "look" && pop.id === id} menuOpen={pop?.kind === "menu" && pop.id === id}
              mat={materialKey(m)} lookName={appearanceName(p.appearance) ?? ""}
              mass={rowMassText(p, massOf.get(id))} massNote={massOf.get(id)?.note ?? null} />
          );
        })}
        {end < rows.length && <li class="tree-spacer" aria-hidden="true" style={{ height: `${(rows.length - end) * ROW_PX}px` }} />}
      </ul>
      {ghost && <div class="tree-ghost" aria-hidden="true" style={{ left: `${ghost.x + 12}px`, top: `${ghost.y + 8}px` }}>{ghost.name}</div>}
      {note && <div class={treeError.value ? "tree-error" : "tree-note"} role={treeError.value ? "alert" : "status"}>{note}</div>}
      {pop?.kind === "menu" && popPart && (
        <PartMenu items={menuItems(pop.id)} at={pop.at} label={`${partName(popPart)} actions`} onClose={(re) => closePop(re, pop.id)} />
      )}
      {pop?.kind === "look" && popPart && (
        <AppearancePopover key={pop.id} color={popPart.color ?? null} appearance={popPart.appearance ?? null}
          title={lookTargets.length > 1 ? `${lookTargets.length} parts` : partName(popPart)} anchor={pop.anchor}
          onFinish={(f) => { void setFinish(lookTargets, f); }} onColor={(c) => { void setColor(lookTargets, c); }}
          onClose={() => closePop(true, pop.id)} />
      )}
      {pop?.kind === "remove" && popPart && (() => {
        const r = removalOf(pop.ids);
        const loose = r.loose.slice(0, 3).join(", ") + (r.loose.length > 3 ? "…" : "");
        return (
          <ConfirmDelete names={[]} instances={r.instances} anchor={pop.anchor}
            text={{ title: `Remove ${r.instances === 1 ? "1 instance" : `${r.instances} instances`} from the assembly?`,
              detail: `${r.joints === 1 ? "1 joint comes" : `${r.joints} joints come`} loose${loose ? ` (${loose})` : ""}. The part stays; you can undo this.`, action: "Remove" }}
            onConfirm={() => { const ids = pop.ids; closePop(false); void removeFromAssembly(ids); }} onClose={() => closePop(true, pop.id)} />
        );
      })()}
      {pop?.kind === "delete" && popPart && (
        <ConfirmDelete names={pop.ids.map((t) => partName(byId(t) ?? { part_id: t }))} instances={instanceCount(pop.ids)} anchor={pop.anchor}
          onConfirm={() => { const ids = pop.ids; closePop(false); void deleteParts(ids); }} onClose={() => closePop(true, pop.id)} />
      )}
      {pop?.kind === "props" && popPart && (
        <PartProperties key={pop.id} part={popPart} anchor={pop.anchor} onClose={() => closePop(true, pop.id)} />
      )}
    </div>
  );
}
