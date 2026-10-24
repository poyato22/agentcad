import type { JSX } from "preact";
import { useState } from "preact/hooks";
import type { AgentRun, Step } from "../../api/types";
import { fmtElapsed, fmtSigned } from "../../lib/format";
import { runLabel } from "../../lib/names";
import { actions, runs, steps } from "../../state/store";
import { changedVars } from "./changedVars";
import { allRuns, newestFirst, num } from "./derived";
import { Led, type LedTone } from "../common/Led";

const TONE: Record<AgentRun["status"], LedTone> = { running: "blue", done: "green", failed: "red", cancelled: "faint" };

interface StepRow { id: number; text: string }

function isRec(v: unknown): v is Record<string, unknown> { return typeof v === "object" && v !== null && !Array.isArray(v); }

/** The diff/decision steps of a run as "v-step: thickness 5→6 · +0.96 g". */
export function stepRows(list: readonly Step[]): StepRow[] {
  const rows: StepRow[] = [];
  list.forEach((s, i) => {
    const out = isRec(s.payload["output"]) ? s.payload["output"] : null;
    const data = out && isRec(out["data"]) ? out["data"] : null;
    if (s.kind === "diff") {
      const partId = data && typeof data["part_id"] === "string" ? data["part_id"] : null;
      const ch = partId ? [...changedVars(partId, list.slice(0, i + 1)).values()].filter((c) => c.stepId === s.id) : [];
      const delta = data && isRec(data["delta"]) ? data["delta"] : null;
      const mass = delta && typeof delta["mass_g"] === "number" ? delta["mass_g"] : null;
      let text = ch.length
        ? ch.map((c) => `${c.name} ${c.prev === null ? "" : `${num(c.prev)}→`}${num(c.now)}`).join(", ")
        : typeof out?.["summary"] === "string" ? out["summary"] : "changed";
      if (mass !== null) text += ` · ${fmtSigned(mass, 2, " g")}`;
      rows.push({ id: s.id, text: `v-step: ${text}` });
    } else if (s.kind === "decision") {
      const why = typeof s.payload["reason"] === "string" ? s.payload["reason"] : typeof out?.["summary"] === "string" ? out["summary"] : "";
      rows.push({ id: s.id, text: `decision: ${why}`.trim() });
    }
  });
  return rows;
}

function RunItem({ run, open, onToggle }: { run: AgentRun; open: boolean; onToggle: () => void }): JSX.Element {
  const rows = open ? stepRows(steps.value.get(run.id) ?? []) : [];
  return (
    <li data-testid="run-row" data-run={run.id}>
      <button type="button" class="left-row" aria-expanded={open} onClick={() => { actions.selectRun(run.id); onToggle(); }}>
        <Led tone={TONE[run.status]} title={run.status} />
        <span class="row-name" data-label>{runLabel(run, allRuns.value)}</span>
        <span class="row-right faint">{fmtElapsed(run.updated_at - run.created_at)}</span>
      </button>
      {open && (
        <ul class="step-list">
          {rows.length === 0 && <li class="faint step-empty">no changes yet</li>}
          {rows.map((r) => (
            <li key={r.id}>
              <button type="button" class="step-row" onClick={() => { actions.selectRun(run.id); actions.focusStep(r.id); }}>{r.text}</button>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function History(): JSX.Element {
  const [openIds, setOpen] = useState<ReadonlySet<string>>(new Set());
  const list = newestFirst(runs.value);
  if (list.length === 0) return <div class="pane-empty">no runs yet</div>;
  const toggle = (id: string) => setOpen((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  return (
    <ul class="left-list">
      {list.map((r) => <RunItem key={r.id} run={r} open={openIds.has(r.id)} onToggle={() => toggle(r.id)} />)}
    </ul>
  );
}
