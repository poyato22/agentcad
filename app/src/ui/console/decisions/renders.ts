// view-tab: the pictures a render tool returned. render_views, present_view, concept_sketch,
// section_check and critique_model results carry render ids (GET /renders/{id}.png) in their
// output data — render_ids {view: id} or [id, …], thumbnail_id, before / after — but the View
// tab only knew the announcement cards (view_presented, critique_round, concept_sketch …), which
// a Claude Code session's tool calls never write as steps. So those renders reached the View tab
// as bare text rows, without their picture. This reads them. Pure.
import type { ThreadItem } from "../threadModel";

export interface RenderImage { id: string; label: string }
export interface RenderSet {
  /** The tool that made it, as words ("render views"). */
  tool: string;
  images: RenderImage[];
  /** A concept sketch / pitch the result is about (the card with its decisions shows it). */
  sketchId: string | null;
  pitchId: string | null;
  /** When the result landed (s). */
  ts: number | null;
}

const RID = /^[A-Za-z0-9_.-]{1,128}$/;
const rec = (v: unknown): Record<string, unknown> => (typeof v === "object" && v !== null && !Array.isArray(v) ? v as Record<string, unknown> : {});
const str = (v: unknown): string | null => (typeof v === "string" && RID.test(v) ? v : null);

export const bareTool = (name: string): string => name.replace(/^mcp__mises__/, "");

/** The render set of a thread item (a finished tool call whose result carries render ids), else null. */
export function renderSetOf(item: ThreadItem): RenderSet | null {
  if (item.kind !== "activity" || !item.call || !item.call.result || item.call.ok === false) return null;
  const c = item.call;
  const data = rec(rec(rec(c.result!.payload).output).data);
  const images: RenderImage[] = [];
  const seen = new Set<string>();
  const add = (id: string | null, label: string) => { if (id && !seen.has(id)) { seen.add(id); images.push({ id, label }); } };
  const ids = data.render_ids;
  if (Array.isArray(ids)) {
    const views = Array.isArray(data.views) ? data.views : [];
    ids.forEach((v, i) => add(str(v), typeof views[i] === "string" ? String(views[i]).replace(/_/g, " ") : `view ${i + 1}`));
  } else {
    for (const [view, id] of Object.entries(rec(ids))) add(str(id), view.replace(/_/g, " "));
  }
  add(str(data.thumbnail_id), "thumbnail");
  for (const k of ["before", "after"] as const) {
    const v = data[k];
    if (typeof v === "string") add(str(v), k);
    else for (const [view, id] of Object.entries(rec(v))) add(str(id), `${k} · ${view}`);
  }
  const sketchId = str(data.sketch_id);
  const pitchId = str(data.pitch_id);
  if (images.length === 0 && !sketchId && !pitchId) return null;
  return { tool: bareTool(c.name).replace(/_/g, " "), images, sketchId, pitchId, ts: c.result!.ts ?? null };
}
