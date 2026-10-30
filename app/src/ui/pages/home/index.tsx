// Home — the launcher shown on startup (R1). An Onshape-Documents-style grid of build tiles:
// a composite thumbnail, the build's name, its part count and when it was last edited, and a
// lead-agent status dot. Search and sort sit in the toolbar; a New-build tile starts a build;
// the Trash filter shows trashed builds with Restore. Opening a tile opens that build as a tab.
import type { JSX } from "preact";
import { useRef, useState } from "preact/hooks";
import type { Build } from "../../../api/builds";
import { builds } from "../../../state/builds";
import { buildActions } from "../../../state/builds";
import { atHome, openBuild } from "../../../state/home";
import { actions, runs } from "../../../state/store";
import { Led } from "../../common/Led";
import { Thumb } from "../../common/Thumb";
import { buildStatus, DEFAULT_VIEW, fmtLastEdited, partsLabel, SORTS, type HomeSort, viewBuilds } from "./model";
import "./home.css";

export function HomePage(): JSX.Element {
  const [query, setQuery] = useState(DEFAULT_VIEW.query);
  const [sort, setSort] = useState<HomeSort>(DEFAULT_VIEW.sort);
  const [trash, setTrash] = useState(DEFAULT_VIEW.trash);
  const [creating, setCreating] = useState(false);

  const shown = viewBuilds(builds.value, { query, sort, trash });
  const trashCount = builds.value.filter((b) => b.trashed_at !== null).length;

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
          <button type="button" class={trash ? "home-trash on" : "home-trash"} aria-pressed={trash}
            title="Show trashed builds" onClick={() => setTrash((t) => !t)}>
            {`Trash${trashCount ? ` · ${trashCount}` : ""}`}
          </button>
        </div>
      </header>

      <div class="home-grid" role="list">
        {!trash && (creating
          ? <NewTile onDone={(name) => { setCreating(false); if (name) void create(name); }} />
          : <button type="button" class="tile tile-new" role="listitem" onClick={() => setCreating(true)}>
              <span class="tile-plus" aria-hidden="true">+</span>
              <span class="tile-new-label">New build</span>
            </button>)}

        {shown.map((b) => trash
          ? <TrashTile key={b.id} build={b} />
          : <BuildTile key={b.id} build={b} />)}

        {shown.length === 0 && (
          <p class="home-empty dim">
            {trash ? "The Trash is empty." : query ? `No builds match “${query}”.` : "No builds yet. Start one with New build."}
          </p>
        )}
      </div>
    </section>
  );

  async function create(name: string): Promise<void> {
    const b = await buildActions.create(name);
    if (b) atHome.value = false;        // created builds open straight into their tab
  }
}

function BuildTile({ build: b }: { build: Build }): JSX.Element {
  const status = buildStatus(b.id, runs.value.values());
  return (
    <button type="button" class="tile" role="listitem" data-build={b.id}
      title={`Open ${b.name}`} onClick={() => { void openBuild(b.id); }}>
      <div class="tile-thumb">
        <Thumb class="tile-img" alt={`${b.name} preview`} cacheKey={`${b.id}:${b.updated_at}:${b.part_count}`}
          load={() => actions.client().projectThumb(b.id)} />
      </div>
      <div class="tile-body">
        <span class="tile-name">{b.name}</span>
        <span class="tile-meta dim">
          <Led tone={status.tone} title={status.label} />
          {`${partsLabel(b.part_count)} · ${fmtLastEdited(b.updated_at)}`}
        </span>
      </div>
    </button>
  );
}

function TrashTile({ build: b }: { build: Build }): JSX.Element {
  return (
    <div class="tile tile-trashed" role="listitem" data-build={b.id}>
      <div class="tile-thumb">
        <Thumb class="tile-img" alt={`${b.name} preview`} cacheKey={`${b.id}:${b.updated_at}`}
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
    <div class="tile tile-new" role="listitem">
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
