// The Agents panel (R10): one easy view of every build's agents. Each build shows its long-lived
// lead on top with the sub-agents it spawned indented beneath (task title, status dot, live
// activity, messages waiting), then the build's other runs. A row opens its conversation; its ⋯
// menu renames, pins, archives or deletes it. "Clean up finished…" removes old finished runs
// after a dry-run list. A build whose agents can't be read says so instead of looking empty.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { onCoreEvent } from "../../api/events";
import type { AgentRun, AgentsView, DeleteRefused } from "../../api/types";
import { activityByRun, parseSnapshot } from "../../state/activity";
import { actions } from "../../state/store";
import { Button } from "../common/Button";
import { Led, type LedTone } from "../common/Led";
import { OverflowMenu } from "../common/OverflowMenu";
import { activityLabel } from "./activityLabel";
import { agoText, closeAgentManager, costText, lastActive, refusalText, REJECTED_NOT_APPLIED } from "./manager";
import { agentRowTitle, allNodes, viewRows, waitingText, type AgentRow } from "./panelModel";
import { CleanupPanel } from "./RunsTab";

const TONE: Record<AgentRun["status"], LedTone> = { queued: "faint", running: "blue", done: "green", failed: "red", cancelled: "faint" };
/** Refetch at most this often as runs start and finish. */
const RELOAD_MS = 300;
/** Cancel and delete: retry the delete this many times, this far apart, while the run stops
 *  (the core's worst-case stop is ~7 s, C-8). */
const STOP_TRIES = 30;
const STOP_POLL_MS = 300;
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

function message(e: unknown): string { return e instanceof Error ? e.message : String(e); }

interface BuildAgents { id: string; name: string; view: AgentsView | null; error: string | null }
const RELOAD_EVENTS = new Set(["run_started", "run_finished", "run_queued", "run_deleted", "message_queued", "message_delivered", "message_dropped"]);

export function AgentsPanel(): JSX.Element {
  const [builds, setBuilds] = useState<BuildAgents[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const [cleanupOpen, setCleanupOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** Which build each run belongs to, so an event reloads only its build (M3). */
  const buildOf = useRef(new Map<string, string>());
  const pendingBuilds = useRef<Set<string> | "all">(new Set());

  const fetchBuild = async (id: string, name: string): Promise<BuildAgents> => {
    try { return { id, name, view: await actions.client().projectAgents(id), error: null }; } catch (e) { return { id, name, view: null, error: message(e) }; }
  };

  const load = async (only?: ReadonlySet<string>) => {
    try {
      const names = await actions.client().projectNames();
      const entries = [...names.entries()].filter(([id]) => !only || only.has(id));
      const fresh = await Promise.all(entries.map(([id, name]) => fetchBuild(id, name)));
      setBuilds((cur) => {
        const keep = only && cur ? cur.filter((b) => !only.has(b.id) && names.has(b.id)) : [];
        const next = [...keep, ...fresh].sort((a, b) => a.name.localeCompare(b.name));
        const m = new Map<string, string>();
        for (const b of next) for (const n of b.view ? allNodes(b.view) : []) m.set(n.id, b.id);
        buildOf.current = m;
        return next;
      });
      setError(null);
    } catch (e) { setError(message(e)); }
  };

  useEffect(() => {
    void load();
    const off = onCoreEvent((ev) => {
      if (!RELOAD_EVENTS.has(ev.type)) return;
      const b = ev.run_id ? buildOf.current.get(ev.run_id) : undefined;
      // a run we don't know yet (a new lead) may be in any build: reload them all
      if (!b || pendingBuilds.current === "all") pendingBuilds.current = "all";
      else pendingBuilds.current.add(b);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        const p = pendingBuilds.current;
        pendingBuilds.current = new Set();
        void load(p === "all" ? undefined : p);
      }, RELOAD_MS);
    });
    return () => { off(); if (timer.current) clearTimeout(timer.current); };
  }, []);

  const labelOf = (id: string) => {
    for (const b of builds ?? []) for (const n of b.view ? allNodes(b.view) : []) if (n.id === id) return `${n.agent} · ${agentRowTitle(n)}`;
    return id;
  };
  const setFold = (id: string, closed: boolean) => setCollapsed((s) => { const n = new Set(s); if (closed) n.add(id); else n.delete(id); return n; });
  const reload = () => { void load(); };
  const shown = (builds ?? []).filter((b) => b.error !== null || (b.view !== null && (b.view.lead !== null || b.view.others.some((o) => !o.archived))));

  const rowList = (rows: AgentRow[], label: string) => (
    <ul class="ap-list" aria-label={label}>
      {rows.map((row) => (
        <AgentRowView key={row.node.id} row={row} labelOf={labelOf} onFold={() => setFold(row.node.id, row.expanded)}
          onChanged={reload} onNotice={setNotice} />
      ))}
    </ul>
  );

  return (
    <div class="am-agents-view" data-agents-view>
      {error && <div class="am-error tone-red text" role="alert">{`Couldn't read the builds: ${error}`}</div>}
      {notice && <div class="am-notice text" role="status">{notice}</div>}
      {builds === null && !error && <div class="am-empty faint">Loading agents…</div>}
      {builds !== null && shown.length === 0 && !error && <div class="am-empty faint">No agents yet. Message the build's agent in the dock to start one.</div>}
      {shown.map((b) => {
        const rows = b.view ? viewRows(b.view, collapsed) : { lead: [], others: [], earlier: [] };
        return (
          <section key={b.id} class="am-group" aria-label={`Agents in ${b.name}`} data-build={b.id}>
            <h3 class="am-group-h">{b.name}</h3>
            {b.error && <div class="am-error tone-red text" role="alert" data-build-error>{`Couldn't read this build's agents: ${b.error}`}</div>}
            {rows.lead.length > 0 && rowList(rows.lead, `Lead agent of ${b.name}`)}
            {rows.earlier.length > 0 && (
              <>
                <h4 class="ap-others-h faint">Earlier leads of this build</h4>
                {rowList(rows.earlier, `Earlier leads of ${b.name}`)}
              </>
            )}
            {rows.others.length > 0 && (
              <>
                <h4 class="ap-others-h faint">Other agents in this build</h4>
                {rowList(rows.others, `Other agents in ${b.name}`)}
              </>
            )}
          </section>
        );
      })}
      <div class="ap-foot">
        <Button onClick={() => setCleanupOpen(!cleanupOpen)} aria-expanded={cleanupOpen}>Clean up finished…</Button>
      </div>
      {cleanupOpen && (
        <CleanupPanel labelOf={labelOf} refusalOf={(r) => refusalText(r, labelOf)}
          onDeleted={(summary) => { setNotice(summary); reload(); }} />
      )}
    </div>
  );
}

function AgentRowView({ row, labelOf, onFold, onChanged, onNotice }: {
  row: AgentRow; labelOf: (id: string) => string; onFold: () => void; onChanged: () => void; onNotice: (s: string) => void;
}): JSX.Element {
  const n = row.node;
  const act = activityByRun.value.get(n.id) ?? parseSnapshot(n.id, n.activity);
  const live = n.status === "running" || n.status === "queued";
  const shownAct = act && (live || act.state === "idle") ? act : null;
  const waiting = shownAct?.state === "waiting_approval" || shownAct?.state === "waiting_user";
  const tone: LedTone = waiting ? "blue" : TONE[n.status];
  const title = agentRowTitle(n);
  const isLead = n.role === "lead";
  const queued = waitingText(n);
  const [mode, setMode] = useState<"idle" | "rename" | "delete">("idle");
  const [draft, setDraft] = useState("");
  const [refusal, setRefusal] = useState<DeleteRefused | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const cancelled = useRef(false);

  const patch = async (p: { title?: string; pinned?: boolean; archived?: boolean }) => {
    setError(null);
    try { actions.upsertRun(await actions.client().patchRun(n.id, p)); onChanged(); } catch (e) { setError(message(e)); }
  };
  const commitRename = () => {
    if (cancelled.current) return;
    setMode("idle");
    const t = draft.trim();
    if (t && t !== title) void patch({ title: t });
  };
  const remove = async () => {
    setBusy(true); setError(null);
    try {
      const refused = await actions.client().deleteRun(n.id);
      if (refused) { setRefusal(refused); setBusy(false); return; }
      actions.forgetRuns([n.id]);
      onNotice(`Deleted ${labelOf(n.id)}.`);
      setMode("idle");
      onChanged();
    } catch (e) { setError(message(e)); }
    setBusy(false);
  };
  /** Stop the run and every run blocking it (cancelling only ever rejects a pending change),
   *  then delete once the core no longer sees them busy. */
  const cancelAndDelete = async (r: DeleteRefused) => {
    setBusy(true); setError(null);
    const client = actions.client();
    try {
      for (const id of [...r.children, n.id]) await client.cancel(id).catch(() => undefined);
      for (let i = 0; i < STOP_TRIES; i++) {
        const again = await client.deleteRun(n.id);
        if (!again) { setRefusal(null); actions.forgetRuns([n.id]); onNotice(`Cancelled and deleted ${labelOf(n.id)}.`); setMode("idle"); setBusy(false); onChanged(); return; }
        setRefusal(again);
        if (again.reason === "pending_approval") { setError(`A change is still waiting for your approval. Decide its card first — ${REJECTED_NOT_APPLIED}.`); setBusy(false); return; }
        await sleep(STOP_POLL_MS);
      }
      setError("The run is still stopping. Try Delete again in a moment.");
    } catch (e) { setError(message(e)); }
    setBusy(false);
  };

  return (
    <li class={`ap-row${isLead ? " ap-lead" : ""}${n.archived ? " ap-archived" : ""}`} data-agent-row={n.id} data-role={n.role ?? undefined}
      style={{ "--depth": String(row.depth) } as JSX.CSSProperties}>
      <div class="ap-line">
        {row.hasChildren
          ? <button type="button" class="ap-fold" aria-expanded={row.expanded} aria-label={`${row.expanded ? "Hide" : "Show"} the sub-agents of ${title}`} onClick={onFold}>{row.expanded ? "▾" : "▸"}</button>
          : <span class="ap-fold" aria-hidden="true">{row.depth > 0 ? "└" : ""}</span>}
        <span class="ap-dot" data-activity={shownAct?.state ?? undefined}><Led tone={tone} title={shownAct ? activityLabel(shownAct.state) : n.status} /></span>
        {mode === "rename"
          ? (
            <input type="text" class="am-rename" aria-label="Run title" value={draft} ref={(el) => el?.focus()}
              onInput={(e) => setDraft((e.currentTarget as HTMLInputElement).value)} onBlur={commitRename}
              onKeyDown={(e) => {
                e.stopPropagation();
                if (e.key === "Enter") { e.preventDefault(); commitRename(); }
                else if (e.key === "Escape") { e.preventDefault(); cancelled.current = true; setMode("idle"); }
              }} />
          )
          : (
            <button type="button" class="ap-title text" aria-label={`Open ${title}`} title="Open this conversation" data-open
              onClick={() => { actions.selectRun(n.id); closeAgentManager(); }}>
              {isLead && <span class="ap-badge" data-lead>lead</span>}
              {n.pinned && <span class="am-pin" aria-label="pinned">⚲ </span>}
              {title}
            </button>
          )}
        <span class="ap-agent faint text">{n.agent}</span>
        <span class="am-spacer" />
        <OverflowMenu label={`More for ${title}`} items={[
          { label: "Rename…", onSelect: () => { cancelled.current = false; setDraft(title); setMode("rename"); } },
          { label: n.pinned ? "Unpin" : "Pin", onSelect: () => { void patch({ pinned: !n.pinned }); } },
          { label: n.archived ? "Unarchive" : "Archive", onSelect: () => { void patch({ archived: !n.archived }); } },
          { label: "Delete…", onSelect: () => { setRefusal(null); setError(null); setMode("delete"); } },
        ]} />
      </div>
      <div class="ap-sub">
        <span class={`ap-act text${waiting ? " needs-you" : ""}`} data-row-activity>
          {shownAct ? `${activityLabel(shownAct.state)}${shownAct.detail ? ` · ${shownAct.detail}` : ""}` : n.status}
        </span>
        {queued && <span class="ap-waiting" data-waiting>{queued}</span>}
        <span class="faint">{costText(n)}</span>
        <span class="faint">{agoText(lastActive(n))}</span>
        {n.archived && <span class="faint">archived</span>}
      </div>
      {mode === "delete" && (
        <div class="ap-confirm" role="group" aria-label={`Delete ${title}`} data-delete-confirm>
          {refusal
            ? (
              <span class="ap-refused text" role="alert" data-refusal={refusal.reason}>
                <span>{refusalText(refusal, labelOf)}</span>
                <Button variant="danger" disabled={busy} onClick={() => { void cancelAndDelete(refusal); }}>Cancel and delete</Button>
                <Button variant="ghost" onClick={() => { setMode("idle"); setRefusal(null); }}>Keep it</Button>
              </span>
            )
            : (
              <>
                <span class="text">{`Delete ${title} and its log from this Mac?`}</span>
                <Button variant="danger" disabled={busy} onClick={() => { void remove(); }}>{`Delete ${title}`}</Button>
                <Button variant="ghost" onClick={() => setMode("idle")}>Keep it</Button>
              </>
            )}
        </div>
      )}
      {error && <div class="ap-refused tone-red text" role="alert">{error}</div>}
    </li>
  );
}
