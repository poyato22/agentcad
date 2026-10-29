// Parts directory page (Plan 5 A6): built-in parts (ISO fasteners, bearings, drone motors and
// props, packs, stacks, servos, steppers, extrusions, boards) plus items imported from a product
// page. Category sidebar · search · item cards with key specs and the mount pattern · a detail
// panel with "Insert into build", "Use with agent" and "Open online".
//
// Import from link: the core fetches the page (https only, size and time capped, redirects
// re-checked) and SAVES the extracted item at once (C5 ruling). The review panel then saves the
// user's edits with PATCH, and Discard deletes the item it just added. A link already in the
// directory comes back unchanged; only the "Read the page again" button asks the core to refresh.
//
// Everything an imported item carries (name, vendor, specs, page text) came from a web page: it is
// rendered as plain text only (Preact text nodes, never HTML), and the page text is shown in a
// <pre>. Masses are marked "estimate" whenever the directory has no stated mass.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { CatalogItem, PartResult } from "../../../api/directory";
import { addCatalogChip } from "../../../state/agentChips";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { composerOpen } from "../../console/view";
import { describeError } from "../../profile/common";
import { PageShell } from "../PageShell";
import { setConsoleCollapsed } from "../router";
import { AllowedSites } from "./AllowedSites";
import { BuildPicker, useBuilds } from "./BuildPicker";
import { ImportPanel } from "./ImportPanel";
import { categoryLabel, dimLabel, fmtSpecValue, httpsLink, humanKey, keySpecs, massInfo, onlineLink } from "./model";
import { MountDiagram } from "./MountDiagram";
import "./directory.css";

export const SEARCH_DEBOUNCE_MS = 180;
const MASS_KEY = /^(mass|weight)$/i;

type Panel = { kind: "item"; id: string } | { kind: "import" } | null;

export function DirectoryPage(): JSX.Element {
  const [categories, setCategories] = useState<string[] | null>(null);
  const [category, setCategory] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<CatalogItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [panel, setPanel] = useState<Panel>(null);
  const [rev, setRev] = useState(0);

  useEffect(() => {
    let live = true;
    actions.client().catalogCategories().then((c) => { if (live) setCategories(c); }, () => { if (live) setCategories([]); });
    return () => { live = false; };
  }, []);

  useEffect(() => {
    const t = setTimeout(() => setQuery(q.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    let live = true;
    setError(null);
    actions.client().catalogSearch({ q: query, category, limit: 200 }).then(
      (list) => { if (live) setItems(list); },
      (e: unknown) => { if (live) { setItems([]); setError(describeError(e)); } },
    );
    return () => { live = false; };
  }, [query, category, rev]);

  const changed = () => setRev((r) => r + 1);

  return (
    <PageShell title="Parts directory">
      <div class="dir" data-dir>
        <nav class="dir-side" aria-label="Categories">
          <ul class="dir-cats">
            {[null, ...(categories ?? [])].map((c) => (
              <li key={c ?? "all"}>
                <button type="button" class={c === category ? "dir-cat dir-cat-on" : "dir-cat"} aria-pressed={c === category}
                  onClick={() => setCategory(c)}>{c === null ? "All parts" : categoryLabel(c)}</button>
              </li>
            ))}
          </ul>
          <AllowedSites />
        </nav>

        <section class="dir-main" aria-label="Parts">
          <div class="dir-toolbar">
            <input class="dir-search" type="search" aria-label="Search parts" placeholder="Search: 2207, M3, 608, 30.5…" value={q}
              onInput={(e) => setQ(e.currentTarget.value)}
              onKeyDown={(e) => { if (e.key === "Enter") setQuery(q.trim()); }} />
            <Button onClick={() => setPanel({ kind: "import" })} aria-expanded={panel?.kind === "import"}>Import from link</Button>
          </div>
          {error && <p class="dir-err" role="alert">{error}</p>}
          {items === null && !error && <p class="faint dir-note">Loading parts…</p>}
          {items !== null && items.length === 0 && !error && (
            <p class="dir-note dim">{query ? `No part matches “${query}”.` : "Nothing in this category yet."} Import one from a product page with Import from link.</p>
          )}
          {items !== null && items.length > 0 && (
            <ul class="dir-grid" aria-label={`${items.length} parts`}>
              {items.map((it) => (
                <li key={it.id}>
                  <ItemCard item={it} selected={panel?.kind === "item" && panel.id === it.id} onOpen={() => setPanel({ kind: "item", id: it.id })} />
                </li>
              ))}
            </ul>
          )}
        </section>

        {panel && (
          <aside class="dir-panel" aria-label={panel.kind === "import" ? "Import from link" : "Part details"}>
            {panel.kind === "import"
              ? <ImportPanel onClose={() => setPanel(null)} onChanged={changed} onSaved={(id) => { changed(); setPanel({ kind: "item", id }); }} />
              : <ItemDetail key={panel.id} id={panel.id} onClose={() => setPanel(null)} onChanged={changed} />}
          </aside>
        )}
      </div>
    </PageShell>
  );
}

function SourceTag({ item }: { item: CatalogItem }): JSX.Element {
  if (item.source === "builtin") return <span class="dir-tag">Built-in</span>;
  const host = onlineLink(item)?.host ?? "link";
  return <span class="dir-tag dir-tag-url" title={`Imported from ${host}`}>{host}</span>;
}

function MassLine({ item }: { item: CatalogItem }): JSX.Element {
  const m = massInfo(item);
  return (
    <span class="dir-mass" data-mass-source={m.estimate ? "estimate" : "catalog"}>
      <span class="dir-mass-k">Mass</span> {m.text}{m.estimate && <span class="dir-estimate" title="Not a stated mass: computed from the shape's volume and a density">estimate</span>}
    </span>
  );
}

function ItemCard({ item, selected, onOpen }: { item: CatalogItem; selected: boolean; onOpen: () => void }): JSX.Element {
  return (
    <button type="button" class={selected ? "dir-card dir-card-on" : "dir-card"} aria-pressed={selected} data-item={item.id} onClick={onOpen}>
      <span class="dir-card-head">
        <span class="dir-card-name">{item.name}</span>
        <SourceTag item={item} />
      </span>
      <span class="dir-card-body">
        <span class="dir-card-specs">
          {keySpecs(item).map((s) => (
            <span key={s.key} class="dir-spec"><span class="dir-spec-k">{humanKey(s.key)}</span> {fmtSpecValue(s)}</span>
          ))}
          <MassLine item={item} />
        </span>
        <MountDiagram mount={item.mount} dims={item.dims} size={72} />
      </span>
    </button>
  );
}

function ItemDetail({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }): JSX.Element {
  const [item, setItem] = useState<CatalogItem | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [inserting, setInserting] = useState(false);
  const [inserted, setInserted] = useState<PartResult | null>(null);
  const [insertError, setInsertError] = useState<string | null>(null);
  const [chipNote, setChipNote] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const { builds, buildId } = useBuilds();
  const head = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    let live = true;
    actions.client().catalogItem(id).then((it) => { if (live) setItem(it); }, (e: unknown) => { if (live) setError(describeError(e)); });
    return () => { live = false; };
  }, [id]);
  useEffect(() => { if (item) head.current?.focus(); }, [item?.id]);

  if (error) return <div class="dir-detail"><p class="dir-err" role="alert">{error}</p><Button variant="ghost" onClick={onClose}>Close</Button></div>;
  if (!item) return <div class="dir-detail"><p class="faint">Loading…</p></div>;

  if (editing) {
    return <ImportPanel review={item} onClose={() => setEditing(false)} onChanged={onChanged}
      onSaved={() => { setEditing(false); onChanged(); void actions.client().catalogItem(id).then(setItem, () => {}); }} />;
  }

  const link = onlineLink(item);
  const mass = massInfo(item);
  // the mass spec (with its note, e.g. "typical") is folded into the Mass row
  const massNote = item.specs.find((s) => MASS_KEY.test(s.key))?.note ?? null;

  async function insert(): Promise<void> {
    if (!item) return;
    setInserting(true);
    setInsertError(null);
    setInserted(null);
    try {
      setInserted(await actions.client().catalogInsert(item.id, buildId));
    } catch (e) {
      setInsertError(describeError(e));
    } finally {
      setInserting(false);
    }
  }

  function useWithAgent(): void {
    if (!item) return;
    if (addCatalogChip(item.id, item.name)) {
      setConsoleCollapsed(false);
      composerOpen.value = true;
      setChipNote("Added to the new-run composer. The agent reads it with catalog_get.");
    } else {
      setChipNote("Couldn't add it: the composer holds at most 12 parts.");
    }
  }

  async function remove(): Promise<void> {
    if (!item) return;
    try {
      await actions.client().catalogDelete(item.id);
      onChanged();
      onClose();
    } catch (e) {
      setInsertError(describeError(e));
    }
  }

  const buildName = builds?.find((b) => b.id === buildId)?.name ?? "the build";
  return (
    <div class="dir-detail" data-detail={item.id}>
      <div class="dir-detail-head">
        <h2 ref={head} tabIndex={-1} class="dir-detail-name">{item.name}</h2>
        <Button variant="ghost" aria-label="Close details" onClick={onClose}>×</Button>
      </div>
      <p class="dir-detail-sub dim">
        {categoryLabel(item.category)}{item.vendor ? `, ${item.vendor}` : ""}. <SourceTag item={item} />
        {item.edited_by_user && <span class="dir-tag">edited</span>}
      </p>

      <div class="dir-actions">
        <BuildPicker builds={builds} buildId={buildId} label="Into build" />
        <Button variant="ink" disabled={inserting || !buildId} onClick={() => { void insert(); }}>{inserting ? "Inserting…" : "Insert into build"}</Button>
        <Button onClick={useWithAgent}>Use with agent</Button>
        {link && (
          // the host is in the label, so the domain is visible before the system browser opens it
          <a class="btn dir-online" href={link.href} target="_blank" rel="noopener noreferrer" title={link.href}>Open online ({link.host})</a>
        )}
      </div>
      {inserted && (
        <p class="dir-ok" role="status" data-inserted={inserted.part_id}>
          {inserted.existing ? `Already in ${buildName} as ${inserted.part_id}.` : `Inserted into ${buildName} as ${inserted.part_id}.`}
          {inserted.metrics?.mass_source === "estimate" && <> Mass {inserted.metrics.mass_g !== undefined ? `${inserted.metrics.mass_g.toFixed(1)} g` : ""} <span class="dir-estimate">estimate</span></>}
        </p>
      )}
      {insertError && <p class="dir-err" role="alert">{insertError}</p>}
      {chipNote && <p class="dir-note dim" role="status">{chipNote}</p>}

      {item.mount && (
        <figure class="dir-figure">
          <MountDiagram mount={item.mount} dims={item.dims} size={200} detailed />
          <figcaption class="dim">
            Mount {item.mount.pattern ?? ""}{item.mount.thread ? ` ${item.mount.thread}` : ""}{item.mount.hole_d ? `, holes ⌀${item.mount.hole_d} mm` : ""}
          </figcaption>
        </figure>
      )}

      <table class="dir-table" aria-label="Specs">
        <tbody>
          <tr data-mass-source={mass.estimate ? "estimate" : "catalog"}>
            <th scope="row">Mass</th>
            <td>
              {mass.text}{mass.estimate ? <span class="dir-estimate">estimate</span> : <span class="dir-faint-tag">catalog value</span>}
              {massNote && <span class="dir-spec-note">{massNote}</span>}
            </td>
          </tr>
          {item.specs.filter((s) => !MASS_KEY.test(s.key)).map((s, i) => (
            <tr key={`${s.key}-${i}`}>
              <th scope="row">{humanKey(s.key)}</th>
              <td>{fmtSpecValue(s)}{s.note && <span class="dir-spec-note">{s.note}</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {Object.keys(item.dims).length > 0 && (
        <table class="dir-table" aria-label="Dimensions">
          <tbody>
            {Object.entries(item.dims).map(([k, v]) => <tr key={k}><th scope="row">{dimLabel(k)}</th><td>{v}</td></tr>)}
          </tbody>
        </table>
      )}
      {item.sources.length > 0 && (
        <div class="dir-sources">
          <h3 class="dir-h3">Sources</h3>
          <ul>
            {item.sources.map((s, i) => {
              const l = httpsLink(s.url);
              return (
                <li key={i}>
                  {l ? <a href={l.href} target="_blank" rel="noopener noreferrer" title={l.href}>{s.title || l.host}</a> : s.title}
                  {l && <span class="faint"> ({l.host})</span>}
                  {s.note && <span class="dir-spec-note">{s.note}</span>}
                </li>
              );
            })}
          </ul>
        </div>
      )}
      {item.source === "url" && (
        <div class="dir-actions dir-actions-end">
          <Button onClick={() => setEditing(true)}>Edit</Button>
          {confirmDelete
            ? <><Button variant="danger" onClick={() => { void remove(); }}>Delete from the directory</Button><Button variant="ghost" onClick={() => setConfirmDelete(false)}>Keep it</Button></>
            : <Button variant="ghost" onClick={() => setConfirmDelete(true)}>Delete…</Button>}
        </div>
      )}
    </div>
  );
}
