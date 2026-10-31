// Plan 9 D4: the agent dock's three streams. Every thread item lands in exactly one of them —
// **view** (presented views, critique rounds, renders, part cards), **sim** (study / review cards
// and the other study tools) or **text** (everything else: messages, approvals, questions,
// announcements, errors). The core marks steps and events with `stream` (B3); an older core
// doesn't, and the item's type decides. Cards that need the human (approvals, questions), your
// own messages, the agent's words and errors always stay in the conversation, whatever a step
// says: a stream is where something shows, never a way to hide it. Pure: no DOM, no store.
import type { MisesEvent, StreamKind } from "../../api/types";
import { studyCallView } from "./studycard/cardModel";
import type { ThreadItem } from "./threadModel";

export type { StreamKind };
export const STREAMS: readonly StreamKind[] = ["view", "sim", "text"];

const bare = (name: string): string => name.replace(/^mcp__mises__/, "");

/** Study tools: B3's sim_* (sim_run, sim_static_run, sim_wait, sim_result_get, …), study_*, the
 *  design review and generative design (topopt_*, Plan 9 D14). */
const SIM_TOOL = /^(sim_|study_|studies_|topopt_)|^design_review$/;
/** Tools whose output is a picture of the model. */
const VIEW_TOOL = /^(present_view|critique_model|part_show|part_render|assembly_animate)$|^render(_|$)|^view_/;

/** The stream a tool call belongs to, by its name (the fallback when its steps carry none). */
export function toolStream(name: string): StreamKind {
  const n = bare(name);
  if (SIM_TOOL.test(n)) return "sim";
  if (VIEW_TOOL.test(n)) return "view";
  return "text";
}

/** Announcement kinds that are pictures (the rest are conversation cards). */
const VIEW_ANNOUNCE: ReadonlySet<string> = new Set(["view_presented", "critique_round", "part_shown"]);

/** The stream of a core event: its own `stream`, else by type (sim_progress / sim_finished → sim;
 *  render / view / critique events → view). */
export function eventStream(ev: Pick<MisesEvent, "type"> & { stream?: StreamKind }): StreamKind {
  if (ev.stream) return ev.stream;
  const t = ev.type;
  if (/^(sim|study|topopt)_/.test(t)) return "sim";
  if (/^(render|view|critique)_/.test(t) || t === "part_shown") return "view";
  return "text";
}

/** Kinds that always stay in the conversation (safety and the dialogue itself). */
const ALWAYS_TEXT: ReadonlySet<ThreadItem["kind"]> = new Set(["user", "agent", "approval", "question", "queued", "error", "fold"]);

/** The stream a thread item shows in. */
export function streamOf(item: ThreadItem): StreamKind {
  if (ALWAYS_TEXT.has(item.kind)) return "text";
  switch (item.kind) {
    case "studies": return "sim";
    case "activity": {
      const c = item.call;
      const marked = c?.stream ?? c?.result?.stream ?? item.diff?.step.stream ?? null;
      if (marked) return marked;
      return c ? toolStream(c.name) : "text";
    }
    case "critique": case "view": return item.stream ?? "view";
    case "announce": return item.stream ?? (VIEW_ANNOUNCE.has(item.announcement.kind) ? "view" : "text");
    case "note": return item.stream ?? "text";
    default: return "text";
  }
}

export interface Streams { view: ThreadItem[]; sim: ThreadItem[]; text: ThreadItem[] }

/** The thread split into the three streams, each in thread order. Study groups (R18's grid) are
 *  one card per study here: the Simulations stream orders them itself. */
export function splitStreams(items: readonly ThreadItem[]): Streams {
  const out: Streams = { view: [], sim: [], text: [] };
  for (const it of items) {
    const s = streamOf(it);
    if (s === "sim" && it.kind === "studies") {
      it.calls.forEach((call, i) => out.sim.push({
        // the key the study has on its own (logModel itemKey "c:<call id>"): joining a group
        // never remounts its card nor makes it unread again
        kind: "activity", key: `c:${call.callId}`, text: call.name, tone: call.ok === false ? "fail" : call.result === null ? "running" : "ok",
        ts: call.startedTs, call, diff: null, extra: [], hint: call.ok === false ? call.hint : null, stepIds: i === 0 ? it.stepIds : [],
      }));
      continue;
    }
    out[s].push(it);
  }
  return out;
}

/** Is this Simulations item a study still running (the call in flight, or B3's immediate
 *  {status: "running"} result whose study goes on in the background until its `sim_finished`)?
 *  `finished`: sim ids the core reported finished (state/activity finishedSims). */
export function simRunning(item: ThreadItem, finished: ReadonlyMap<string, string> = new Map()): boolean {
  if (item.kind !== "activity" || !item.call) return false;
  const c = item.call;
  if (c.ok === false) return false;
  if (c.result === null) return true;
  const out = c.result.payload.output;
  const data = typeof out === "object" && out !== null ? (out as Record<string, unknown>).data : null;
  const d = typeof data === "object" && data !== null ? (data as Record<string, unknown>) : {};
  const v = studyCallView(c);
  const simId = (v?.kind === "study" ? v.simId : null) ?? (typeof d.sim_id === "string" ? d.sim_id : null)
    ?? (typeof d.topopt_id === "string" ? d.topopt_id : null);
  if (simId && finished.has(simId)) return false;
  if (v?.kind === "study" && v.card?.status === "running") return true;
  return d.status === "running";
}

/** The Simulations stream's order: running studies on top, then the rest, newest first in each. */
export function simOrder<T = ThreadItem>(items: readonly T[], finished: ReadonlyMap<string, string> = new Map(),
  itemOf: (x: T) => ThreadItem = (x) => x as unknown as ThreadItem): T[] {
  const indexed = items.map((it, i) => ({ it, i, run: simRunning(itemOf(it), finished) }));
  indexed.sort((a, b) => (a.run === b.run ? b.i - a.i : a.run ? -1 : 1));
  return indexed.map((x) => x.it);
}
