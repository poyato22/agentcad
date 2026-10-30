// The agent's "Browser" tab (R3): the pages this run read, oldest first, as readable snapshots
// (GET /runs/{id}/browser?full=1). A product page shows its title, the domain next to the full
// URL, "Open in browser" (openExternal), the extracted text, tables and the proxied images; a
// site search shows its results; a page that refused automated access says so. Everything a
// snapshot carries is untrusted data (a fetched page), rendered as plain escaped text; images
// load only through the core proxy. There is no address bar and no live page: this is the
// hardened fetcher's snapshot, not a web browser. The list refetches when the run reads another
// page (tool_result of web_open / web_search).
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import type { BrowserSnapshot } from "../../../api/browser";
import { CoreError } from "../../../api/client";
import { onCoreEvent } from "../../../api/events";
import { actions } from "../../../state/store";
import { linkHost } from "../../markup/parse";
import { Button } from "../../common/Button";
import { ExternalLink } from "../../common/ExternalLink";
import { ProxyImage } from "../../common/ProxyImage";
import { effectiveRunId } from "../derived";
import { setConsoleView } from "../view";
import { fmtFetched } from "./model";
import "./browser.css";

type Load = { state: "loading" } | { state: "error"; message: string } | { state: "ready"; snapshots: BrowserSnapshot[] };

const BROWSE_TOOLS = /(?:^|__)(web_open|web_search)$/;

export function BrowserPanel(): JSX.Element {
  const runId = effectiveRunId.value;
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [tick, setTick] = useState(0);

  useEffect(() => onCoreEvent((ev) => {
    if (ev.type === "tool_result" && ev.run_id === runId && typeof ev.data.name === "string" && BROWSE_TOOLS.test(ev.data.name)) setTick((n) => n + 1);
  }), [runId]);

  useEffect(() => {
    if (!runId) { setLoad({ state: "ready", snapshots: [] }); return; }
    let live = true;
    if (tick === 0) setLoad({ state: "loading" });
    actions.client().runBrowser(runId).then(
      (snapshots) => { if (live) setLoad({ state: "ready", snapshots }); },
      (e: unknown) => { if (live) setLoad({ state: "error", message: e instanceof CoreError ? e.message : "Couldn't load this agent's pages" }); },
    );
    return () => { live = false; };
  }, [runId, tick]);

  return (
    <div class="con" data-console data-view="browser">
      <div class="cv-detailsbar">
        <span class="label">Browser</span>
        <span class="cv-spacer" />
        <Button variant="ghost" onClick={() => setConsoleView("chat")}>Conversation</Button>
      </div>
      <div class="brz-list" data-browser-panel>
        {load.state === "loading" && <p class="dim brz-empty">Loading…</p>}
        {load.state === "error" && <p class="cv-fail brz-empty text" role="status">{load.message}</p>}
        {load.state === "ready" && load.snapshots.length === 0 && (
          <p class="dim brz-empty">This agent hasn't opened any pages yet. When it looks up a part, each page shows here.</p>
        )}
        {load.state === "ready" && load.snapshots.map((s) => <SnapshotCard key={s.id} snap={s} />)}
      </div>
    </div>
  );
}

function SnapshotCard({ snap }: { snap: BrowserSnapshot }): JSX.Element {
  const host = snap.domain || linkHost(snap.final_url || snap.url);
  const url = snap.final_url || snap.url;
  return (
    <article class="brz-snap" data-snapshot={snap.id} data-kind={snap.kind} data-blocked={snap.blocked ? "" : undefined}>
      <header class="brz-head">
        <div class="brz-kind faint">{snap.kind === "search" ? `Search${snap.site ? ` · ${snap.site}` : ""}` : "Page"}</div>
        <h3 class="brz-title text">{snap.title || host || "Untitled page"}</h3>
        <div class="brz-urlrow">
          {host && <span class="brz-host faint text">{host}</span>}
          {snap.fetched_at > 0 && <span class="brz-when faint">{fmtFetched(snap.fetched_at)}</span>}
          {url && <ExternalLink class="brz-open" href={url}>Open in browser</ExternalLink>}
        </div>
        {url && <div class="brz-url faint text" title={url}>{url}</div>}
      </header>

      {snap.blocked && <p class="brz-blocked text" role="note">{`${snap.blocked}. Open it in your browser to read it.`}</p>}

      {snap.kind === "search" && snap.results.length > 0 && (
        <ol class="brz-results">
          {snap.results.map((r, i) => (
            <li key={i}>
              <ExternalLink class="brz-result-title" href={r.url}>{r.title || r.url}</ExternalLink>
              <span class="brz-host faint text">{` ${linkHost(r.url)}`}</span>
              {r.snippet && <div class="brz-snippet faint text">{r.snippet}</div>}
            </li>
          ))}
        </ol>
      )}

      {snap.images.length > 0 && (
        <div class="brz-imgs">
          {snap.images.map((im, i) => <ProxyImage key={i} path={im.proxy_url} alt={im.alt ?? ""} class="brz-img" />)}
        </div>
      )}

      {snap.kind === "page" && snap.text.trim() !== "" && <p class="brz-text text">{snap.text}</p>}

      {snap.tables.map((t, ti) => (
        <table key={ti} class="brz-table">
          <tbody>
            {t.rows.map((row, ri) => <tr key={ri}>{row.map((cell, ci) => <td key={ci} class="text">{cell}</td>)}</tr>)}
          </tbody>
        </table>
      ))}
    </article>
  );
}
