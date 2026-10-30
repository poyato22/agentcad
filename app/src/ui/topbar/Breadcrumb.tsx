// "◇ LOCAL / [build tabs] / part". Each open build is a tab with its own parts, runs and
// assembly: click (or ←/→ then Enter) switches, × closes, double-click or F2 renames, + makes a
// new build. The ▾ menu opens any other build, moves the active one to the Trash, and lists the
// Trash (Restore, Empty trash — the only step that deletes, and it asks first).
import type { JSX } from "preact";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import type { Build } from "../../api/builds";
import { partName, runLabel } from "../../lib/names";
import {
  activeBuild, activeBuildId, buildActions, buildsError, liveBuilds, openBuildIds, openBuilds, trashBlocked, trashedBuilds,
} from "../../state/builds";
import { activePart } from "../../state/selection";
import { actions, runs, selectedPartId } from "../../state/store";
import { setConsoleCollapsed } from "../pages/router";
import { useDismiss } from "../common/dismiss";

/** The part the viewer and Variables show (activePart); a fallback part is shown plain, not as selected. */
export function Breadcrumb(): JSX.Element {
  const part = activePart.value;
  const chosen = part !== null && selectedPartId.value === part.part_id;
  return (
    <nav class="crumb" aria-label="location">
      <span class="crumb-root">◇ LOCAL</span>
      <span class="crumb-sep"> / </span>
      {openBuilds.value.length > 0 && <><BuildTabs /><span class="crumb-sep"> / </span></>}
      {part === null
        ? <span class="crumb-none" data-crumb="part">no part</span>
        : <span class={chosen ? "crumb-part" : "crumb-fallback"} data-crumb="part" data-fallback={chosen ? undefined : "true"}>
            {`Part · ${partName(part)}`}
          </span>}
    </nav>
  );
}

function NameField({ initial, label, onDone }: { initial: string; label: string; onDone(v: string | null): void }): JSX.Element {
  const [v, setV] = useState(initial);
  const done = useRef(false);
  const finish = (x: string | null) => { if (done.current) return; done.current = true; onDone(x); };
  return (
    <input class="btab-input" aria-label={label} value={v} maxLength={80} placeholder="Build name"
      ref={(el) => { if (el && !done.current && document.activeElement !== el) { el.focus(); el.select(); } }}
      onInput={(e) => setV((e.target as HTMLInputElement).value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") { e.preventDefault(); finish(v.trim() ? v.trim() : null); }
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(null); }
      }}
      onBlur={() => finish(v.trim() && v.trim() !== initial ? v.trim() : null)} />
  );
}

/** A tab never gets narrower than this (its name ellipsizes inside) nor wider than TAB_MAX. */
export const TAB_MIN = 84;
export const TAB_MAX = 168;
/** Iosevka at the 12px UI size: 0.5em per character. */
const CHAR_PX = 6.2;
/** Room kept for the part crumb ("Part · name"); a longer one ellipsizes. */
const PART_ROOM = 120;
/** The "+N" chip that holds the tabs that don't fit. */
const MORE_PX = 34;

/** A tab's width for its name: padding, the × when tabs can close, clamped to [TAB_MIN, TAB_MAX]. */
export function tabWidth(name: string, closable: boolean): number {
  return Math.min(TAB_MAX, Math.max(TAB_MIN, Math.ceil(18 + name.length * CHAR_PX + (closable ? 18 : 0))));
}

/**
 * Which open tabs show in `avail` px: the active one always, then the others in their order while
 * they fit (leaving room for the "+N" chip when some don't). The rest go behind "+N" — tabs never
 * shrink below TAB_MIN, so a name stays readable.
 */
export function fitTabs<T extends { id: string; name: string }>(tabs: readonly T[], active: string | null, avail: number): { shown: T[]; hidden: T[] } {
  if (!Number.isFinite(avail)) return { shown: [...tabs], hidden: [] };
  const closable = tabs.length > 1;
  const w = (t: T) => tabWidth(t.name, closable) + 2;
  const total = tabs.reduce((s, t) => s + w(t), 0);
  if (total <= avail) return { shown: [...tabs], hidden: [] };
  const room = avail - MORE_PX;
  const keep = new Set<string>();
  let used = 0;
  const act = tabs.find((t) => t.id === active) ?? tabs[0];
  if (act) { keep.add(act.id); used += w(act); }
  for (const t of tabs) {
    if (keep.has(t.id)) continue;
    if (used + w(t) > room) break;
    keep.add(t.id);
    used += w(t);
  }
  return { shown: tabs.filter((t) => keep.has(t.id)), hidden: tabs.filter((t) => !keep.has(t.id)) };
}

export function BuildTabs(): JSX.Element {
  const open = openBuilds.value;
  const active = activeBuildId.value;
  const wrap = useRef<HTMLSpanElement>(null);
  const [avail, setAvail] = useState(Infinity);
  const { shown: tabs, hidden } = fitTabs(open, active, avail);
  const openKey = open.map((b) => `${b.id}:${b.name}`).join("|");

  // the room for tabs: the top bar's left column minus everything else in it (LOCAL, the part
  // crumb, the + ▾ +N buttons); re-measured when the window or the tabs change
  const measure = () => {
    const el = wrap.current;
    const nav = el?.closest<HTMLElement>(".crumb");
    const left = nav?.parentElement;
    if (!el || !nav || !left) return;
    let others = 0;
    // the part crumb counts at most PART_ROOM: a long name ellipsizes rather than starve the tabs
    for (const c of Array.from(nav.children)) {
      if (c === el) continue;
      const cw = (c as HTMLElement).getBoundingClientRect().width;
      others += c.getAttribute("data-crumb") === "part" ? Math.min(cw, PART_ROOM) : cw;
    }
    for (const c of Array.from(el.children)) if (!c.classList.contains("btab-list") && !c.classList.contains("btab-more")) others += (c as HTMLElement).getBoundingClientRect().width;
    // I6: the left column also holds the Home button and the traffic-light inset (its padding);
    // leaving them out let 4 tabs at 1280 px overflow under "+", whose click then closed a tab
    for (const c of Array.from(left.children)) if (c !== nav) others += (c as HTMLElement).getBoundingClientRect().width;
    const cs = getComputedStyle(left);
    const pad = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
    // the gaps between the tab cells (2px each) and the chips beside them
    const gaps = (el.children.length + 1) * 2;
    const w = left.clientWidth - pad - others - gaps - 16;
    if (w > 0) setAvail((a) => (Math.abs(a - w) < 1 ? a : w));
  };
  useLayoutEffect(measure, [openKey]);
  // the widths above are estimates (tabWidth); if the shown tabs still don't fit (the list clips
  // them, .btab-list overflow:hidden), take the overflow off the room so one more goes behind "+N"
  useLayoutEffect(() => {
    const l = list.current;
    if (!l || !Number.isFinite(avail) || tabs.length <= 1) return;
    const over = l.scrollWidth - l.clientWidth;
    if (over > 1) setAvail((a) => a - over);
  });
  useEffect(() => {
    const left = wrap.current?.closest<HTMLElement>(".crumb")?.parentElement;
    if (!left || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => measure());
    ro.observe(left);
    return () => ro.disconnect();
  }, []);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [menuAt, setMenuAt] = useState<{ left: number; top: number } | null>(null);
  const menuOpen = menuAt !== null;
  const setMenuOpen = (on: boolean) => {
    const r = on ? menuWrap.current?.getBoundingClientRect() : undefined;
    setMenuAt(r ? { left: r.left, top: r.bottom + 4 } : null);
  };
  const list = useRef<HTMLDivElement>(null);
  const menuWrap = useRef<HTMLSpanElement>(null);
  useDismiss(menuWrap, () => { if (menuOpen) setMenuOpen(false); });

  const focusTab = (i: number) => list.current?.querySelectorAll<HTMLButtonElement>("[role=tab]")[i]?.focus();
  const moreBtn = useRef<HTMLButtonElement>(null);

  function onKeyDown(e: KeyboardEvent, i: number, b: Build): void {
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      focusTab((i + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length);
    } else if (e.key === "Home") { e.preventDefault(); focusTab(0); }
    else if (e.key === "End") { e.preventDefault(); focusTab(tabs.length - 1); }
    else if (e.key === "F2") { e.preventDefault(); setRenaming(b.id); }
  }

  return (
    <span class="btabs" data-testid="build-tabs" ref={wrap}>
      <div ref={list} class="btab-list" role="tablist" aria-label="Open builds">
        {tabs.map((b, i) => {
          const on = b.id === active;
          if (renaming === b.id) {
            return (
              <span key={b.id} class={on ? "btab btab-on" : "btab"}>
                <NameField initial={b.name} label={`Rename ${b.name}`} onDone={(v) => {
                  setRenaming(null);
                  if (v && v !== b.name) void buildActions.rename(b.id, v);
                  requestAnimationFrame(() => focusTab(i));
                }} />
              </span>
            );
          }
          return (
            <span key={b.id} class={on ? "btab btab-on" : "btab"} data-build={b.id} data-selected={on ? "" : undefined}>
              <button type="button" role="tab" class="btab-name" aria-selected={on} tabIndex={on ? 0 : -1}
                title={`${b.name} — ${b.part_count} part${b.part_count === 1 ? "" : "s"}. Double-click or F2 to rename`}
                data-crumb="build" onClick={() => { void buildActions.switchTo(b.id); }} onDblClick={() => setRenaming(b.id)}
                onKeyDown={(e) => onKeyDown(e, i, b)}>{b.name}</button>
              {open.length > 1 && (
                <button type="button" class="btab-close" aria-label={`Close ${b.name}`} title="Close this tab (the build stays; reopen it from ▾)"
                  onClick={() => { void buildActions.close(b.id); }}>×</button>
              )}
            </span>
          );
        })}
      </div>
      {hidden.length > 0 && (
        <button type="button" ref={moreBtn} class="btab-add btab-more" aria-haspopup="menu" aria-label={`${hidden.length} more open build${hidden.length === 1 ? "" : "s"}`}
          title={hidden.map((b) => b.name).join(", ")} onClick={() => {
            const r = moreBtn.current?.getBoundingClientRect();
            setMenuAt(r ? { left: r.left, top: r.bottom + 4 } : null);
          }}>{`+${hidden.length}`}</button>
      )}
      {creating
        ? <span class="btab"><NameField initial="" label="New build name" onDone={(v) => { setCreating(false); if (v) void buildActions.create(v); }} /></span>
        : <button type="button" class="btab-add" aria-label="Add a build" title="New build" onClick={() => setCreating(true)}>+</button>}
      <span class="bmenu-wrap" ref={menuWrap}>
        <button type="button" class="btab-add" aria-haspopup="menu" aria-expanded={menuOpen} aria-label="All builds and the Trash"
          title="All builds and the Trash" onClick={() => setMenuOpen(!menuOpen)}>▾</button>
        {menuAt && <BuildsMenu at={menuAt} onClose={() => setMenuOpen(false)} onRename={(id) => { setMenuOpen(false); setRenaming(id); }} />}
      </span>
      {buildsError.value && (
        <span class="btab-error" role="alert">
          {buildsError.value}
          <button type="button" class="btab-close" aria-label="Dismiss" onClick={() => { buildsError.value = null; }}>×</button>
        </span>
      )}
    </span>
  );
}

function BuildsMenu({ at, onClose, onRename }: { at: { left: number; top: number }; onClose(): void; onRename(id: string): void }): JSX.Element {
  const live = liveBuilds.value;
  const trash = trashedBuilds.value;
  const open = new Set(openBuildIds.value);
  const cur = activeBuild.value;
  const [confirmEmpty, setConfirmEmpty] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const blocked = trashBlocked.value;
  const confirmFocused = useRef(false);
  const ref = useRef<HTMLDivElement>(null);
  const run = (f: () => void) => { onClose(); f(); };
  return (
    <div class="bmenu" role="menu" aria-label="Builds" style={{ left: `${at.left}px`, top: `${at.top}px` }} ref={(el) => { ref.current = el; if (el && !el.contains(document.activeElement)) el.querySelector<HTMLButtonElement>("button")?.focus(); }}
      onKeyDown={(e) => {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); return; }
        if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
        if (e.target instanceof HTMLInputElement) return;   // a rename field
        e.preventDefault();
        const f = [...(ref.current?.querySelectorAll<HTMLButtonElement>("button:not([disabled])") ?? [])];
        const i = f.indexOf(document.activeElement as HTMLButtonElement);
        f[(i + (e.key === "ArrowDown" ? 1 : -1) + f.length) % f.length]?.focus();
      }}>
      <div class="bmenu-head label">Builds</div>
      {live.map((b) => {
        if (renamingId === b.id) {
          return (
            <div key={b.id} class="bmenu-row" data-build-row={b.id}>
              <NameField initial={b.name} label={`Rename ${b.name}`} onDone={(v) => {
                setRenamingId(null);
                if (v && v !== b.name) void buildActions.rename(b.id, v);
                requestAnimationFrame(() => ref.current?.querySelector<HTMLButtonElement>(`[data-build-row="${CSS.escape(b.id)}"] button`)?.focus());
              }} />
            </div>
          );
        }
        const isCur = b.id === cur?.id;
        return (
          <div key={b.id} class="bmenu-row" data-build-row={b.id}>
            <button type="button" role="menuitemradio" aria-checked={isCur} class="bmenu-item"
              onClick={() => run(() => { void buildActions.switchTo(b.id); })}>
              <span>{b.name}</span>
              <span class="faint">{isCur ? "active" : open.has(b.id) ? "open" : `${b.part_count} part${b.part_count === 1 ? "" : "s"}`}</span>
            </button>
            {/* any build, a tab hidden behind "+N" included, can be renamed or trashed from here;
                the active one has the items below */}
            {!isCur && (
              <>
                <button type="button" role="menuitem" class="bmenu-act" aria-label={`Rename build ${b.name}`} title="Rename"
                  onClick={() => setRenamingId(b.id)}>rename</button>
                <button type="button" role="menuitem" class="bmenu-act" aria-label={`Move build ${b.name} to the Trash`} title="Move to the Trash"
                  onClick={() => { void buildActions.trash(b.id); }}>trash</button>
              </>
            )}
          </div>
        );
      })}
      {cur && (
        <>
          <div class="bmenu-sep" />
          <button type="button" role="menuitem" class="bmenu-item" onClick={() => onRename(cur.id)}><span>{`Rename “${cur.name}”`}</span><span class="faint">F2</span></button>
          <button type="button" role="menuitem" class="bmenu-item" onClick={() => run(() => { void buildActions.trash(cur.id); })}>
            <span>{`Move “${cur.name}” to the Trash`}</span>
          </button>
        </>
      )}
      <div class="bmenu-sep" />
      <div class="bmenu-head label">Trash</div>
      {trash.length === 0 && <div class="bmenu-empty faint">The Trash is empty.</div>}
      {trash.map((b) => (
        <button key={b.id} type="button" role="menuitem" class="bmenu-item" onClick={() => run(() => { void buildActions.restore(b.id); })}>
          <span class="dim">{b.name}</span><span>Restore</span>
        </button>
      ))}
      {blocked && blocked.run_ids.length > 0 && (
        <div class="bmenu-blocked text" data-trash-blocked={blocked.reason}>
          <span>{blocked.reason === "pending_approval" ? "Waiting for your approval:" : "Still working:"}</span>
          {blocked.run_ids.map((id) => {
            const r = runs.value.get(id);
            return (
              <button key={id} type="button" role="menuitem" class="bmenu-item" data-blocking-run={id}
                onClick={() => run(() => { actions.selectRun(id); setConsoleCollapsed(false); })}>
                <span>{r ? runLabel(r, [...runs.value.values()]) : `run ${id}`}</span><span class="faint">show</span>
              </button>
            );
          })}
        </div>
      )}
      {trash.length > 0 && (confirmEmpty
        ? (
          <div class="bmenu-confirm" role="group" aria-label="Confirm emptying the Trash">
            <span>{`Delete ${trash.length} build${trash.length === 1 ? "" : "s"} for good? Their part files stay on disk.`}</span>
            <span class="bmenu-actions">
              <button type="button" class="btn btn-danger" onClick={() => run(() => { void buildActions.emptyTrash(); })}>Empty trash</button>
              <button type="button" class="btn" ref={(el) => { if (el && !confirmFocused.current) { confirmFocused.current = true; el.focus(); } }}
                onClick={() => { confirmFocused.current = false; setConfirmEmpty(false); }}>Keep</button>
            </span>
          </div>
        )
        : <button type="button" role="menuitem" class="bmenu-item" onClick={() => setConfirmEmpty(true)}><span>Empty trash…</span></button>)}
    </div>
  );
}
