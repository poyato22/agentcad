// The active part's variables, editable: a scale (slider) and a number field per var with its
// unit and range from the script's `# mises:` annotation. A range the core only guessed
// (`inferred`) draws as a dashed scale and says so. Changes rebuild the part (debounced
// POST /vars on the version you started from). If an agent changed the part meanwhile, the 409
// asks: reload (take theirs) or keep mine (apply yours on top). A value an agent just set stays
// blue, with what it was before.
import type { JSX } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import type { VarSpec } from "../../api/builds";
import { runLabel } from "../../lib/names";
import { loadParts } from "../../state/builds";
import { ensureSource, sources, varSpecs } from "../../state/partSource";
import { describeVarsEdit, fmtNum, lastAgentChange, noteMyEdit, versionOf } from "../../state/partEdits";
import { actions, runs } from "../../state/store";
import { activeChanges, activePart, activeRun, allRuns } from "./derived";
import { VarsEditor } from "./varsEdit";


/** One editor per part for the session, so switching parts keeps an unsaved value. */
const editors = new Map<string, VarsEditor>();

export function editorFor(partId: string): VarsEditor {
  let ed = editors.get(partId);
  if (!ed) {
    ed = new VarsEditor(partId, {
      onSaved: ({ result, sent, prev }) => {
        const unit = new Map(varSpecs({ ...sent, ...result.vars }, sources.peek().get(partId)).map((s) => [s.name, s.unit]));
        const changes = Object.keys(sent).map((name) => ({ name, prev: prev[name] ?? null, now: result.vars[name] ?? sent[name], unit: unit.get(name) ?? null }));
        noteMyEdit(partId, result.version, describeVarsEdit(partId, changes));
      },
      client: () => actions.client(),
    });
    editors.set(partId, ed);
  }
  return ed;
}

/** Tests: forget every editor. */
export function resetVarEditors(): void {
  for (const e of editors.values()) e.dispose();
  editors.clear();
}

function niceStep(spec: VarSpec): number {
  if (spec.step && spec.step > 0) return spec.step;
  const span = (spec.max ?? spec.value * 4) - (spec.min ?? 0);
  if (!(span > 0)) return 0.1;
  const raw = span / 200;
  const p = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 5, 10].map((m) => m * p).find((s) => s >= raw) ?? raw;
}

export function Variables(): JSX.Element {
  const part = activePart.value;
  const changes = activeChanges.value;
  const run = activeRun.value;
  const entry = part ? sources.value.get(part.part_id) : undefined;
  const version = part ? versionOf(part) : 0;
  const [, tick] = useState(0);

  useEffect(() => { if (part && part.kind !== "group" && part.metrics) ensureSource(part.part_id, version); }, [part?.part_id, version]);
  const specs = useMemo(() => (part ? varSpecs(part.vars, entry) : []), [part, entry]);
  const ed = part ? editorFor(part.part_id) : null;
  useEffect(() => ed?.subscribe(() => tick((n) => n + 1)), [ed]);

  if (!part) return <div class="pane-empty">No part selected.</div>;
  if (!ed) return <div class="pane-empty">No part selected.</div>;
  const names = specs.map((s) => s.name);
  for (const n of changes.keys()) if (!names.includes(n)) names.push(n);
  if (names.length === 0) return <div class="pane-empty">This part has no variables.</div>;
  const who = run ? runLabel(run, allRuns.value) : "an agent";
  const st = ed.status;
  const specOf = (n: string): VarSpec => specs.find((s) => s.name === n) ?? { name: n, value: part.vars[n] ?? 0, min: null, max: null, step: null, unit: "mm", doc: null, inferred: true };

  return (
    <div class="vars-wrap">
      <div class="vars" role="group" aria-label={`Variables of ${part.part_id}`}>
        {names.map((name) => {
          const spec = specOf(name);
          const c = changes.get(name);
          // whichever is newer: the part list (current state) or the run's change (its diff step can
          // land before the part list refetch does)
          const listValue = c && (!(name in part.vars) || c.ts > part.mtime) ? c.now : part.vars[name];
          const value = ed.shown(name, part.version ?? 0, listValue) ?? 0;
          const draft = ed.drafts.has(name);
          const title = c && !draft ? `was ${c.prev === null ? "unknown" : c.prev.toFixed(2)} · set by ${who}` : spec.doc ?? undefined;
          return (
            <VarRow key={name} spec={spec} value={value} agentChanged={!!c && !draft} draft={draft} title={title}
              onChange={(v) => ed.set(name, v, versionOf(part), part.vars)} onCommit={() => { void ed.send(); }} />
          );
        })}
      </div>
      <VarsStatusLine ed={ed} partId={part.part_id} status={st} />
    </div>
  );
}

function VarRow({ spec, value, agentChanged, draft, title, onChange, onCommit }: {
  spec: VarSpec; value: number; agentChanged: boolean; draft: boolean; title: string | undefined;
  onChange(v: number): void; onCommit(): void;
}): JSX.Element {
  const [text, setText] = useState<string | null>(null);   // while typing
  const lo = spec.min ?? Math.min(0, value);
  const hi = spec.max ?? Math.max(value * 4, lo + 1);
  const step = niceStep(spec);
  const id = `var-in-${spec.name}`;
  const outside = value < lo || value > hi;
  return (
    <div class={`var-row${spec.inferred ? " inferred" : ""}`} data-var={spec.name}>
      <label class="var-name" for={id} title={spec.doc ?? spec.name}>{spec.name}</label>
      <span class="var-field">
        <input id={id} class={agentChanged ? "var-value changed" : draft ? "var-value draft" : "var-value"} data-testid={`var-${spec.name}`}
          type="text" inputMode="decimal" spellcheck={false} value={text ?? fmtNum(value)} title={title}
          data-changed={agentChanged ? "true" : undefined} data-changing={agentChanged ? "" : undefined}
          aria-describedby={`${id}-range`}
          onInput={(e) => {
            const t = (e.target as HTMLInputElement).value;
            setText(t);
            const v = Number(t);
            if (t.trim() !== "" && Number.isFinite(v)) onChange(v);
          }}
          onBlur={() => setText(null)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); setText(null); onCommit(); }
            if (e.key === "ArrowUp" || e.key === "ArrowDown") {
              e.preventDefault();
              const v = Number((value + (e.key === "ArrowUp" ? 1 : -1) * step * (e.shiftKey ? 10 : 1)).toFixed(6));
              setText(null);
              onChange(v);
            }
          }} />
        <span class="var-unit faint">{spec.unit ?? ""}</span>
      </span>
      <input type="range" class="var-scale" min={lo} max={hi} step={step} value={Math.min(hi, Math.max(lo, value))}
        aria-label={`${spec.name}${spec.unit ? ` (${spec.unit})` : ""}`} aria-valuetext={`${fmtNum(value)}${spec.unit ? ` ${spec.unit}` : ""}`}
        data-outside={outside ? "" : undefined}
        onInput={(e) => { setText(null); onChange(Number((e.target as HTMLInputElement).value)); }}
        onChange={() => onCommit()} />
      <span class="var-range faint" id={`${id}-range`}
        title={spec.inferred ? "Suggested range: the script doesn't set one (add `# mises: min= max=` to its VARS line)" : "Range set in the script"}>
        {`${spec.inferred ? "~" : ""}${fmtNum(lo)} – ${fmtNum(hi)}`}
      </span>
    </div>
  );
}

function VarsStatusLine({ ed, partId, status }: { ed: VarsEditor; partId: string; status: VarsEditor["status"] }): JSX.Element | null {
  if (status.kind === "idle") return null;
  if (status.kind === "pending") return <div class="vars-status faint" role="status">Rebuilding when you stop…</div>;
  if (status.kind === "saving") return <div class="vars-status" role="status" data-changing>Rebuilding…</div>;
  if (status.kind === "error") {
    return (
      <div class="vars-status vars-error" role="alert">
        <span>{status.message}</span>
        <span class="vars-actions">
          <button type="button" class="btn" onClick={() => { void ed.send(); }}>Try again</button>
          <button type="button" class="btn" onClick={() => ed.discard()}>Undo my change</button>
        </span>
      </div>
    );
  }
  const change = lastAgentChange.value.get(partId);
  const run = change?.run_id ? runs.value.get(change.run_id) : undefined;
  const who = run ? runLabel(run, [...runs.value.values()]) : change ? "An agent" : "Someone else";
  const theirs = status.theirs ? Object.entries(status.theirs).filter(([k]) => ed.drafts.has(k)).map(([k, v]) => `${k} ${fmtNum(v)}`).join(", ") : "";
  return (
    <div class="vars-status vars-conflict needs-you" role="alert" data-conflict>
      <span>{`${who} changed this part${theirs ? ` (now ${theirs})` : ""} while you were editing.`}</span>
      <span class="vars-actions">
        <button type="button" class="btn" onClick={() => { ed.discard(); void loadParts(); }}>Reload</button>
        <button type="button" class="btn" onClick={() => { void ed.keepMine(); }}>Keep mine</button>
      </span>
    </div>
  );
}
