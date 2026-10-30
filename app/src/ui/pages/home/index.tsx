// Home — the launcher shown on startup (R1). An Onshape-Documents-style grid of build tiles:
// a composite thumbnail, the build's name, its part count and when it was last edited, and a
// lead-agent status dot. Search and sort sit in the toolbar; a New-build tile starts a build;
// the Trash filter shows trashed builds with Restore. Opening a tile opens that build as a tab.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { lastEditedAt, type Build } from "../../../api/builds";
import { buildActions, builds, buildsError } from "../../../state/builds";
import { atHome, openBuild } from "../../../state/home";
import { actions, conn, runs } from "../../../state/store";
import { Led } from "../../common/Led";
import { Thumb } from "../../common/Thumb";
import { DrawingDrop, ImportCards } from "../../imports";
import { openAgentManager } from "../../agents/manager";
import { buildStatus, DEFAULT_VIEW, fmtLastEdited, partsLabel, SORTS, type HomeSort, viewBuilds } from "./model";
import "./home.css";

export function HomePage(): JSX.Element {
  const [query, setQuery] = useState(DEFAULT_VIEW.query);
  const [sort, setSort] = useState<HomeSort>(DEFAULT_VIEW.sort);
  const [trash, setTrash] = useState(DEFAULT_VIEW.trash);
  const [creating, setCreating] = useState(false);
  // m1: the tiles (part count, last edited) are refetched every time Home shows, so returning with
  // ⌘0 never shows a stale count. The first launch is covered by the connect-time refresh.
  useEffect(() => { if (conn.peek() === "open") void buildActions.reloadList(); }, []);

  const shown = viewBuilds(builds.value, { query, sort, trash });
  const trashCount = builds.value.filter((b) => b.trashed_at !== null).length;
  const working = [...runs.value.values()].filter((r) => !r.archived && r.status === "running").length;

  return (
    <section class="home" data-testid="home" aria-label="Builds">
      <header class="home-bar">
        <h1 class="home-title">Builds</h1>
        <div class="home-tools">
          <input class="home-search" type="search" placeholder="Search builds" aria-label="Search builds"
            value={query} onInput={(e) => setQuery((e.target as HTMLInputElement).value)} />
          <label class="home-sort">
            <span class="label">Sort</span>
            <select aria-label="Sort builds" value={sort} onChange={(e) => setSort((e.target as HTMLSelectElement).value as HomeSort)}>
              {SORTS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
            </select>
          </label>
          {/* I7: the one easy view of every build's agents, reachable from Home (also ⇧⌘A) */}
          <button type="button" class="home-trash home-agents" title="Every build's lead and sub-agents (⇧⌘A)"
            onClick={() => openAgentManager()}>{`Agents${working ? ` · ${working} working` : ""}`}</button>
          <button type="button" class={trash ? "home-trash on" : "home-trash"} aria-pressed={trash}
            title="Show trashed builds" onClick={() => setTrash((t) => !t)}>
            {`Trash${trashCount ? ` · ${trashCount}` : ""}`}
          </button>
        </div>
      </header>
      {buildsError.value && (
        <p class="home-error" role="alert">
          {buildsError.value}
          <button type="button" class="home-error-x" aria-label="Dismiss" onClick={() => { buildsError.value = null; }}>×</button>
        </p>
      )}

      {/* m2: import results get the page's full width, not a 200 px grid cell */}
      {!trash && <div class="home-imports"><ImportCards /></div>}
      <ul class="home-grid" role="list" aria-label={trash ? "Trashed builds" : "Builds"}>
        {!trash && (
          <li class="home-cell">
            {creating
              ? <NewTile onDone={(name) => { setCreating(false); if (name) void create(name); }} />
              : <button type="button" class="tile tile-new" onClick={() => setCreating(true)}>
                  <span class="tile-plus" aria-hidden="true">+</span>
                  <span class="tile-new-label">New build</span>
                </button>}
          </li>
        )}
        {/* U5: drop a technical drawing to start a part (the core picks or makes its build) */}
        {!trash && <li class="home-cell home-cell-drop"><DrawingDrop variant="tile" cards={false} /></li>}

        {shown.map((b) => (
          <li key={b.id} class="home-cell">
            {trash ? <TrashTile build={b} /> : <BuildTile build={b} />}
          </li>
        ))}

        {shown.length === 0 && (
          <li class="home-empty dim">
            {trash ? "The Trash is empty." : query ? `No builds match “${query}”.` : "No builds yet. Start one with New build."}
          </li>
        )}
      </ul>
    </section>
  );

  async function create(name: string): Promise<void> {
    const b = await buildActions.create(name);
    if (b) atHome.value = false;        // created builds open straight into their tab
  }
}

function BuildTile({ build: b }: { build: Build }): JSX.Element {
  const status = buildStatus(b.id, runs.value.values());
  const edited = lastEditedAt(b);
  return (
    <button type="button" class="tile" data-build={b.id}
      title={`Open ${b.name}`} onClick={() => { void openBuild(b.id); }}>
      <div class="tile-thumb">
        <Thumb class="tile-img" alt={`${b.name} preview`} cacheKey={`${b.id}:${edited}:${b.part_count}`}
          load={b.part_count > 0 ? () => actions.client().projectThumb(b.id) : null} />
      </div>
      <div class="tile-body">
        <span class="tile-name">{b.name}</span>
        <span class="tile-meta dim">
          <Led tone={status.tone} title={status.label} />
          {`${partsLabel(b.part_count)} · ${fmtLastEdited(edited)}`}
        </span>
      </div>
    </button>
  );
}

function TrashTile({ build: b }: { build: Build }): JSX.Element {
  return (
    <div class="tile tile-trashed" data-build={b.id}>
      <div class="tile-thumb">
        <Thumb class="tile-img" alt={`${b.name} preview`} cacheKey={`${b.id}:${lastEditedAt(b)}`}
          load={() => actions.client().projectThumb(b.id)} />
      </div>
      <div class="tile-body">
        <span class="tile-name">{b.name}</span>
        <span class="tile-meta">
          <button type="button" class="tile-restore" onClick={() => { void buildActions.restore(b.id); }}>Restore</button>
        </span>
      </div>
    </div>
  );
}

function NewTile({ onDone }: { onDone(name: string | null): void }): JSX.Element {
  const [v, setV] = useState("");
  const done = useRef(false);
  const finish = (name: string | null) => { if (done.current) return; done.current = true; onDone(name); };
  return (
    <div class="tile tile-new">
      <span class="tile-plus" aria-hidden="true">+</span>
      <input class="tile-new-input" aria-label="New build name" placeholder="Build name" maxLength={80} value={v}
        ref={(el) => { if (el && !done.current && document.activeElement !== el) el.focus(); }}
        onInput={(e) => setV((e.target as HTMLInputElement).value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); finish(v.trim() || null); }
          if (e.key === "Escape") { e.preventDefault(); finish(null); }
        }}
        onBlur={() => finish(v.trim() || null)} />
    </div>
  );
}
