// Runs tab of the agent manager: every run as the orchestrator → worker tree, grouped by build.
// Rows show status, live activity, model, cost, last activity and title; rename, pin, archive and
// delete per row; multi-select bulk delete; and a "Clean up" panel that always shows a dry-run
// list before anything is removed, then deletes exactly those runs. Deleting never touches a
// running run or one awaiting approval: the core refuses (409) and the row says why in plain
// words, offering "Cancel and delete" (cancelling only ever rejects a pending change).
// Keyboard: the tree uses a roving tabindex — ↑/↓ move between rows, → opens / goes to the first
// worker, ← closes / goes to the parent, Home/End, Enter shows the run; Tab reaches the row's
// buttons.
import type { JSX } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { CoreError } from "../../api/client";
import { onCoreEvent } from "../../api/events";
import type { AgentRun, CleanupResult, DeleteRefused, RunNode } from "../../api/types";
import { activityByRun } from "../../state/activity";
import { actions, approvals } from "../../state/store";
import { Button } from "../common/Button";
import { Led, type LedTone } from "../common/Led";
import { activityLabel } from "./activityLabel";
import {
  agoText, closeAgentManager, costText, flattenTree, groupByBuild, lastActive, refusalText, REJECTED_NOT_APPLIED, runTitle,
  subtreeIds, type TreeRow, visibleRoots,
} from "./manager";

const TONE: Record<AgentRun["status"], LedTone> = { queued: "faint", running: "blue", done: "green", failed: "red", cancelled: "faint" };
/** Refetch the tree at most this often while runs start and finish. */
const RELOAD_MS = 300;
/** Cancel and delete: first wait (up to STOP_WAIT_MS) for the cancelled runs to reach a final
 *  state — their run_finished, or GET /runs/{id} polled STOP_POLL_MS apart — since the core's
 *  worst-case stop is ~7 s (2 s close + 5 s TERM grace, C-8); then retry the delete while the core
 *  still winds the session down (250 ms apart, ~5 s more). */
export const STOP_WAIT_MS = 12_000;
const STOP_POLL_MS = 250;
const STOP_TRIES = 20;
const TERMINAL: ReadonlySet<string> = new Set(["done", "failed", "cancelled"]);

/** Resolves once every run is final (or gone), or after `ms`; true when they all stopped. */
export async function waitStopped(ids: readonly string[], getStatus: (id: string) => Promise<string | null>,
  opts: { ms?: number; pollMs?: number; subscribe?: typeof onCoreEvent } = {}): Promise<boolean> {
  const left = new Set(ids);
  const deadline = Date.now() + (opts.ms ?? STOP_WAIT_MS);
  let wake: (() => void) | null = null;
  const off = (opts.subscribe ?? onCoreEvent)((ev) => {
    if (ev.type === "run_finished" && ev.run_id && left.delete(ev.run_id)) wake?.();
  });
  try {
    while (left.size > 0 && Date.now() < deadline) {
      for (const id of [...left]) {
        const st = await getStatus(id).catch(() => "unknown");
        if (st === null || TERMINAL.has(st)) left.delete(id);
      }
      if (left.size === 0) break;
      await new Promise<void>((r) => { wake = r; setTimeout(r, opts.pollMs ?? STOP_POLL_MS); });
      wake = null;
    }
    return left.size === 0;
  } finally { off(); }
}

/** A run's status, or null when the core no longer has it (404). */
async function runStatus(id: string): Promise<string | null> {
  try { return (await actions.client().getRun(id)).status; } catch (e) {
    if (e instanceof CoreError && e.code === "not_found") return null;
    throw e;
  }
}
/** …but a blocker that is only an approval stops the retries after this many (cancel didn't clear it). */
const APPROVAL_TRIES = 4;

function message(e: unknown): string { return e instanceof Error ? e.message : String(e); }
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Does this run wait for the human's approval (a pending card in the store, or its activity)? */
function awaitingApproval(id: string): boolean {
  if (activityByRun.value.get(id)?.state === "waiting_approval") return true;
  for (const a of approvals.value.values()) if (a.run_id === id && a.status === "pending") return true;
  return false;
}

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
  /** The tree row that holds the roving tab stop. */
  const [focusId, setFocusId] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const root = useRef<HTMLDivElement>(null);

  const load = async () => {
    try {
      const client = actions.client();
      const [t, n] = await Promise.all([client.runsTree(), client.projectNames().catch(() => new Map<string, string>())]);
      setTree(t);
      setNames(n);
      // a refusal says "still working": once the run (and its workers) stopped, it is stale (C-8)
      const busy = new Set<string>();
      const walk = (ns: readonly RunNode[]) => { for (const x of ns) { if (x.status === "running" || x.status === "queued") busy.add(x.id); walk(x.children); } };
      walk(t);
      setRefusals((m) => {
        const keep = [...m].filter(([id, r]) => busy.has(id) || r.reason === "pending_approval" || r.children.some((c) => busy.has(c)));
        return keep.length === m.size ? m : new Map(keep);
      });
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
  const refusalOf = (r: DeleteRefused) => refusalText(r, labelOf, awaitingApproval);

  const groups = groupByBuild(visibleRoots(tree ?? [], showArchived), names);
  const rowsByGroup = groups.map((g) => flattenTree(g.roots, collapsed));
  const allRows = rowsByGroup.flat();
  const tabStop = allRows.some((r) => r.run.id === focusId) ? focusId : allRows[0]?.run.id ?? null;

  const toggleSel = (id: string) => setSelected((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const setFold = (id: string, closed: boolean) => setCollapsed((s) => { const n = new Set(s); if (closed) n.add(id); else n.delete(id); return n; });
  const setRefusal = (id: string, r: DeleteRefused | null) => setRefusals((m) => { const n = new Map(m); if (r) n.set(id, r); else n.delete(id); return n; });
  const focusRow = (id: string) => {
    setFocusId(id);
    // after the re-render that moves the tab stop
    queueMicrotask(() => root.current?.querySelector<HTMLElement>(`[data-run-row="${CSS.escape(id)}"]`)?.focus());
  };

  const onTreeKey = (e: KeyboardEvent, row: TreeRow) => {
    if (e.target !== e.currentTarget) return;           // keys inside the row's own controls
    const i = allRows.findIndex((r) => r.run.id === row.run.id);
    const parentId = row.run.parent_run_id ?? row.run.parent_id;
    let to: string | null = null;
    switch (e.key) {
      case "ArrowDown": to = allRows[i + 1]?.run.id ?? null; break;
      case "ArrowUp": to = allRows[i - 1]?.run.id ?? null; break;
      case "Home": to = allRows[0]?.run.id ?? null; break;
      case "End": to = allRows[allRows.length - 1]?.run.id ?? null; break;
      case "ArrowRight":
        if (row.hasChildren && !row.expanded) setFold(row.run.id, false);
        else if (row.expanded) to = allRows[i + 1]?.run.id ?? null;
        break;
      case "ArrowLeft":
        if (row.expanded) setFold(row.run.id, true);
        else if (parentId && byId.has(parentId)) to = parentId;
        break;
      case "Enter": actions.selectRun(row.run.id); closeAgentManager(); break;
      case " ": toggleSel(row.run.id); break;
      default: return;
    }
    e.preventDefault();
    if (to) focusRow(to);
  };

  /** Deletes one run; a refusal is kept on its row. Returns true when it was deleted. */
  const deleteOne = async (id: string): Promise<boolean> => {
    const refused = await actions.client().deleteRun(id);
    if (refused) { setRefusal(id, refused); return false; }
    setRefusal(id, null);
    actions.forgetRuns([id]);
    setSelected((s) => { if (!s.has(id)) return s; const n = new Set(s); n.delete(id); return n; });
    return true;
  };

  const onDelete = async (id: string) => {
    setNotice(null);
    try {
      if (await deleteOne(id)) { setNotice(`Deleted ${labelOf(id)}.`); await load(); }
    } catch (e) { setError(message(e)); }
  };

  /** Cancel the run and every run blocking it (cancel is idempotent and only ever rejects a
   *  pending change, never applies it), then delete once the core no longer sees them busy. A
   *  blocker that cancelling didn't clear — an approval still pending — stops the retries at once
   *  with the approval wording instead of spinning. */
  const onCancelAndDelete = async (id: string, refused: DeleteRefused) => {
    setNotice(null);
    setError(null);
    const client = actions.client();
    try {
      const targets = [...refused.children, id];
      for (const rid of targets) await client.cancel(rid).catch(() => undefined);
      // a run waiting only for an approval never stops by itself: no point waiting for it
      if (refused.reason !== "pending_approval") await waitStopped(targets, runStatus);
      for (let i = 0; i < STOP_TRIES; i++) {
        const again = await client.deleteRun(id);
        if (!again) { setRefusal(id, null); actions.forgetRuns([id]); setNotice(`Cancelled and deleted ${labelOf(id)}.`); await load(); return; }
        setRefusal(id, again);
        const onlyApprovals = again.reason === "pending_approval"
          || (again.children.length > 0 && again.children.every(awaitingApproval) && i + 1 >= APPROVAL_TRIES);
        if (onlyApprovals) {
          setError(`Stopped, but a change is still waiting for your approval. Decide its card first (Reject keeps nothing), then delete — ${REJECTED_NOT_APPLIED}.`);
          return;
        }
        await sleep(250);
      }
      setError("The run is still stopping. Try Delete again in a moment.");
    } catch (e) { setError(message(e)); }
  };

  const onBulkDelete = async () => {
    setNotice(null);
    let done = 0;
    let refused = 0;
    const failed: string[] = [];
    // each id on its own: a 404 (deleted elsewhere) or a network error never stops the rest
    for (const id of [...selected]) {
      try { if (await deleteOne(id)) done++; else refused++; } catch (e) { failed.push(`${labelOf(id)}: ${message(e)}`); }
    }
    const bits = [`Deleted ${done} run${done === 1 ? "" : "s"}.`];
    if (refused) bits.push(`${refused} couldn't be deleted yet — the reason is shown on ${refused === 1 ? "its row" : "each row"}.`);
    if (failed.length) bits.push(`${failed.length} failed: ${failed.join("; ")}.`);
    setNotice(bits.join(" "));
    await load();
  };

  const patch = async (id: string, p: { title?: string; pinned?: boolean; archived?: boolean }) => {
    // the store's copy too, so History, the switcher and the command line show a rename at once (C-5)
    try { actions.upsertRun(await actions.client().patchRun(id, p)); await load(); } catch (e) { setError(message(e)); }
  };

  return (
    <div class="am-runs" data-runs-tab ref={root}>
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
      {cleanupOpen && (
        <CleanupPanel labelOf={labelOf} refusalOf={refusalOf}
          onDeleted={(summary) => { setNotice(summary); void load(); }} />
      )}
      {notice && <div class="am-notice text" role="status">{notice}</div>}
      {error && <div class="am-error tone-red text" role="alert">{error}</div>}
      {tree === null && !error && <div class="am-empty faint">Loading runs…</div>}
      {tree !== null && groups.length === 0 && <div class="am-empty faint">No runs yet. Start one with New run (⌘N).</div>}
      {groups.map((g, gi) => (
        <section key={g.projectId ?? "none"} class="am-group" aria-label={`Build ${g.name}`}>
          <h3 class="am-group-h">{g.name}</h3>
          <ul class="am-tree" role="tree" aria-label={`Runs in ${g.name}`} aria-multiselectable="true">
            {rowsByGroup[gi].map((row) => (
              <RunRow key={row.run.id} row={row} selected={selected.has(row.run.id)} refusal={refusals.get(row.run.id) ?? null}
                tabStop={tabStop === row.run.id} refusalOf={refusalOf}
                onKey={(e) => onTreeKey(e, row)} onFocus={() => setFocusId(row.run.id)}
                onSelect={() => toggleSel(row.run.id)} onFold={() => setFold(row.run.id, row.expanded)}
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
  row: TreeRow;
  selected: boolean;
  refusal: DeleteRefused | null;
  workers: number;
  tabStop: boolean;
  refusalOf: (r: DeleteRefused) => string;
  onKey: (e: KeyboardEvent) => void; onFocus: () => void;
  onSelect: () => void; onFold: () => void; onOpen: () => void;
  onRename: (title: string) => void; onPin: () => void; onArchive: () => void;
  onDelete: () => void; onCancelAndDelete: (r: DeleteRefused) => void;
}

function RunRow(p: RowProps): JSX.Element {
  const r = p.row.run;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  /** Esc means "discard": the blur that follows the input's removal must not save. */
  const cancelled = useRef(false);
  const act = activityByRun.value.get(r.id) ?? null;
  const waiting = act?.state === "waiting_approval" || act?.state === "waiting_user";
  const tone: LedTone = waiting ? "blue" : TONE[r.status];
  const title = runTitle(r);

  const startRename = () => { cancelled.current = false; setDraft(title); setEditing(true); };
  const commit = () => {
    if (cancelled.current) return;
    setEditing(false);
    const t = draft.trim();
    if (t && t !== title) p.onRename(t);
  };
  const cancel = () => { cancelled.current = true; setEditing(false); };

  return (
    <li role="treeitem" aria-level={p.row.depth + 1} aria-expanded={p.row.hasChildren ? p.row.expanded : undefined}
      aria-selected={p.selected} aria-label={`${title}, ${r.agent}, ${act ? activityLabel(act.state) : r.status}`}
      tabIndex={p.tabStop ? 0 : -1} onKeyDown={p.onKey} onFocus={(e) => { if (e.target === e.currentTarget) p.onFocus(); }}
      class={`am-row${p.selected ? " sel" : ""}`} data-run-row={r.id}
      style={{ "--depth": String(p.row.depth) } as JSX.CSSProperties}>
      <div class="am-line">
        <input type="checkbox" class="am-sel" checked={p.selected} aria-label={`Select ${title}`} onChange={p.onSelect} tabIndex={-1} />
        {p.row.hasChildren
          ? <button type="button" class="am-fold" tabIndex={-1} aria-label={`${p.row.expanded ? "Hide" : "Show"} ${p.workers} worker${p.workers === 1 ? "" : "s"}`} onClick={p.onFold}>{p.row.expanded ? "▾" : "▸"}</button>
          : <span class="am-fold" aria-hidden="true">{p.row.depth > 0 ? "└" : ""}</span>}
        <span class="am-dot" data-activity={act?.state ?? undefined}><Led tone={tone} title={act ? activityLabel(act.state) : r.status} /></span>
        {editing
          ? (
            <input type="text" class="am-rename" aria-label="Run title" value={draft} ref={(el) => el?.focus()}
              onInput={(e) => setDraft((e.currentTarget as HTMLInputElement).value)}
              onBlur={commit}
              onKeyDown={(e) => {
                e.stopPropagation();
                if (e.key === "Enter") { e.preventDefault(); commit(); }
                else if (e.key === "Escape") { e.preventDefault(); cancel(); }
              }} />
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
          <Button variant="ghost" onClick={startRename}>Rename</Button>
          <Button variant="ghost" aria-pressed={!!r.pinned} onClick={p.onPin}>{r.pinned ? "Unpin" : "Pin"}</Button>
          <Button variant="ghost" onClick={p.onArchive}>{r.archived ? "Unarchive" : "Archive"}</Button>
          <ConfirmButton label="Delete" confirm="Confirm delete" variant="danger" onConfirm={p.onDelete} />
        </span>
      </div>
      {p.refusal && (
        <div class="am-refused" role="alert" data-refusal={p.refusal.reason}>
          <span class="text">{p.refusalOf(p.refusal)}</span>
          <ConfirmButton label="Cancel and delete" confirm="Stop it and delete?" variant="danger" onConfirm={() => p.onCancelAndDelete(p.refusal as DeleteRefused)} />
        </div>
      )}
    </li>
  );
}

/** A two-step button: the first press arms it (label changes), the second confirms. Blur or
 *  Escape disarms. Works the same by keyboard. */
export function ConfirmButton({ label, confirm, onConfirm, variant = "default" }: {
  label: string; confirm: string; onConfirm: () => void; variant?: "default" | "danger";
}): JSX.Element {
  const [armed, setArmed] = useState(false);
  return (
    <Button variant={armed ? "danger" : variant === "danger" ? "ghost" : "default"} class={armed ? "am-armed" : undefined}
      onClick={() => { if (armed) { setArmed(false); onConfirm(); } else setArmed(true); }}
      onBlur={() => setArmed(false)}
      onKeyDown={(e: KeyboardEvent) => { if (e.key === "Escape" && armed) { e.stopPropagation(); e.preventDefault(); setArmed(false); } }}>
      {armed ? confirm : label}
    </Button>
  );
}

const AGES: { label: string; days: number | null }[] = [
  { label: "any age", days: null }, { label: "older than 1 day", days: 1 }, { label: "older than 7 days", days: 7 }, { label: "older than 30 days", days: 30 },
];
const STATUSES = ["done", "failed", "cancelled"] as const;

interface CleanupOutcome { deleted: number; total: number; kept: { id: string; why: string }[] }

/** Clean up: pick age and statuses, see the dry-run list, then delete exactly those runs, one by
 *  one (DELETE /runs/{id} re-checks each). A run that finished after the check is never included;
 *  one that became busy is kept and its reason shown. */
export function CleanupPanel({ labelOf, refusalOf, onDeleted }: {
  labelOf: (id: string) => string; refusalOf: (r: DeleteRefused) => string; onDeleted: (summary: string) => void;
}): JSX.Element {
  const [days, setDays] = useState<number | null>(7);
  const [statuses, setStatuses] = useState<ReadonlySet<string>>(new Set(STATUSES));
  const [preview, setPreview] = useState<CleanupResult | null>(null);
  const [outcome, setOutcome] = useState<CleanupOutcome | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const check = async () => {
    setBusy(true); setError(null); setOutcome(null);
    try { setPreview(await actions.client().cleanupRuns({ older_than_days: days, status_in: [...statuses], dry_run: true })); } catch (e) { setError(message(e)); }
    setBusy(false);
  };
  // any change to the filters invalidates the list: it must be checked again before deleting
  useEffect(() => { setPreview(null); }, [days, [...statuses].sort().join(",")]);

  const run = async () => {
    if (!preview) return;
    setBusy(true); setError(null);
    const client = actions.client();
    const kept: { id: string; why: string }[] = [];
    let deleted = 0;
    for (const id of preview.ids) {
      try {
        const refused = await client.deleteRun(id);
        if (refused) kept.push({ id, why: refusalOf(refused) });
        else { deleted++; actions.forgetRuns([id]); }
      } catch (e) { kept.push({ id, why: message(e) }); }
    }
    const total = preview.ids.length;
    setOutcome({ deleted, total, kept });
    setPreview(null);
    setBusy(false);
    onDeleted(deleted === total ? `Deleted ${deleted} finished run${deleted === 1 ? "" : "s"}.` : `Deleted ${deleted} of ${total} runs; ${total - deleted} kept (see Clean up).`);
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
        <div data-cleanup-preview={preview.count}>
          <div class="am-cleanup-row">
            <span class="text">{preview.count === 0 ? "Nothing to clean up." : `${preview.count} run${preview.count === 1 ? "" : "s"} would be deleted:`}</span>
            {preview.count > 0 && (
              <ConfirmButton label={`Delete ${preview.count} run${preview.count === 1 ? "" : "s"}`} confirm={`Really delete ${preview.count}?`} variant="danger"
                onConfirm={() => { void run(); }} />
            )}
          </div>
          {preview.count > 0 && (
            <ul class="am-cleanup-list faint text" aria-label="Runs that would be deleted">
              {preview.ids.slice(0, 50).map((id) => <li key={id}>{labelOf(id)}</li>)}
              {preview.ids.length > 50 && <li>{`and ${preview.ids.length - 50} more`}</li>}
            </ul>
          )}
        </div>
      )}
      {outcome && (
        <div class="am-cleanup-row" data-cleanup-outcome role="status">
          <span class="text">{`Deleted ${outcome.deleted} of ${outcome.total}.`}</span>
          {outcome.kept.length > 0 && (
            <ul class="am-cleanup-list text" aria-label="Runs kept">
              {outcome.kept.map((k) => <li key={k.id} data-kept={k.id}>{`${labelOf(k.id)} — ${k.why}`}</li>)}
            </ul>
          )}
        </div>
      )}
      {error && <div class="am-error tone-red text" role="alert">{error}</div>}
    </div>
  );
}
