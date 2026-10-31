// The build's studies and generative runs for the dock's Simulations stream (FW-app I3, D19): not
// only the ones the dock's agent tree runs (treeStreams reads those from their steps), but every
// study or generative design of the build — a study started from the Studies page (its own
// "studies" run, which GET /runs leaves out), a studies-agent run you are not looking at, a
// generative run started from the panel (the build's ui run). Read from the core's records
// (GET /sims: the build's parts' and its assembly's studies; GET /topopt?project_id=), refreshed
// when the build changes and on the events that start, move or end one (sim_progress of a study
// not listed yet or at its end, sim_finished, topopt_progress / topopt_finished, run_finished, a
// study or generative tool's result). Records are core data: rendered as text.
import { computed, effect, signal, type ReadonlySignal, type Signal } from "@preact/signals";
import { onCoreEvent } from "../../api/events";
import type { StudyRecord, StudySummary } from "../../api/studies";
import type { TopoptRecord } from "../../api/topopt";
import type { MisesEvent } from "../../api/types";
import { debug } from "../../lib/log";
import { activeBuildId } from "../../state/builds";
import { actions, conn, parts } from "../../state/store";

/** At most this many records in the stream (newest first): the Studies page has the history. */
export const KEEP = 20;

export type BuildStudy =
  | { kind: "sim"; id: string; runId: string | null; partId: string; at: number; status: string; sim: StudySummary }
  | { kind: "topopt"; id: string; runId: string | null; partId: string; at: number; status: string; topopt: TopoptRecord };

export interface BuildStudies { buildId: string | null; items: readonly BuildStudy[] }

export const buildStudies: Signal<BuildStudies> = signal<BuildStudies>({ buildId: null, items: [] });

/** A finished study's full record (its card), by sim id; fetched once each by the card. */
export const studyRecords: Signal<ReadonlyMap<string, StudyRecord>> = signal<ReadonlyMap<string, StudyRecord>>(new Map());

/** The shown build's records (an older build's list never shows in another). */
export const shownBuildStudies: ReadonlySignal<readonly BuildStudy[]> = computed(() => {
  const b = buildStudies.value;
  return b.buildId !== null && b.buildId === activeBuildId.value ? b.items : [];
});

/** The build's studies and generative runs, newest first, at most KEEP (pure, for tests). */
export function mergeRecords(sims: readonly StudySummary[], topopts: readonly TopoptRecord[], partIds: ReadonlySet<string>, buildId: string): BuildStudy[] {
  const seen = new Set<string>();
  const out: BuildStudy[] = [];
  for (const s of sims) {
    // a part study counts when the part is the build's; an assembly study names its build
    const mine = (s as { project_id?: unknown }).project_id === buildId || partIds.has(s.part_id);
    if (!mine || seen.has(s.sim_id)) continue;
    seen.add(s.sim_id);
    out.push({ kind: "sim", id: s.sim_id, runId: s.run_id, partId: s.part_id, at: s.created_at, status: s.status, sim: s });
  }
  for (const t of topopts) {
    if ((t.project_id ?? buildId) !== buildId || seen.has(t.topopt_id)) continue;
    seen.add(t.topopt_id);
    out.push({ kind: "topopt", id: t.topopt_id, runId: t.run_id ?? null, partId: t.part_id, at: t.created_at ?? 0, status: t.status, topopt: t });
  }
  out.sort((a, b) => b.at - a.at);
  return out.slice(0, KEEP);
}

let seq = 0;
export async function refreshBuildStudies(): Promise<void> {
  const b = activeBuildId.peek();
  const mine = ++seq;
  if (!b) { buildStudies.value = { buildId: null, items: [] }; return; }
  let client;
  try { client = actions.client(); } catch { return; }   // no core bound yet
  const quiet = <T>(p: Promise<T[]>): Promise<T[]> => p.catch((e: unknown) => { debug("build studies refresh", e); return []; });
  // GET /sims?project_id= lists a build's assembly studies only: its parts' come from the full list
  const [all, asm, topopts] = await Promise.all([
    quiet(client.listStudies()), quiet(client.listStudies({ projectId: b })), quiet(client.topoptList({ projectId: b })),
  ]);
  if (mine !== seq || activeBuildId.peek() !== b) return;
  const ids = new Set(parts.peek().filter((p) => (p.project_id ?? b) === b).map((p) => p.part_id));
  buildStudies.value = { buildId: b, items: mergeRecords([...asm, ...all], topopts, ids, b) };
}

/** A finished study's card: the full record, read once. */
export async function loadStudyRecord(simId: string): Promise<void> {
  if (studyRecords.peek().has(simId)) return;
  try {
    const r = await actions.client().getStudy(simId);
    const m = new Map(studyRecords.peek());
    m.set(simId, r);
    while (m.size > KEEP * 3) m.delete(m.keys().next().value as string);
    studyRecords.value = m;
  } catch (e) {
    debug("study record", simId, e);
  }
}

const TERMINAL = /^(done|failed|cancelled|interrupted)$/;
const JOB_TOOLS = /^(mcp__mises__)?(sim_|topopt_run$|topopt_generate$|design_review$|study_run$)/;

let timer: ReturnType<typeof setTimeout> | undefined;
function soon(): void {
  clearTimeout(timer);
  timer = setTimeout(() => { void refreshBuildStudies(); }, 250);
}

/** Does this event start, move or end a study or generative run the list should follow? (Exported for tests.) */
export function wantsRefresh(ev: MisesEvent, known: ReadonlySet<string>): boolean {
  const d = ev.data;
  switch (ev.type) {
    case "sim_progress": {
      const id = typeof d.sim_id === "string" ? d.sim_id : null;
      const end = [d.stage, d.phase].some((x) => typeof x === "string" && TERMINAL.test(x));
      return id !== null && (!known.has(id) || end);
    }
    case "topopt_progress": return typeof d.topopt_id === "string" && !known.has(d.topopt_id);
    case "sim_finished": case "topopt_finished": case "run_finished": return true;
    case "tool_result": return typeof d.name === "string" && JOB_TOOLS.test(d.name);
    default: return false;
  }
}

let users = 0;
let stop: (() => void) | null = null;

/** Follow the shown build's studies while the dock is up (ref-counted). Returns a release. */
export function retainBuildStudies(): () => void {
  if (users++ === 0) {
    const off = onCoreEvent((ev) => {
      const known = new Set(buildStudies.peek().items.map((i) => i.id));
      // the finished study's card changed: read it again
      if (ev.type === "sim_finished" && typeof ev.data.sim_id === "string" && studyRecords.peek().has(ev.data.sim_id)) {
        const m = new Map(studyRecords.peek()); m.delete(ev.data.sim_id); studyRecords.value = m;
      }
      if (wantsRefresh(ev, known)) soon();
    });
    const dispose = effect(() => { void activeBuildId.value; if (conn.value === "open") void refreshBuildStudies(); });
    stop = () => { off(); dispose(); clearTimeout(timer); };
  }
  let released = false;
  return () => {
    if (released) return;
    released = true;
    if (--users === 0) { stop?.(); stop = null; }
  };
}

/** Test helper. */
export function resetBuildStudies(): void {
  buildStudies.value = { buildId: null, items: [] };
  studyRecords.value = new Map();
  clearTimeout(timer);
  seq++;
}
