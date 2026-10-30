// Live per-run announcements, fed by the `agent_announce` event (U3c). The core emits one whenever
// an agent creates a part, spawns an agent, adds to the assembly or the BOM, shows a part, imports
// a drawing or animates the assembly — whatever the model says — so the thread can show a clear
// card (R16). Title and detail are untrusted model/tool text and render as text.
import { signal, type Signal } from "@preact/signals";
import { onCoreEvent } from "../api/events";
import type { Announcement, AnnounceRef, MisesEvent } from "../api/types";

export const announcementsByRun: Signal<ReadonlyMap<string, Announcement[]>> = signal<ReadonlyMap<string, Announcement[]>>(new Map());

const str = (v: unknown): string | null => (typeof v === "string" && v.trim() !== "" ? v : null);
const refStr = (r: Record<string, unknown>, k: string): string | null => str(r[k]);

function parseRef(v: unknown): AnnounceRef {
  const r = (typeof v === "object" && v !== null && !Array.isArray(v) ? v : {}) as Record<string, unknown>;
  return {
    part_id: refStr(r, "part_id"), run_id: refStr(r, "run_id"), instance_id: refStr(r, "instance_id"),
    bom_id: refStr(r, "bom_id"), snapshot_id: refStr(r, "snapshot_id"), catalog_id: refStr(r, "catalog_id"),
    import_id: refStr(r, "import_id"),
  };
}

/** An `agent_announce` event → one Announcement, or null when it isn't one / has no run or kind. */
export function parseAnnouncement(ev: MisesEvent): Announcement | null {
  if (ev.type !== "agent_announce") return null;
  const d = ev.data;
  const runId = ev.run_id ?? str(d.run_id);
  const kind = str(d.kind);
  const title = str(d.title);
  if (!runId || !kind || !title) return null;
  return {
    id: str(d.id) ?? `${runId}:${ev.ts}:${kind}`,
    run_id: runId, kind, title, detail: str(d.detail), ref: parseRef(d.ref), ts: ev.ts,
  };
}

export function recordAnnouncement(ev: MisesEvent): void {
  if (ev.type === "run_deleted") {
    const ids = [ev.run_id, ev.data.run_id, ...(Array.isArray(ev.data.run_ids) ? ev.data.run_ids : [])].filter((x): x is string => typeof x === "string");
    forgetAnnouncements(ids);
    return;
  }
  const a = parseAnnouncement(ev);
  if (!a) return;
  const m = new Map(announcementsByRun.value);
  const list = m.get(a.run_id) ?? [];
  // de-dupe by id (a reconnect can replay): keep the first seen
  if (list.some((x) => x.id === a.id)) return;
  m.set(a.run_id, [...list, a]);
  announcementsByRun.value = m;
}

/** The run's announcements, oldest first (empty when none). */
export function announcementsForRun(runId: string | null): Announcement[] {
  if (!runId) return [];
  return announcementsByRun.value.get(runId) ?? [];
}

export function forgetAnnouncements(ids: readonly string[]): void {
  if (!ids.length) return;
  const m = new Map(announcementsByRun.value);
  let changed = false;
  for (const id of ids) if (m.delete(id)) changed = true;
  if (changed) announcementsByRun.value = m;
}

export function resetAnnouncements(): void { announcementsByRun.value = new Map(); }

/** Subscribes to the event stream (default onCoreEvent). Call once at boot; returns an unsubscribe. */
export function startAnnouncements(subscribe: (cb: (e: MisesEvent) => void) => () => void = onCoreEvent): () => void {
  return subscribe(recordAnnouncement);
}
