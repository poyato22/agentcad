// Agent announcements (R16) as the core stores them: every `agent_announce` {run_id, kind, title,
// detail, ref} is also a DECISION step {"agent_announce": {...}} on the acting run and on its
// lead (core announce.announce), so the thread rebuilds its cards from steps — after a reload
// too — and the live event only triggers a step fetch. Pure: no DOM, no store.
import type { Announcement, AnnounceRef, Step } from "../../api/types";

const str = (v: unknown): string | null => (typeof v === "string" && v.trim() !== "" ? v : null);
const rec = (v: unknown): Record<string, unknown> => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {});

function parseRef(v: unknown): AnnounceRef {
  const r = rec(v);
  const out: AnnounceRef = {};
  for (const k of ["part_id", "run_id", "instance_id", "bom_id", "snapshot_id", "catalog_id", "import_id", "sketch_id", "pitch_id"] as const) {
    const s = str(r[k]);
    if (s) out[k] = s;
  }
  const img = Array.isArray(r.image_ids) ? str(r.image_ids[0]) : null;
  if (img) out.image_id = img;
  return out;
}

/** The announcement a DECISION step stores, or null when it isn't one (or lacks a kind/title).
 *  `run_id` is the thread's run (where the card shows); the acting run is `actor`. */
export function announcementOf(step: Step): (Announcement & { actor: string | null; projectId: string | null; jointId: string | null }) | null {
  if (step.kind !== "decision") return null;
  const d = rec(step.payload.agent_announce);
  const kind = str(d.kind);
  const title = str(d.title);
  if (!kind || !title) return null;
  const ref = rec(d.ref);
  return {
    id: `step:${step.id}`, run_id: step.run_id, kind, title, detail: str(d.detail), ref: parseRef(d.ref), ts: step.ts,
    actor: str(d.run_id), projectId: str(ref.project_id), jointId: str(ref.joint_id),
  };
}

/** A message the user sent while the lead was busy (core lead._enqueue's DECISION step). */
export interface QueuedStep { messageId: string; text: string; position: number | null }
export function queuedOf(step: Step): QueuedStep | null {
  if (step.kind !== "decision") return null;
  const q = rec(step.payload.queued_message);
  const id = str(q.message_id);
  if (!id) return null;
  return { messageId: id, text: typeof q.text === "string" ? q.text : "", position: typeof q.position === "number" ? q.position : null };
}
/** queued_message_delivered → the id; queued_message_dropped → the id and why. */
export function deliveredOf(step: Step): string | null {
  return step.kind === "decision" ? str(rec(step.payload.queued_message_delivered).message_id) : null;
}
export function droppedOf(step: Step): { messageId: string; reason: string } | null {
  if (step.kind !== "decision") return null;
  const d = rec(step.payload.queued_message_dropped);
  const id = str(d.message_id);
  return id ? { messageId: id, reason: str(d.reason) ?? "not delivered" } : null;
}
