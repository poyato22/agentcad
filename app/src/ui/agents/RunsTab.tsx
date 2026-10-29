// Runs tab of the agent manager: every run as the orchestrator → worker tree, grouped by build.
// Rows show status, live activity, model, cost, last activity and title; rename, pin, archive and
// delete per row; multi-select bulk delete; and a "Clean up" panel that always shows a dry-run
// count before anything is removed. Deleting never touches a running run or one awaiting approval:
// the core refuses (409) and the row says why in plain words, offering "Cancel and delete".
import type { JSX } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { onCoreEvent } from "../../api/events";
import type { AgentRun, CleanupResult, DeleteRefused, RunNode } from "../../api/types";
import { activityByRun } from "../../state/activity";
import { actions } from "../../state/store";
import { Button } from "../common/Button";
import { Led, type LedTone } from "../common/Led";
import {
  agoText, closeAgentManager, costText, flattenTree, groupByBuild, lastActive, refusalText, runTitle, subtreeIds, visibleRoots,
} from "./manager";
import { activityLabel } from "./activityLabel";

const TONE: Record<AgentRun["status"], LedTone> = { queued: "faint", running: "blue", done: "green", failed: "red", cancelled: "faint" };
const FINISHED = new Set<AgentRun["status"]>(["done", "failed", "cancelled"]);
/** Refetch the tree at most this often while runs start and finish. */
const RELOAD_MS = 300;

function message(e: unknown): string { return e instanceof Error ? e.message : String(e); }

export function RunsTab(): JSX.Element {
  const [tree, setTree] = useState<RunNode[] | null>(null);
  const [names, setNames] = useState<ReadonlyMap<string, string>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [refusals, setRefusals] = useState<ReadonlyMap<string, DeleteRefused>>(new Map());
  const [cleanupOpen, setCleanupOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = async () => {
    try {
      const client = actions.client();
      const [t, n] = await Promise.all([client.runsTree(), client.projectNames().catch(() => new Map<string, string>())]);
      setTree(t);
      setNames(n);
      setError(null);
    } catch (e) {
      setError(message(e));
    }
  };

  useEffect(() => {
    void load();
    const off = onCoreEvent((ev) => {
      if (ev.type !== "run_started" && ev.type !== "run_finished" && ev.type !== "run_queued") return;
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => { void load(); }, RELOAD_MS);
    });
    return () => { off(); if (timer.current) clearTimeout(timer.current); };
  }, []);

  const byId = useMemo(() => {
    const m = new Map<string, RunNode>();
    const walk = (ns: readonly RunNode[]) => { for (const n of ns) { m.set(n.id, n); walk(n.children); } };
    walk(tree ?? []);
    return m;
  }, [tree]);
  const labelOf = (id: string) => { const r = byId.get(id); return r ? `${r.agent} · ${runTitle(r)}` : id; };

  const groups = groupByBuild(visibleRoots(tree ?? [], showArchived), names);

  const toggleSel = (id: string) => setSelected((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const toggleFold = (id: string) => setCollapsed((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const setRefusal = (id: string, r: DeleteRefused | null) => setRefusals((m) => { const n = new Map(m); if (r) n.set(id, r); else n.delete(id); return n; });

  /** Deletes one run; a refusal is kept on its row. Returns true when it was deleted. */
  const deleteOne = async (id: string): Promise<boolean> => {
    const refused = await actions.client().deleteRun(id);
    if (refused) { setRefusal(id, refused); return false; }
    setRefusal(id, null);
    setSelected((s) => { if (!s.has(id)) return s; const n = new Set(s); n.delete(id); return n; });
    return true;
  };

  const onDelete = async (id: string) => {
    setNotice(null);
    try {
      if (await deleteOne(id)) { setNotice(`Deleted ${labelOf(id)}.`); await load(); }
    } catch (e) { setError(message(e)); }
  };

  /** Cancel the run and its blocking workers (pending changes are rejected, never applied), then
   *  delete once the core no longer sees them as busy. */
  const onCancelAndDelete = async (id: string, refused: DeleteRefused) => {
    setNotice(null);
    const client = actions.client();
    try {
      for (const rid of [...refused.children, id]) {
        const r = byId.get(rid);
        if (!r || !FINISHED.has(r.status) || refused.reason === "pending_approval") await client.cancel(rid).catch(() => undefined);
      }
      for (let i = 0; i < 20; i++) {
        const again = await client.deleteRun(id);
        if (!again) { setRefusal(id, null); setNotice(`Cancelled and deleted ${labelOf(id)}.`); await load(); return; }
        setRefusal(id, again);
        await new Promise((r) => setTimeout(r, 250));
      }
      setError("The run is still stopping. Try Delete again in a moment.");
    } catch (e) { setError(message(e)); }
  };

  const onBulkDelete = async () => {
    setNotice(null);
    let done = 0;
    let refused = 0;
    try {
      for (const id of selected) { if (await deleteOne(id)) done++; else refused++; }
    } catch (e) { setError(message(e)); }
    setNotice(refused
      ? `Deleted ${done}. ${refused} couldn't be deleted yet — the reason is shown on ${refused === 1 ? "its row" : "each row"}.`
      : `Deleted ${done} run${done === 1 ? "" : "s"}.`);
    await load();
  };

  const patch = async (id: string, p: { title?: string; pinned?: boolean; archived?: boolean }) => {
    try { await actions.client().patchRun(id, p); await load(); } catch (e) { setError(message(e)); }
  };

  return (
    <div class="am-runs" data-runs-tab>
      <div class="am-toolbar">
        <label class="am-check faint">
          <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived((e.currentTarget as HTMLInputElement).checked)} />
          Show archived
        </label>
        <span class="am-spacer" />
        {selected.size > 0 && (
          <ConfirmButton label={`Delete ${selected.size} selected`} confirm={`Delete ${selected.size} run${selected.size === 1 ? "" : "s"}?`}
            onConfirm={() => { void onBulkDelete(); }} />
        )}
        <Button onClick={() => setCleanupOpen(!cleanupOpen)} aria-expanded={cleanupOpen}>Clean up…</Button>
      </div>
      {cleanupOpen && <CleanupPanel onDone={(r) => { setNotice(`Deleted ${r.count} finished run${r.count === 1 ? "" : "s"}.`); setCleanupOpen(false); void load(); }} />}
      {notice && <div class="am-notice text" role="status">{notice}</div>}
      {error && <div class="am-error tone-red text" role="alert">{error}</div>}
      {tree === null && !error && <div class="am-empty faint">Loading runs…</div>}
      {tree !== null && groups.length === 0 && <div class="am-empty faint">No runs yet. Start one with New run (⌘N).</div>}
      {groups.map((g) => (
        <section key={g.projectId ?? "none"} class="am-group" aria-label={`Build ${g.name}`}>
          <h3 class="am-group-h">{g.name}</h3>
          <ul class="am-tree" role="tree" aria-label={`Runs in ${g.name}`}>
            {flattenTree(g.roots, collapsed).map((row) => (
              <RunRow key={row.run.id} row={row} selected={selected.has(row.run.id)} refusal={refusals.get(row.run.id) ?? null}
                labelOf={labelOf}
                onSelect={() => toggleSel(row.run.id)} onFold={() => toggleFold(row.run.id)}
                onOpen={() => { actions.selectRun(row.run.id); closeAgentManager(); }}
                onRename={(t) => { void patch(row.run.id, { title: t }); }}
                onPin={() => { void patch(row.run.id, { pinned: !row.run.pinned }); }}
                onArchive={() => { void patch(row.run.id, { archived: !row.run.archived }); }}
                onDelete={() => { void onDelete(row.run.id); }}
                onCancelAndDelete={(r) => { void onCancelAndDelete(row.run.id, r); }}
                workers={subtreeIds(row.run).length - 1} />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

interface RowProps {
  row: ReturnType<typeof flattenTree>[number];
  selected: boolean;
  refusal: DeleteRefused | null;
  workers: number;
  labelOf: (id: string) => string;
  onSelect: () => void; onFold: () => void; onOpen: () => void;
  onRename: (title: string) => void; onPin: () => void; onArchive: () => void;
  onDelete: () => void; onCancelAndDelete: (r: DeleteRefused) => void;
}

function RunRow(p: RowProps): JSX.Element {
  const r = p.row.run;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const act = activityByRun.value.get(r.id) ?? null;
  const waiting = act?.state === "waiting_approval" || act?.state === "waiting_user";
  const tone: LedTone = waiting ? "blue" : TONE[r.status];
  const title = runTitle(r);

  const commit = () => {
    setEditing(false);
    const t = draft.trim();
    if (t && t !== title) p.onRename(t);
  };

  return (
    <li role="treeitem" aria-level={p.row.depth + 1} aria-expanded={p.row.hasChildren ? p.row.expanded : undefined}
      aria-selected={p.selected} class={`am-row${p.selected ? " sel" : ""}`} data-run-row={r.id}
      style={{ "--depth": String(p.row.depth) } as JSX.CSSProperties}>
      <div class="am-line">
        <input type="checkbox" class="am-sel" checked={p.selected} aria-label={`Select ${title}`} onChange={p.onSelect} />
        {p.row.hasChildren
          ? <button type="button" class="am-fold" aria-label={`${p.row.expanded ? "Hide" : "Show"} ${p.workers} worker${p.workers === 1 ? "" : "s"}`} onClick={p.onFold}>{p.row.expanded ? "▾" : "▸"}</button>
          : <span class="am-fold" aria-hidden="true">{p.row.depth > 0 ? "└" : ""}</span>}
        <span class="am-dot" data-activity={act?.state ?? undefined}><Led tone={tone} title={act ? activityLabel(act.state) : r.status} /></span>
        {editing
          ? (
            <input type="text" class="am-rename" aria-label="Run title" value={draft} ref={(el) => el?.focus()}
              onInput={(e) => setDraft((e.currentTarget as HTMLInputElement).value)}
              onBlur={commit}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setEditing(false); } }} />
          )
          : <button type="button" class="am-title text" title="Show this run" onClick={p.onOpen}>{r.pinned && <span class="am-pin" aria-label="pinned">⚲ </span>}{title}</button>}
        <span class="am-meta faint text">{`${r.agent} · ${r.model}`}</span>
      </div>
      <div class="am-sub">
        <span class={`am-act text${waiting ? " needs-you" : ""}`} data-row-activity>
          {act ? `${activityLabel(act.state)}${act.detail ? ` · ${act.detail}` : ""}` : r.status}
        </span>
        <span class="faint">{costText(r)}</span>
        <span class="faint">{agoText(lastActive(r))}</span>
        {r.archived && <span class="faint">archived</span>}
        <span class="am-spacer" />
        <span class="am-actions">
          <Button variant="ghost" onClick={() => { setDraft(title); setEditing(true); }}>Rename</Button>
          <Button variant="ghost" aria-pressed={!!r.pinned} onClick={p.onPin}>{r.pinned ? "Unpin" : "Pin"}</Button>
          <Button variant="ghost" onClick={p.onArchive}>{r.archived ? "Unarchive" : "Archive"}</Button>
          <ConfirmButton label="Delete" confirm="Confirm delete" variant="danger" onConfirm={p.onDelete} />
        </span>
      </div>
      {p.refusal && (
        <div class="am-refused" role="alert" data-refusal={p.refusal.reason}>
          <span class="text">{refusalText(p.refusal, p.labelOf)}</span>
          <ConfirmButton label="Cancel and delete" confirm="Stop it and delete?" variant="danger" onConfirm={() => p.onCancelAndDelete(p.refusal as DeleteRefused)} />
        </div>
      )}
    </li>
  );
}

/** A two-step button: the first press arms it (label changes), the second confirms. Blur or
 *  Escape disarms. Works the same by keyboard. */
function ConfirmButton({ label, confirm, onConfirm, variant = "default" }: {
  label: string; confirm: string; onConfirm: () => void; variant?: "default" | "danger";
}): JSX.Element {
  const [armed, setArmed] = useState(false);
  return (
    <Button variant={armed ? "danger" : variant === "danger" ? "ghost" : "default"} class={armed ? "am-armed" : undefined}
      onClick={() => { if (armed) { setArmed(false); onConfirm(); } else setArmed(true); }}
      onBlur={() => setArmed(false)}
      onKeyDown={(e: KeyboardEvent) => { if (e.key === "Escape" && armed) { e.stopPropagation(); setArmed(false); } }}>
      {armed ? confirm : label}
    </Button>
  );
}

const AGES: { label: string; days: number | null }[] = [
  { label: "any age", days: null }, { label: "older than 1 day", days: 1 }, { label: "older than 7 days", days: 7 }, { label: "older than 30 days", days: 30 },
];
const STATUSES = ["done", "failed", "cancelled"] as const;

/** Clean up: pick age and statuses, see the dry-run count, then delete with the same filters (the
 *  core re-checks each run, so one that became busy since the check is kept). */
function CleanupPanel({ onDone }: { onDone: (r: CleanupResult) => void }): JSX.Element {
  const [days, setDays] = useState<number | null>(7);
  const [statuses, setStatuses] = useState<ReadonlySet<string>>(new Set(STATUSES));
  const [preview, setPreview] = useState<CleanupResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const body = { older_than_days: days, status_in: [...statuses] };

  const check = async () => {
    setBusy(true); setError(null);
    try { setPreview(await actions.client().cleanupRuns({ ...body, dry_run: true })); } catch (e) { setError(message(e)); }
    setBusy(false);
  };
  // any change to the filters invalidates the count: it must be checked again before deleting
  useEffect(() => { setPreview(null); }, [days, [...statuses].sort().join(",")]);

  const run = async () => {
    setBusy(true); setError(null);
    try { onDone(await actions.client().cleanupRuns({ ...body, dry_run: false })); } catch (e) { setError(message(e)); }
    setBusy(false);
  };

  return (
    <div class="am-cleanup" role="group" aria-label="Clean up finished runs" data-cleanup>
      <p class="faint text">Removes finished runs and their logs from this Mac. Running runs and runs awaiting your approval are always kept. Onshape is never touched.</p>
      <div class="am-cleanup-row">
        <label>
          <span class="faint">Age </span>
          <select aria-label="Age" value={String(days)} onChange={(e) => { const v = (e.currentTarget as HTMLSelectElement).value; setDays(v === "null" ? null : Number(v)); }}>
            {AGES.map((a) => <option key={a.label} value={String(a.days)}>{a.label}</option>)}
          </select>
        </label>
        {STATUSES.map((s) => (
          <label key={s} class="am-check">
            <input type="checkbox" checked={statuses.has(s)}
              onChange={() => setStatuses((cur) => { const n = new Set(cur); if (n.has(s)) n.delete(s); else n.add(s); return n; })} />
            {s}
          </label>
        ))}
        <Button disabled={busy || statuses.size === 0} onClick={() => { void check(); }}>Check</Button>
      </div>
      {preview && (
        <div class="am-cleanup-row" data-cleanup-preview={preview.count}>
          <span class="text">{preview.count === 0 ? "Nothing to clean up." : `${preview.count} run${preview.count === 1 ? "" : "s"} would be deleted.`}</span>
          {preview.count > 0 && (
            <ConfirmButton label={`Delete ${preview.count} run${preview.count === 1 ? "" : "s"}`} confirm={`Really delete ${preview.count}?`} variant="danger"
              onConfirm={() => { void run(); }} />
          )}
        </div>
      )}
      {error && <div class="am-error tone-red text" role="alert">{error}</div>}
    </div>
  );
}
