// Home — the launcher shown on startup (R1), and the only way between builds (D1). It fills the
// whole window: there is no top bar on Home (D23), so its own header keeps clear of the macOS
// traffic lights and is the window's drag region.
//
// The grid: one tile per build, its cover (D24: a render of the build's assembly, 16:10,
// full-bleed) over its name, mass (when known) and when it was last edited, with a lead-agent
// status dot; every grid slot is a build. The header holds ← / → (history, Plan 10 E11), search,
// sort, Agents (a toggle, E1), one Settings menu (E2: the parts library, Onshape, the Trash and
// Profile & settings — each view's item closes it again, E7), "Import drawing…" and the primary
// New build; a drawing dropped anywhere on Home starts a part. A tile's ⋯ button and its context
// menu move it to the Trash, with a 5 s Undo (D30); the Trash shows Restore and "Delete forever"
// per tile, and Empty trash. Opening a tile opens that build.
import type { JSX } from "preact";
import { type MutableRef, useEffect, useRef, useState } from "preact/hooks";
import { lastEditedAt, type Build } from "../../../api/builds";
import { buildActions, builds, buildsError } from "../../../state/builds";
import { atHome, goHome, homeView, openBuild, toggleHomeView } from "../../../state/home";
import { actions, conn, runs } from "../../../state/store";
import { CubeMark } from "../../boot/CubeMark";
import { Led } from "../../common/Led";
import { Thumb } from "../../common/Thumb";
import { useDismiss } from "../../common/dismiss";
import { DrawingDrop, ImportCards, ImportDrawingButton } from "../../imports";
import { agentManagerOpen, closeAgentManager, openAgentManager } from "../../agents/manager";
import { NavButtons } from "../../common/NavButtons";
import { OverflowMenu } from "../../common/OverflowMenu";
import { useToggleOpener } from "../../common/toggle";
import { openProfile } from "../../profile/open";
import { DirectoryPage } from "../directory";
import { OnshapePage } from "../onshape";
import { buildStatus, DEFAULT_VIEW, fmtLastEdited, fmtMass, partsLabel, SORTS, type HomeSort, viewBuilds } from "./model";
import "./home.css";

/** How long a "Moved to the Trash" toast offers Undo. */
export const UNDO_MS = 5000;

export function HomePage(): JSX.Element {
  const view = homeView.value;
  if (view === "onshape") return <HomeSubView title="Onshape"><OnshapePage /></HomeSubView>;
  if (view === "library") return <HomeSubView title="Parts library"><DirectoryPage /></HomeSubView>;
  return <BuildGrid />;
}

/** A Home sub-view (D22): not part of any build, so it lives here with a way back to the grid. */
function HomeSubView({ title, children }: { title: string; children: JSX.Element }): JSX.Element {
  return (
    <section class="home home-sub" data-testid="home" data-home-view={homeView.value} aria-label={title}>
      <header class="home-bar" data-tauri-drag-region>
        <NavButtons />
        <BackToBuilds />
        <h1 class="home-title" data-tauri-drag-region>{title}</h1>
        <div class="home-tools"><SettingsMenu /></div>
      </header>
      <div class="home-sub-body">{children}</div>
    </section>
  );
}

function BackToBuilds(): JSX.Element {
  return (
    <button type="button" class="home-back" onClick={() => goHome()}>
      <span aria-hidden="true">←</span> Builds
    </button>
  );
}

function GearIcon(): JSX.Element {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <circle cx="8" cy="8" r="2.2" fill="none" stroke="currentColor" stroke-width="1.3" />
      <path d="M13.3 6.95 14.94 7.11v1.78l-1.64.16-.81 1.95 1.05 1.28-1.26 1.26-1.28-1.05-1.95.81-.16 1.64H7.11l-.16-1.64-1.95-.81-1.28 1.05-1.26-1.26 1.05-1.28-.81-1.95-1.64-.16V7.11l1.64-.16.81-1.95-1.05-1.28 1.26-1.26 1.28 1.05 1.95-.81.16-1.64h1.78l.16 1.64 1.95.81 1.28-1.05 1.26 1.26-1.05 1.28Z"
        fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" />
    </svg>
  );
}

/** E2: one Settings button for what isn't a build — the parts library, Onshape, the Trash — and
 *  Profile & settings. A view's item is checked while it shows; choosing it again closes it. */
function SettingsMenu(): JSX.Element {
  const view = homeView.value;
  const trashCount = builds.value.filter((b) => b.trashed_at !== null).length;
  return (
    <OverflowMenu label="Settings" title="Parts library, Onshape, Trash and settings" class="ovm-float home-settings"
      btnClass="home-tool home-settings-btn" trigger={<><GearIcon /><span>Settings</span></>}
      items={[
        { label: "Parts library", checked: view === "library", onSelect: () => toggleHomeView("library") },
        { label: "Onshape", checked: view === "onshape", onSelect: () => toggleHomeView("onshape") },
        { label: "Trash", checked: view === "trash", note: trashCount ? String(trashCount) : undefined, onSelect: () => toggleHomeView("trash") },
        { label: "Profile & settings", divider: true, onSelect: () => openProfile() },
      ]} />
  );
}

/** E1: Agents opens the agent manager, and clicking it again closes it (the header sits above
 *  the manager's scrim). */
function AgentsButton({ working }: { working: number }): JSX.Element {
  const opener = useToggleOpener(agentManagerOpen.value, () => openAgentManager(), closeAgentManager);
  return (
    // I7: the one easy view of every build's agents, reachable from Home (also ⇧⌘A)
    <button type="button" class={agentManagerOpen.value ? "home-tool home-agents on" : "home-tool home-agents"}
      title="Every build's lead and sub-agents (⇧⌘A)" {...opener}>
      {working > 0 && <span class="home-working-dot" aria-hidden="true" />}
      {`Agents${working ? ` · ${working} working` : ""}`}
    </button>
  );
}

interface Undo { id: string; name: string }

function BuildGrid(): JSX.Element {
  const [query, setQuery] = useState(DEFAULT_VIEW.query);
  const [sort, setSort] = useState<HomeSort>(DEFAULT_VIEW.sort);
  const trash = homeView.value === "trash";
  const [creating, setCreating] = useState(false);
  const [confirmEmpty, setConfirmEmpty] = useState(false);
  useEffect(() => { setConfirmEmpty(false); }, [trash]);
  const [undo, setUndo] = useState<Undo | null>(null);
  const undoTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // m1: the tiles (part count, last edited) are refetched every time Home shows, so returning with
  // ⌘0 never shows a stale count. The first launch is covered by the connect-time refresh.
  useEffect(() => { if (conn.peek() === "open") void buildActions.reloadList(); }, []);
  useEffect(() => () => clearTimeout(undoTimer.current), []);

  const shown = viewBuilds(builds.value, { query, sort, trash });
  const trashCount = builds.value.filter((b) => b.trashed_at !== null).length;
  const working = [...runs.value.values()].filter((r) => !r.archived && r.status === "running").length;

  const moveToTrash = async (b: Build) => {
    await buildActions.trash(b.id);
    if (builds.peek().some((x) => x.id === b.id && x.trashed_at !== null)) {
      setUndo({ id: b.id, name: b.name });
      clearTimeout(undoTimer.current);
      undoTimer.current = setTimeout(() => setUndo(null), UNDO_MS);
    }
  };
  const undoTrash = () => {
    if (!undo) return;
    clearTimeout(undoTimer.current);
    setUndo(null);
    void buildActions.restore(undo.id, { open: false });
  };

  return (
    <section class="home" data-testid="home" data-home-view="builds" aria-label="Builds">
      <header class="home-bar" data-tauri-drag-region>
        <NavButtons />
        {trash && <BackToBuilds />}
        <h1 class="home-title" data-tauri-drag-region>{trash ? "Trash" : "Builds"}</h1>
        <div class="home-tools">
          <input class="home-search" type="search" placeholder="Search builds" aria-label="Search builds"
            value={query} onInput={(e) => setQuery((e.target as HTMLInputElement).value)} />
          <label class="home-sort">
            <span class="label">Sort</span>
            <select aria-label="Sort builds" value={sort} onChange={(e) => setSort((e.target as HTMLSelectElement).value as HomeSort)}>
              {SORTS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
            </select>
          </label>
          <span class="home-sep" aria-hidden="true" />
          <AgentsButton working={working} />
          <SettingsMenu />
          <span class="home-sep" aria-hidden="true" />
          {/* U5 / review m1: a drawing becomes a part — this button, or drop one anywhere on Home */}
          <ImportDrawingButton />
          {creating
            ? <NewBuildField onDone={(name) => { setCreating(false); if (name) void create(name); }} />
            : <button type="button" class="btn btn-primary home-new" onClick={() => setCreating(true)}>New build</button>}
        </div>
      </header>
      {buildsError.value && (
        <p class="home-error" role="alert">
          <span class="text">{buildsError.value}</span>
          <button type="button" class="home-error-x" aria-label="Dismiss" onClick={() => { buildsError.value = null; }}>×</button>
        </p>
      )}

      {trash && trashCount > 0 && (
        <div class="home-trashbar">
          <span class="dim">Builds in the Trash keep their files until you delete them for good.</span>
          {confirmEmpty
            ? (
              <span class="home-confirm" role="group" aria-label="Confirm emptying the Trash">
                <span>{`Delete ${trashCount} build${trashCount === 1 ? "" : "s"} for good?`}</span>
                <button type="button" class="btn btn-danger" onClick={() => { setConfirmEmpty(false); void buildActions.emptyTrash(); }}>Empty trash</button>
                <button type="button" class="btn" ref={(el) => el?.focus()} onClick={() => setConfirmEmpty(false)}>Keep</button>
              </span>
            )
            : <button type="button" class="btn" onClick={() => setConfirmEmpty(true)}>Empty trash…</button>}
        </div>
      )}

      {/* m2: import results get the page's full width, not a grid cell */}
      {!trash && <div class="home-imports"><ImportCards /></div>}
      {/* every grid slot is a build (review m1); a drawing dropped anywhere on Home starts a part */}
      {/* FW-layout (D20): the grid fills the window (3–5 columns of 280–420px); one or two builds sit
          as a centred pair of large covers with a quiet hint under them, none as a centred start */}
      <ul class="home-grid" role="list" aria-label={trash ? "Trashed builds" : "Builds"}
        data-few={shown.length <= 2 ? String(shown.length) : undefined}>
        {shown.map((b) => (
          <li key={b.id} class="home-cell">
            {trash ? <TrashTile build={b} /> : <BuildTile build={b} onTrash={() => { void moveToTrash(b); }} />}
          </li>
        ))}

        {shown.length === 0 && (
          <li class="home-empty">
            {!trash && !query && <CubeMark />}
            <span class="home-empty-title">{trash ? "The Trash is empty." : query ? `No builds match “${query}”.` : "No builds yet"}</span>
            {!trash && !query && <span class="dim">Start one with New build, or drop a drawing anywhere on this window.</span>}
          </li>
        )}
      </ul>
      {!trash && !query && shown.length > 0 && shown.length <= 2 && (
        <p class="home-hint dim">Start another from the button above; a drawing dropped anywhere here becomes a part.</p>
      )}

      {undo && (
        <div class="home-toast" role="status" aria-live="polite">
          <span class="text">{`Moved “${undo.name}” to the Trash`}</span>
          <button type="button" class="home-toast-undo" onClick={undoTrash}>Undo</button>
        </div>
      )}
      {!trash && <DrawingDrop variant="home" />}
    </section>
  );

  async function create(name: string): Promise<void> {
    const b = await buildActions.create(name);
    if (b) atHome.value = false;        // created builds open straight into the build
  }
}

function BuildTile({ build: b, onTrash }: { build: Build; onTrash(): void }): JSX.Element {
  const status = buildStatus(b.id, runs.value.values());
  const edited = lastEditedAt(b);
  const mass = fmtMass(b.mass_g);
  const [menuAt, setMenuAt] = useState<{ x: number; y: number } | null>(null);
  const more = useRef<HTMLButtonElement>(null);
  return (
    <div class="tile" data-build={b.id}
      onContextMenu={(e) => { e.preventDefault(); setMenuAt({ x: e.clientX, y: e.clientY }); }}>
      <button type="button" class="tile-open" title={`Open ${b.name}`} aria-label={`Open ${b.name}`}
        onClick={() => { void openBuild(b.id); }}>
        <div class="tile-thumb">
          <Thumb class="tile-img" alt={`${b.name} cover`} glyph={b.part_count > 0 ? "" : "No parts yet"} cacheKey={`${b.id}:${edited}:${b.part_count}`}
            load={b.part_count > 0 ? () => actions.client().projectThumb(b.id) : null} />
        </div>
        <div class="tile-body">
          <span class="tile-name">{b.name}</span>
          <span class="tile-meta">
            <Led tone={status.tone} title={status.label} />
            {mass ? <span class="num">{mass}</span> : <span>{partsLabel(b.part_count)}</span>}
            <span class="tile-dot" aria-hidden="true">·</span>
            <span>{fmtLastEdited(edited)}</span>
          </span>
        </div>
      </button>
      <button type="button" ref={more} class="tile-more" aria-label={`More for ${b.name}`} aria-haspopup="menu"
        aria-expanded={menuAt !== null}
        onClick={(e) => {
          const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
          setMenuAt(menuAt ? null : { x: r.right - 180, y: r.bottom + 4 });
        }}>⋯</button>
      {menuAt && (
        <TileMenu at={menuAt} name={b.name} trigger={more} onClose={() => setMenuAt(null)}
          onOpen={() => { void openBuild(b.id); }} onTrash={onTrash} />
      )}
    </div>
  );
}

function TileMenu({ at, name, trigger, onClose, onOpen, onTrash }: {
  at: { x: number; y: number }; name: string; trigger: MutableRef<HTMLButtonElement | null>;
  onClose(): void; onOpen(): void; onTrash(): void;
}): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  useDismiss(ref, onClose, [trigger]);
  const run = (f: () => void) => { onClose(); f(); };
  return (
    <div ref={(el) => { ref.current = el; if (el && !el.contains(document.activeElement)) el.querySelector<HTMLButtonElement>("button")?.focus(); }}
      class="tile-menu surface" role="menu" aria-label={`${name} actions`}
      style={{ left: `${Math.max(8, at.x)}px`, top: `${at.y}px` }}
      onKeyDown={(e) => {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); return; }
        if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
        e.preventDefault();
        const f = [...(ref.current?.querySelectorAll<HTMLButtonElement>("button") ?? [])];
        const i = f.indexOf(document.activeElement as HTMLButtonElement);
        f[(i + (e.key === "ArrowDown" ? 1 : -1) + f.length) % f.length]?.focus();
      }}>
      <button type="button" role="menuitem" class="tile-menu-item" onClick={() => run(onOpen)}>Open</button>
      <button type="button" role="menuitem" class="tile-menu-item tile-menu-danger" onClick={() => run(onTrash)}>Move to Trash</button>
    </div>
  );
}

function TrashTile({ build: b }: { build: Build }): JSX.Element {
  const [confirm, setConfirm] = useState(false);
  return (
    <div class="tile tile-trashed" data-build={b.id}>
      <div class="tile-thumb">
        <Thumb class="tile-img" alt={`${b.name} cover`} glyph={b.part_count > 0 ? "" : "No parts"} cacheKey={`${b.id}:${lastEditedAt(b)}`}
          load={b.part_count > 0 ? () => actions.client().projectThumb(b.id) : null} />
      </div>
      <div class="tile-body">
        <span class="tile-name">{b.name}</span>
        {confirm
          ? (
            <span class="tile-confirm" role="group" aria-label={`Confirm deleting ${b.name}`}>
              <span class="dim">Delete for good?</span>
              <button type="button" class="tile-link tile-link-danger" onClick={() => { setConfirm(false); void buildActions.purge(b.id); }}>Delete</button>
              <button type="button" class="tile-link" ref={(el) => el?.focus()} onClick={() => setConfirm(false)}>Keep</button>
            </span>
          )
          : (
            <span class="tile-meta">
              <button type="button" class="tile-link" onClick={() => { void buildActions.restore(b.id, { open: false }); }}>Restore</button>
              <button type="button" class="tile-link tile-link-danger" onClick={() => setConfirm(true)}>Delete forever</button>
            </span>
          )}
      </div>
    </div>
  );
}

/** The header's New build button turned into a name field: Enter creates, Esc / empty cancels. */
function NewBuildField({ onDone }: { onDone(name: string | null): void }): JSX.Element {
  const [v, setV] = useState("");
  const done = useRef(false);
  const finish = (name: string | null) => { if (done.current) return; done.current = true; onDone(name); };
  return (
    <span class="home-new-field">
      <input class="home-new-input" aria-label="New build name" placeholder="Build name" maxLength={80} value={v}
        ref={(el) => { if (el && !done.current && document.activeElement !== el) el.focus(); }}
        onInput={(e) => setV((e.target as HTMLInputElement).value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); finish(v.trim() || null); }
          if (e.key === "Escape") { e.preventDefault(); finish(null); }
        }}
        onBlur={() => finish(v.trim() || null)} />
    </span>
  );
}
