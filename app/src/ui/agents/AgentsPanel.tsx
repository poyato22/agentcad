// The Agents panel (R10): one easy view of every build's agents. Each build shows its long-lived
// lead on top and the sub-agents it spawned indented beneath, with the task title, a status dot,
// live activity, and a click target that opens the conversation. Delete is a simple two-step
// confirm; a run that is still working says so instead of being forced away.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { onCoreEvent } from "../../api/events";
import type { AgentRun, DeleteRefused, RunNode } from "../../api/types";
import { activityByRun } from "../../state/activity";
import { actions } from "../../state/store";
import { Button } from "../common/Button";
import { Led, type LedTone } from "../common/Led";
import { activityLabel } from "./activityLabel";
import { agentRows, agentRowTitle } from "./panelModel";
import { agoText, closeAgentManager, costText, lastActive, refusalText, runTitle, subtreeIds } from "./manager";

const TONE: Record<AgentRun["status"], LedTone> = { queued: "faint", running: "blue", done: "green", failed: "red", cancelled: "faint" };
/** Refetch at most this often as runs start and finish. */
const RELOAD_MS = 300;

function message(e: unknown): string { return e instanceof Error ? e.message : String(e); }

interface BuildAgents { id: string; name: string; roots: RunNode[] }

export function AgentsPanel(): JSX.Element {
  const [builds, setBuilds] = useState<BuildAgents[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = async () => {
    try {
      const client = actions.client();
      const names = await client.projectNames().catch(() => new Map<string, string>());
      const entries = [...names.entries()];
      const trees = await Promise.all(entries.map(([id]) => client.projectAgents(id).catch(() => [] as RunNode[])));
      const next = entries
        .map(([id, name], i) => ({ id, name, roots: trees[i] }))
        .filter((b) => b.roots.length > 0)
        .sort((a, b) => a.name.localeCompare(b.name));
      setBuilds(next);
      setError(null);
    } catch (e) { setError(message(e)); }
  };

  useEffect(() => {
    void load();
    const off = onCoreEvent((ev) => {
      if (ev.type !== "run_started" && ev.type !== "run_finished" && ev.type !== "run_queued" && ev.type !== "run_deleted") return;
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => { void load(); }, RELOAD_MS);
    });
    return () => { off(); if (timer.current) clearTimeout(timer.current); };
  }, []);

  const byId = (() => {
    const m = new Map<string, RunNode>();
    const walk = (ns: readonly RunNode[]) => { for (const n of ns) { m.set(n.id, n); walk(n.children); } };
    for (const b of builds ?? []) walk(b.roots);
    return m;
  })();
  const labelOf = (id: string) => { const r = byId.get(id); return r ? `${r.agent} · ${runTitle(r)}` : id; };

  const setFold = (id: string, closed: boolean) => setCollapsed((s) => { const n = new Set(s); if (closed) n.add(id); else n.delete(id); return n; });
  const open = (id: string) => { actions.selectRun(id); closeAgentManager(); };
  const onDeleted = () => { void load(); };

  return (
    <div class="am-agents-view" data-agents-view>
      {error && <div class="am-error tone-red text" role="alert">{error}</div>}
      {builds === null && !error && <div class="am-empty faint">Loading agents…</div>}
      {builds !== null && builds.length === 0 && <div class="am-empty faint">No agents yet. Start one with New run (⌘N).</div>}
      {(builds ?? []).map((b) => (
        <section key={b.id} class="am-group" aria-label={`Agents in ${b.name}`}>
          <h3 class="am-group-h">{b.name}</h3>
          <ul class="ap-list" aria-label={`Agents in ${b.name}`}>
            {agentRows(b.roots, collapsed).map((row) => (
              <AgentRowView key={row.run.id} run={row.run} depth={row.depth} hasChildren={row.hasChildren} expanded={row.expanded}
                workers={subtreeIds(row.run).length - 1}
                labelOf={labelOf} onOpen={() => open(row.run.id)} onFold={() => setFold(row.run.id, row.expanded)} onDeleted={onDeleted} />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function AgentRowView({ run, depth, hasChildren, expanded, workers, labelOf, onOpen, onFold, onDeleted }: {
  run: RunNode; depth: number; hasChildren: boolean; expanded: boolean; workers: number;
  labelOf: (id: string) => string; onOpen: () => void; onFold: () => void; onDeleted: () => void;
}): JSX.Element {
  const act = activityByRun.value.get(run.id) ?? null;
  const waiting = act?.state === "waiting_approval" || act?.state === "waiting_user";
  const tone: LedTone = waiting ? "blue" : TONE[run.status];
  const title = agentRowTitle(run);
  const isLead = run.role === "lead";
  return (
    <li class={`ap-row${isLead ? " ap-lead" : ""}`} data-agent-row={run.id} data-role={run.role ?? undefined}
      style={{ "--depth": String(depth) } as JSX.CSSProperties}>
      <div class="ap-line">
        {hasChildren
          ? <button type="button" class="ap-fold" aria-label={`${expanded ? "Hide" : "Show"} ${workers} sub-agent${workers === 1 ? "" : "s"}`} onClick={onFold}>{expanded ? "▾" : "▸"}</button>
          : <span class="ap-fold" aria-hidden="true">{depth > 0 ? "└" : ""}</span>}
        <span class="ap-dot" data-activity={act?.state ?? undefined}><Led tone={tone} title={act ? activityLabel(act.state) : run.status} /></span>
        <button type="button" class="ap-title text" aria-label={`Open ${title}`} title="Open this conversation" onClick={onOpen} data-open>
          {isLead && <span class="ap-badge" data-lead>lead</span>}
          {title}
        </button>
        <span class="ap-agent faint text">{run.agent}</span>
        <span class="am-spacer" />
        <DeleteButton run={run} labelOf={labelOf} onDeleted={onDeleted} />
      </div>
      <div class="ap-sub">
        <span class={`ap-act text${waiting ? " needs-you" : ""}`} data-row-activity>
          {act ? `${activityLabel(act.state)}${act.detail ? ` · ${act.detail}` : ""}` : run.status}
        </span>
        <span class="faint">{costText(run)}</span>
        <span class="faint">{agoText(lastActive(run))}</span>
      </div>
    </li>
  );
}

/** Two-step delete. A run that is still working (or awaiting approval) is kept and says why,
 *  in plain words — no forced stop here (the full Runs view still offers Cancel and delete). */
function DeleteButton({ run, labelOf, onDeleted }: { run: RunNode; labelOf: (id: string) => string; onDeleted: () => void }): JSX.Element {
  const [armed, setArmed] = useState(false);
  const [refusal, setRefusal] = useState<DeleteRefused | null>(null);
  const [error, setError] = useState<string | null>(null);

  const remove = async () => {
    setArmed(false);
    setError(null);
    try {
      const refused = await actions.client().deleteRun(run.id);
      if (refused) { setRefusal(refused); return; }
      setRefusal(null);
      actions.forgetRuns([run.id]);
      onDeleted();
    } catch (e) { setError(message(e)); }
  };

  return (
    <span class="ap-del">
      <Button variant={armed ? "danger" : "ghost"} aria-label={armed ? `Confirm delete ${run.agent}` : `Delete ${run.agent}`}
        onClick={() => { if (armed) { void remove(); } else setArmed(true); }} onBlur={() => setArmed(false)}>
        {armed ? "Confirm delete" : "Delete"}
      </Button>
      {refusal && <span class="ap-refused tone-red text" role="alert">{refusalText(refusal, labelOf)}</span>}
      {error && <span class="ap-refused tone-red text" role="alert">{error}</span>}
    </span>
  );
}
