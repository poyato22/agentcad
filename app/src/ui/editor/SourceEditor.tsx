// The Code tab: the focused part's build123d script in CodeMirror (Python). Save (⌘S) rebuilds the
// part in the sandbox; your `# mises-op` features are the marked lines. The script is data from
// the part (possibly agent-written): it is only ever shown as editor text, never run here.
import type { EditorView } from "@codemirror/view";
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import type { PartSummary } from "../../api/types";
import { partName, runLabel } from "../../lib/names";
import { isMyVersion, knownVersions, lastAgentChange, nameOf, noteMyEdit, versionOf } from "../../state/partEdits";
import { actions, runs } from "../../state/store";
import { createEditor, setDoc } from "./cm";
import "./editor.css";
import { SourceSession } from "./sourceEdit";

/** One session per part for the app's life, so unsaved code survives switching parts or tabs. */
const sessions = new Map<string, SourceSession>();

export function sessionFor(partId: string): SourceSession {
  let s = sessions.get(partId);
  if (!s) {
    s = new SourceSession(partId, {
      client: () => actions.client(),
      onSaved: (r) => noteMyEdit(partId, r.version, `edited the script of ${nameOf(partId)}`),
    });
    sessions.set(partId, s);
  }
  return s;
}

export function resetSourceSessions(): void { sessions.clear(); }

export function SourceEditor({ part }: { part: PartSummary }): JSX.Element {
  const s = sessionFor(part.part_id);
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const [, tick] = useState(0);
  void knownVersions.value; // re-render when a newer version is seen
  const version = versionOf(part);

  useEffect(() => {
    if (!host.current) return;
    view.current = createEditor(host.current, s.text, { onChange: (doc) => s.edit(doc), onSave: () => { void s.save(); } });
    const off = s.subscribe((reload) => {
      if (reload && view.current && view.current.state.doc.toString() !== s.text) setDoc(view.current, s.text);
      tick((n) => n + 1);
    });
    // the contract: a fresh read whenever the editor opens (unless unsaved text is waiting)
    if (!s.dirty) void s.open();
    return () => { off(); view.current?.destroy(); view.current = null; };
  }, [s]);

  useEffect(() => { s.external(version); }, [s, version]);

  const st = s.status;
  const change = lastAgentChange.value.get(part.part_id);
  const run = change?.run_id ? runs.value.get(change.run_id) : undefined;
  const who = run ? runLabel(run, [...runs.value.values()]) : change ? "An agent" : "Someone else";
  // your own vars / feature edit bumps the version too: say so, not "an agent"
  const whoAt = (v: number) => (isMyVersion(part.part_id, v) ? "You (a variable or feature edit)" : who);

  return (
    <div class="src" data-testid="source-editor">
      <div class="src-bar">
        <span class="src-name">{partName(part)}</span>
        <span class="faint" data-version={s.base ?? undefined}>{s.base !== null ? `version ${s.base}` : ""}</span>
        <span class="src-state" role="status">
          {st.kind === "loading" && "Reading the script…"}
          {st.kind === "saving" && <span data-changing>Rebuilding…</span>}
          {st.kind === "saved" && "Saved and rebuilt."}
          {st.kind === "ready" && (s.dirty ? "Unsaved changes" : "")}
        </span>
        <span class="src-actions">
          <button type="button" class="btn" disabled={!s.dirty || st.kind === "saving"} onClick={() => { view.current?.focus(); setDoc(view.current as EditorView, s.loaded); s.edit(s.loaded); }}>Revert</button>
          <button type="button" class="btn" disabled={!s.dirty || st.kind === "saving" || st.kind === "conflict"} title="Save and rebuild (⌘S)"
            onClick={() => { void s.save(); }}>Save</button>
        </span>
      </div>
      {st.kind === "behind" && (
        <div class="src-note needs-you" role="alert">
          {`${whoAt(st.current)} changed this part (now version ${st.current}) since you opened the script. Saving will ask what to keep.`}
          <span class="src-note-actions"><button type="button" class="btn" onClick={() => { void s.loadTheirs(); }}>Load theirs</button></span>
        </div>
      )}
      {st.kind === "conflict" && (
        <div class="src-note needs-you" role="alert" data-conflict>
          {st.current === null
            ? "This part changed since you opened the script. Your edit was not saved; load the current script to go on."
            : `${whoAt(st.current)} changed this part (version ${st.current}) since you opened the script. Your edit was not saved.`}
          <span class="src-note-actions">
            <button type="button" class="btn" onClick={() => { void s.loadTheirs(); }}>Load theirs</button>
            {st.current !== null && <button type="button" class="btn" onClick={() => { void s.keepMine(); }}>Keep mine</button>}
          </span>
        </div>
      )}
      {st.kind === "error" && <div class="src-note src-error" role="alert">{st.message}</div>}
      <div class="src-cm" ref={host} />
    </div>
  );
}
