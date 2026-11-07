// Error log: the recent errors from <MISES_HOME>/logs/errors.jsonl (every error card, red text,
// failed tool step, failed run or study, and uncaught exception), with filters, "Copy for bug
// report" and "Open log file". Entries come back already scrubbed of secrets by the core. The
// whole view is [data-no-error-log], so the log never logs itself.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import { errorSources, type ErrorEntry } from "../../../api/errorLog";
import { actions } from "../../../state/store";
import { Button } from "../../common/Button";
import { Card, describeError, SectionBox } from "../common";

function when(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}

/** Plain text a bug report can carry: one block per entry, stacks included. */
export function bugReportText(entries: ErrorEntry[], path: string): string {
  const blocks = entries.map((e) => [
    `[${when(e.ts)}] ${e.source}${e.code ? ` ${e.code}` : ""}${e.count > 1 ? ` (x${e.count})` : ""}`,
    e.message,
    e.hint ? `hint: ${e.hint}` : null,
    [e.build_id && `build ${e.build_id}`, e.run_id && `run ${e.run_id}`, e.tool && `tool ${e.tool}`, e.version && `version ${e.version}`]
      .filter(Boolean).join(" · ") || null,
    e.stack ? e.stack.trimEnd() : null,
  ].filter(Boolean).join("\n"));
  return [`Mises error log (${entries.length} entr${entries.length === 1 ? "y" : "ies"}; file ${path})`, ...blocks].join("\n\n");
}

export function ErrorLogSection(): JSX.Element {
  const [source, setSource] = useState("");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<ErrorEntry[] | null>(null);
  const [path, setPath] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  async function load(): Promise<void> {
    try {
      const r = await actions.client().errorLog({ source: source || undefined, q: q || undefined, limit: 200 });
      setRows(r.entries);
      setPath(r.path);
      setNote(null);
    } catch (e) {
      setRows([]);
      setNote(`could not read the error log: ${describeError(e)}`);
    }
  }
  useEffect(() => { void load(); }, [source, q]);

  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(bugReportText(rows ?? [], path));
      setNote("copied");
    } catch {
      setNote("copy failed");
    }
  }
  async function reveal(): Promise<void> {
    try {
      await actions.client().revealErrorLog();
      setNote(null);
    } catch (e) {
      setNote(`could not open it: ${describeError(e)}`);
    }
  }

  return (
    <SectionBox title="Error log">
      <div data-no-error-log data-testid="error-log">
        <Card title="Recent errors">
          <p class="dim">Every error Mises showed you, newest first, with secrets removed.</p>
          {path && <code class="profile-code text" data-testid="error-log-path">{path}</code>}
          <div class="profile-row">
            <select aria-label="Source" value={source} onChange={(e) => setSource((e.target as HTMLSelectElement).value)}>
              <option value="">All sources</option>
              {errorSources.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <input type="search" aria-label="Filter errors" placeholder="Filter" value={q}
                   onInput={(e) => setQ((e.target as HTMLInputElement).value)} />
            <Button onClick={() => { void load(); }}>Refresh</Button>
            <Button disabled={!rows?.length} onClick={() => { void copy(); }}>Copy for bug report</Button>
            <Button onClick={() => { void reveal(); }}>Open log file</Button>
            {note && <span class="dim">{note}</span>}
          </div>
          {rows === null && <p class="dim">Loading…</p>}
          {rows?.length === 0 && !note && <p class="dim">No errors logged.</p>}
          <ul class="errlog-list" aria-label="Errors">
            {rows?.map((e) => {
              const id = e.fp ?? `${e.ts}${e.message}`;
              return (
                <li key={id} class="errlog-row" data-testid="error-log-row">
                  <button type="button" class="errlog-head" aria-expanded={open === id} onClick={() => setOpen(open === id ? null : id)}>
                    <span class="faint">{when(e.ts)}</span> <span class="dim">{e.source}{e.code ? ` · ${e.code}` : ""}{e.tool ? ` · ${e.tool}` : ""}</span>
                    {e.count > 1 && <span class="faint"> ×{e.count}</span>}
                  </button>
                  <div class="text">{e.message}</div>
                  {open === id && (
                    <div class="dim text">
                      {e.hint && <div>hint: {e.hint}</div>}
                      {(e.build_id || e.run_id) && <div>{e.build_id && `build ${e.build_id} `}{e.run_id && `run ${e.run_id}`}</div>}
                      {e.version && <div>version {e.version}</div>}
                      {e.stack && <pre class="profile-code errlog-stack">{e.stack}</pre>}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </Card>
      </div>
    </SectionBox>
  );
}
