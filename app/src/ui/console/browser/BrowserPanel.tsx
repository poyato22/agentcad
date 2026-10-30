// The agent's "Browser" tab (R3): the pages this run visited (web_open), in order, as readable
// snapshots — title, the domain next to the full URL, "Open in browser" (openExternal), the
// extracted text, any tables, and the proxied images. Everything a snapshot carries is untrusted
// data (a fetched page), so it is rendered as plain escaped text; images load only through the
// core proxy. There is no address bar and no live page: this is the hardened fetcher's snapshot,
// not a web browser.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import type { BrowserSnapshot } from "../../../api/browser";
import { CoreError } from "../../../api/client";
import { effectiveRunId } from "../derived";
import { actions } from "../../../state/store";
import { linkHost } from "../../markup/parse";
import { Button } from "../../common/Button";
import { ExternalLink } from "../../common/ExternalLink";
import { ProxyImage } from "../../common/ProxyImage";
import { fmtFetched } from "./model";
import { setConsoleView } from "../view";
import "./browser.css";

type Load = { state: "loading" } | { state: "error"; message: string } | { state: "ready"; snapshots: BrowserSnapshot[] };

export function BrowserPanel(): JSX.Element {
  const runId = effectiveRunId.value;
  const [load, setLoad] = useState<Load>({ state: "loading" });

  useEffect(() => {
    if (!runId) { setLoad({ state: "ready", snapshots: [] }); return; }
    let live = true;
    setLoad({ state: "loading" });
    actions.client().runBrowser(runId).then(
      (snapshots) => { if (live) setLoad({ state: "ready", snapshots }); },
      (e: unknown) => { if (live) setLoad({ state: "error", message: e instanceof CoreError ? e.message : "Couldn't load this agent's pages" }); },
    );
    return () => { live = false; };
  }, [runId]);

  return (
    <div class="con" data-console data-view="browser">
      <div class="cv-detailsbar">
        <span class="label">Browser</span>
        <span class="cv-spacer" />
        <Button variant="ghost" onClick={() => setConsoleView("chat")}>Conversation</Button>
      </div>
      <div class="brz-list" data-browser-panel>
        {load.state === "loading" && <p class="dim brz-empty">Loading…</p>}
        {load.state === "error" && <p class="cv-fail brz-empty text">{load.message}</p>}
        {load.state === "ready" && load.snapshots.length === 0 && (
          <p class="dim brz-empty">This agent hasn't opened any pages yet. When it looks up a part, each page shows here.</p>
        )}
        {load.state === "ready" && load.snapshots.map((s) => <SnapshotCard key={s.id} snap={s} />)}
      </div>
    </div>
  );
}

function SnapshotCard({ snap }: { snap: BrowserSnapshot }): JSX.Element {
  const host = linkHost(snap.url);
  return (
    <article class="brz-snap" data-snapshot={snap.id}>
      <header class="brz-head">
        <h3 class="brz-title text">{snap.title || host || "Untitled page"}</h3>
        <div class="brz-urlrow">
          {host && <span class="brz-host faint text">{host}</span>}
          {snap.fetched_at > 0 && <span class="brz-when faint">{fmtFetched(snap.fetched_at)}</span>}
          {snap.url && <ExternalLink class="brz-open" href={snap.url}>Open in browser</ExternalLink>}
        </div>
        {snap.url && <div class="brz-url faint text" title={snap.url}>{snap.url}</div>}
      </header>

      {snap.images.length > 0 && (
        <div class="brz-imgs">
          {snap.images.map((im, i) => <ProxyImage key={i} path={im.proxy_url} alt={im.alt ?? ""} class="brz-img" />)}
        </div>
      )}

      {snap.text.trim() !== "" && <p class="brz-text text">{snap.text}</p>}

      {snap.tables.map((t, ti) => (
        <table key={ti} class="brz-table">
          {t.headers.length > 0 && (
            <thead><tr>{t.headers.map((h, hi) => <th key={hi} class="text">{h}</th>)}</tr></thead>
          )}
          <tbody>
            {t.rows.map((row, ri) => <tr key={ri}>{row.map((cell, ci) => <td key={ci} class="text">{cell}</td>)}</tr>)}
          </tbody>
        </table>
      ))}
    </article>
  );
}
