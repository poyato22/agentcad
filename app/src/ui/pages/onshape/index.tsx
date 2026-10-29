// Onshape page (Plan 5 A6): browse your Onshape documents and mirror a Part Studio or Assembly into
// a build. Not connected: a card that opens Profile → Onshape. Connected: a grid of your documents
// (newest change first) with thumbnails, search and "Open in Onshape" (system browser); opening a
// document lists its Part Studios and Assemblies, each with "Import to build".
//
// Every list, search, uncached thumbnail and import is a real Onshape request against the yearly
// allowance (the top-bar meter moves with each one), so searching runs on Enter, not per keystroke,
// and pages load on "Load more". Onshape is only read; an import mirrors the element's bounding box
// as a local part (C5 ruling), so its mass is an estimate. Names are user-authored text: plain text.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { CoreError } from "../../../api/client";
import { ONSHAPE_PAGE, type OnshapeDoc, type OnshapeElement, type PartResult } from "../../../api/directory";
import { actions, conn, coreStatus } from "../../../state/store";
import { Button } from "../../common/Button";
import { ExternalLink } from "../../common/ExternalLink";
import { describeError } from "../../profile/common";
import { openProfile } from "../../profile/open";
import { BuildPicker, useBuilds } from "../directory/BuildPicker";
import { onshapeDocUrl } from "../directory/model";
import { PageShell } from "../PageShell";
import "../directory/directory.css";
import "./onshape.css";

/** Thumbnails already fetched this session (object URLs), by core thumbnail path. */
const thumbs = new Map<string, string>();

function notConnected(e: unknown): boolean {
  return e instanceof CoreError && e.code === "denied" && /not connected/i.test(e.message);
}

export function OnshapePage(): JSX.Element {
  const status = coreStatus.value;
  const [docs, setDocs] = useState<OnshapeDoc[] | null>(null);
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [disconnected, setDisconnected] = useState(false);
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<OnshapeDoc | null>(null);
  const stream = conn.value;
  const known = status?.onshape;

  async function load(offset: number, live: () => boolean = () => true): Promise<void> {
    setLoading(true);
    setError(null);
    try {
      const page = await actions.client().onshapeDocuments({ q: query, offset, limit: ONSHAPE_PAGE });
      if (!live()) return;
      setDisconnected(false);
      setDocs((cur) => (offset === 0 || !cur ? page : [...cur, ...page.filter((d) => !cur.some((c) => c.did === d.did))]));
      setMore(page.length === ONSHAPE_PAGE);
    } catch (e) {
      if (!live()) return;
      if (notConnected(e)) setDisconnected(true);
      else setError(describeError(e));
    } finally {
      if (live()) setLoading(false);
    }
  }

  useEffect(() => {
    if (known === "not_configured" || stream !== "open") return;
    let alive = true;
    void load(0, () => alive);
    return () => { alive = false; };
  }, [query, known, stream]);

  if (known === "not_configured" || disconnected) {
    return (
      <PageShell title="Onshape">
        <div class="os-connect" data-onshape="connect">
          <h2 class="os-connect-title">Connect Onshape</h2>
          <p class="dim">Add your Onshape API keys to browse your documents here and bring Part Studios and Assemblies into a build. Mises only reads from Onshape.</p>
          <p class="faint">Each page of documents, each new thumbnail and each import uses calls from your yearly Onshape allowance. The meter in the top bar shows how many are left.</p>
          <Button variant="ink" onClick={() => openProfile("onshape")}>Add Onshape keys</Button>
        </div>
      </PageShell>
    );
  }

  if (open) {
    return (
      <PageShell title="Onshape">
        <DocView doc={open} onBack={() => setOpen(null)} />
      </PageShell>
    );
  }

  return (
    <PageShell title="Onshape">
      <div class="os" data-onshape="docs">
        <form class="os-toolbar" role="search" onSubmit={(e) => { e.preventDefault(); setQuery(q.trim()); }}>
          <input type="search" class="dir-search" aria-label="Search your Onshape documents" placeholder="Search your documents" value={q}
            onInput={(e) => setQ(e.currentTarget.value)} />
          <Button type="submit" disabled={loading}>Search</Button>
          {query && <Button variant="ghost" onClick={() => { setQ(""); setQuery(""); }}>Clear</Button>}
          <span class="faint os-cost">Each search or page uses 1 Onshape call.</span>
        </form>
        {error && <p class="dir-err" role="alert">{error}</p>}
        {docs === null && !error && <p class="faint dir-note">Loading your documents…</p>}
        {docs !== null && docs.length === 0 && !loading && (
          <p class="dim dir-note">{query ? `No document matches “${query}”.` : "No documents in My Documents yet."}</p>
        )}
        {docs !== null && docs.length > 0 && (
          <ul class="os-grid" aria-label={`${docs.length} documents`}>
            {docs.map((d) => <li key={d.did}><DocTile doc={d} onOpen={() => setOpen(d)} /></li>)}
          </ul>
        )}
        {more && docs && (
          <div class="os-more"><Button disabled={loading} onClick={() => { void load(docs.length); }}>{loading ? "Loading…" : "Load more"}</Button></div>
        )}
      </div>
    </PageShell>
  );
}

function fmtModified(iso: string | null): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  return Number.isNaN(t) ? "" : new Date(t).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function Thumb({ url, name }: { url: string | null; name: string }): JSX.Element {
  const [src, setSrc] = useState<string | null>(url ? thumbs.get(url) ?? null : null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!url || thumbs.has(url)) return;
    let live = true;
    actions.client().onshapeThumbnail(url).then((blob) => {
      const obj = URL.createObjectURL(blob);
      thumbs.set(url, obj);
      if (live) setSrc(obj);
    }, () => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [url]);
  if (src) return <img class="os-thumb" src={src} alt="" aria-hidden="true" data-thumb="loaded" />;
  return <span class="os-thumb os-thumb-empty" aria-hidden="true" data-thumb={failed || !url ? "none" : "loading"}>{name.slice(0, 1).toUpperCase()}</span>;
}

function DocTile({ doc, onOpen }: { doc: OnshapeDoc; onOpen: () => void }): JSX.Element {
  const href = onshapeDocUrl(doc.did, doc.default_wid);
  return (
    <div class="os-tile" data-doc={doc.did}>
      <button type="button" class="os-tile-open" onClick={onOpen} aria-label={`Open ${doc.name || "untitled document"}`}>
        <Thumb url={doc.thumbnail_url} name={doc.name || "?"} />
        <span class="os-tile-name">{doc.name || "Untitled"}</span>
        <span class="os-tile-meta faint">{[doc.owner, fmtModified(doc.modified_at)].filter(Boolean).join(", ")}</span>
      </button>
      {href && <ExternalLink class="os-tile-link" href={href}>Open in Onshape (cad.onshape.com)</ExternalLink>}
    </div>
  );
}

const TYPE_LABEL: Record<OnshapeElement["type"], string> = { partstudio: "Part Studio", assembly: "Assembly" };

function DocView({ doc, onBack }: { doc: OnshapeDoc; onBack: () => void }): JSX.Element {
  const [els, setEls] = useState<OnshapeElement[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, { busy?: boolean; ok?: PartResult; err?: string }>>({});
  const { builds, buildId } = useBuilds();
  const back = useRef<HTMLButtonElement>(null);
  const href = onshapeDocUrl(doc.did, doc.default_wid);

  useEffect(() => {
    back.current?.focus();
    if (!doc.default_wid) { setError("This document has no workspace Mises can read."); return; }
    let live = true;
    actions.client().onshapeElements(doc.did, doc.default_wid).then((e) => { if (live) setEls(e); }, (e: unknown) => { if (live) setError(describeError(e)); });
    return () => { live = false; };
  }, [doc.did]);

  async function importEl(el: OnshapeElement): Promise<void> {
    if (!doc.default_wid) return;
    setResults((r) => ({ ...r, [el.eid]: { busy: true } }));
    try {
      const ok = await actions.client().onshapeImport({ did: doc.did, wid: doc.default_wid, eid: el.eid, project_id: buildId });
      setResults((r) => ({ ...r, [el.eid]: { ok } }));
    } catch (e) {
      setResults((r) => ({ ...r, [el.eid]: { err: describeError(e) } }));
    }
  }

  const buildName = builds?.find((b) => b.id === buildId)?.name ?? "the build";
  return (
    <div class="os-doc" data-onshape="doc" data-doc={doc.did}>
      <div class="os-doc-head">
        <button ref={back} type="button" class="btn btn-ghost" onClick={onBack}>Back to documents</button>
        <h2 class="os-doc-name">{doc.name || "Untitled"}</h2>
        {href && <ExternalLink class="btn" href={href}>Open in Onshape (cad.onshape.com)</ExternalLink>}
      </div>
      <div class="dir-actions">
        <BuildPicker builds={builds} buildId={buildId} label="Import into build" />
        <span class="faint">Each import uses 2 Onshape calls and mirrors the element's bounding box as a part.</span>
      </div>
      {error && <p class="dir-err" role="alert">{error}</p>}
      {els === null && !error && <p class="faint dir-note">Loading Part Studios and Assemblies…</p>}
      {els !== null && els.length === 0 && <p class="dim dir-note">This document has no Part Studios or Assemblies.</p>}
      {els !== null && els.length > 0 && (
        <ul class="os-els" aria-label="Part Studios and Assemblies">
          {els.map((el) => {
            const r = results[el.eid] ?? {};
            return (
              <li key={el.eid} class="os-el" data-element={el.eid}>
                <span class="os-el-type dim">{TYPE_LABEL[el.type]}</span>
                <span class="os-el-name">{el.name || "Untitled"}</span>
                <Button disabled={r.busy || !buildId} onClick={() => { void importEl(el); }}>{r.busy ? "Importing…" : "Import to build"}</Button>
                {r.ok && (
                  <p class="dir-ok os-el-result" role="status" data-imported={r.ok.part_id}>
                    {r.ok.existing ? `Already in ${buildName} as ${r.ok.part_id}.` : `Imported into ${buildName} as ${r.ok.part_id}.`}
                    {" "}Bounding-box mirror; mass{r.ok.metrics?.mass_g !== undefined ? ` ${r.ok.metrics.mass_g.toFixed(1)} g` : ""} <span class="dir-estimate">estimate</span>
                  </p>
                )}
                {r.err && <p class="dir-err os-el-result" role="alert">{r.err}</p>}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
