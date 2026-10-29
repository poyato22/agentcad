// "◇ LOCAL / [build tabs] / part". Each open build is a tab with its own parts, runs and
// assembly: click (or ←/→ then Enter) switches, × closes, double-click or F2 renames, + makes a
// new build. The ▾ menu opens any other build, moves the active one to the Trash, and lists the
// Trash (Restore, Empty trash — the only step that deletes, and it asks first).
import type { JSX } from "preact";
import { useRef, useState } from "preact/hooks";
import type { Build } from "../../api/builds";
import { partName } from "../../lib/names";
import {
  activeBuild, activeBuildId, buildActions, buildsError, liveBuilds, openBuildIds, openBuilds, trashedBuilds,
} from "../../state/builds";
import { activePart } from "../../state/selection";
import { selectedPartId } from "../../state/store";
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

export function BuildTabs(): JSX.Element {
  const tabs = openBuilds.value;
  const active = activeBuildId.value;
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

  function onKeyDown(e: KeyboardEvent, i: number, b: Build): void {
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      focusTab((i + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length);
    } else if (e.key === "Home") { e.preventDefault(); focusTab(0); }
    else if (e.key === "End") { e.preventDefault(); focusTab(tabs.length - 1); }
    else if (e.key === "F2") { e.preventDefault(); setRenaming(b.id); }
  }

  return (
    <span class="btabs" data-testid="build-tabs">
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
              {tabs.length > 1 && (
                <button type="button" class="btab-close" aria-label={`Close ${b.name}`} title="Close this tab (the build stays; reopen it from ▾)"
                  onClick={() => { void buildActions.close(b.id); }}>×</button>
              )}
            </span>
          );
        })}
      </div>
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
  const ref = useRef<HTMLDivElement>(null);
  const run = (f: () => void) => { onClose(); f(); };
  return (
    <div class="bmenu" role="menu" aria-label="Builds" style={{ left: `${at.left}px`, top: `${at.top}px` }} ref={(el) => { ref.current = el; if (el && !el.contains(document.activeElement)) el.querySelector<HTMLButtonElement>("button")?.focus(); }}
      onKeyDown={(e) => {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); return; }
        if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
        e.preventDefault();
        const f = [...(ref.current?.querySelectorAll<HTMLButtonElement>("button:not([disabled])") ?? [])];
        const i = f.indexOf(document.activeElement as HTMLButtonElement);
        f[(i + (e.key === "ArrowDown" ? 1 : -1) + f.length) % f.length]?.focus();
      }}>
      <div class="bmenu-head label">Builds</div>
      {live.map((b) => (
        <button key={b.id} type="button" role="menuitemradio" aria-checked={b.id === cur?.id} class="bmenu-item"
          onClick={() => run(() => { void buildActions.switchTo(b.id); })}>
          <span>{b.name}</span>
          <span class="faint">{b.id === cur?.id ? "active" : open.has(b.id) ? "open" : `${b.part_count} part${b.part_count === 1 ? "" : "s"}`}</span>
        </button>
      ))}
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
      {trash.length > 0 && (confirmEmpty
        ? (
          <div class="bmenu-confirm" role="group" aria-label="Confirm emptying the Trash">
            <span>{`Delete ${trash.length} build${trash.length === 1 ? "" : "s"} for good? Their part files stay on disk.`}</span>
            <span class="bmenu-actions">
              <button type="button" class="btn btn-danger" onClick={() => run(() => { void buildActions.emptyTrash(); })}>Empty trash</button>
              <button type="button" class="btn" onClick={() => setConfirmEmpty(false)}>Keep</button>
            </span>
          </div>
        )
        : <button type="button" role="menuitem" class="bmenu-item" onClick={() => setConfirmEmpty(true)}><span>Empty trash…</span></button>)}
    </div>
  );
}
