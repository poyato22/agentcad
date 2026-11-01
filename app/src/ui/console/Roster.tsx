// Agent roster: "AGENTS  N running · M needs you · 38k tok · $0.41", then one row per run:
// LED | label (provider · model faint below) | task | progress | elapsed or status.
// Blue: running (being changed by an agent), the selected row, "needs you". Green / red: done /
// failed only. Hovering a running row shows ✕ to cancel just that run.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import type { AgentRun } from "../../api/types";
import { fmtElapsed, fmtTokens, fmtUsd } from "../../lib/format";
import { providerLabel } from "../../lib/names";
import { debug } from "../../lib/log";
import { needsYou as needsYouSignal } from "../../state/selection";
import { actions, selectedRunId } from "../../state/store";
import { Led, type LedTone } from "../common/Led";
import { effectiveRunId, rosterRuns, runLabels } from "./derived";

const LED: Record<AgentRun["status"], LedTone> = { queued: "faint", running: "blue", done: "green", failed: "red", cancelled: "faint" };

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, [active]);
  return active ? Math.max(now, Date.now() / 1000) : now;
}

function cancelRun(id: string): void {
  try {
    actions.client().cancel(id).catch((e: unknown) => debug("cancel failed", id, e));
  } catch (e) {
    debug("cancel failed", id, e);
  }
}

export function Roster(): JSX.Element {
  const list = rosterRuns.value;
  const labels = runLabels.value;
  const selected = effectiveRunId.value;
  const { count: needsYou, runIds: needsRun } = needsYouSignal.value;
  const running = list.filter((r) => r.status === "running").length;
  const tokens = list.reduce((n, r) => n + r.input_tokens + r.output_tokens, 0);
  const usd = list.reduce((n, r) => n + r.usd, 0);
  const now = useNow(running > 0);
  // the first fallback (newest running run) becomes an explicit choice once, so the blue row and
  // the command target don't jump to a run that starts later
  useEffect(() => {
    if (selected !== null && selectedRunId.peek() === null) actions.selectRun(selected);
  }, [selected]);

  return (
    <section class="con-roster" data-roster aria-label="Agents">
      <header class="pane-head" data-roster-head>
        <span class="pane-title">Agents</span>
        <span class="ink">{`${running} running`}</span>
        <span class="pane-right">
          <span class={needsYou > 0 ? "con-needs needs-you" : ""}>{`${needsYou} needs you`}</span>
          {` · ${fmtTokens(tokens)} · ${fmtUsd(usd)}`}
        </span>
      </header>
      <div class="con-roster-rows" role="listbox" aria-label="Runs">
        {list.map((r) => {
          const label = labels.get(r.id) ?? r.agent;
          const sel = r.id === selected;
          return (
            <div key={r.id} class={`con-ag${sel ? " sel" : ""}`} data-run={r.id} role="option" aria-selected={sel}
              data-selected={sel ? "" : undefined} data-changing={r.status === "running" ? "" : undefined}
              tabIndex={0} onClick={() => actions.selectRun(r.id)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); actions.selectRun(r.id); } }}>
              <Led tone={needsRun.has(r.id) || r.status === "running" ? "blue" : LED[r.status]} title={needsRun.has(r.id) ? "needs you" : r.status} />
              <span class="con-ag-name">
                <span class="con-ag-label text" data-run-label>{label}</span>
                <span class="con-ag-who text">{`${providerLabel(r.provider)} · ${r.model}`}</span>
              </span>
              <span class="con-ag-task text" title={r.task}>{r.task.split("\n")[0]}</span>
              <span class={`con-bar con-bar-${r.status}`} aria-hidden="true"><i /></span>
              <span class="con-ag-t">{r.status === "running" ? fmtElapsed(now - r.created_at) : r.status}</span>
              {r.status === "running"
                ? <button type="button" class="con-ag-x" data-cancel aria-label={`Cancel ${label}`} title="cancel this run"
                    onClick={(e) => { e.stopPropagation(); cancelRun(r.id); }}>✕</button>
                : <span />}
            </div>
          );
        })}
        {list.length === 0 && <div class="pane-empty">no runs yet</div>}
      </div>
    </section>
  );
}
