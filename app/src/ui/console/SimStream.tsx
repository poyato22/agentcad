// The dock's Simulations stream (Plan 9 D4): one card per study the build's agents run (the lead
// and every sub-agent, treeStreams), running ones on top with their live phase bar, then the
// finished ones, newest first. Each card names the agent that ran it. A study never holds up the
// conversation or the views: they are separate streams. Study tools without a card (B3's
// sim_wait / sim_result_get) read as their one-line row. A card whose study, review or design
// study still runs in the background (B3: GET /jobs) has its own Stop (POST /jobs/{id}/cancel);
// the agent gets no turn for a job you stop.
//
// FW-app I3 (D19): every other study and generative run of the build joins it from the core's
// records (buildStudies) — a study run from the Studies page, the studies agent's runs, a
// generative run started from the panel — unless the tree already shows it.
import { computed, type ReadonlySignal } from "@preact/signals";
import type { JSX } from "preact";
import { memo } from "preact/compat";
import { useEffect } from "preact/hooks";
import { jobIdOf } from "../../api/jobs";
import { AssemblyStudyCard } from "./AssemblyStudyCard";
import { finishedSims } from "../../state/activity";
import { isLiveJob, stopJob, stoppingJobs } from "../../state/jobs";
import { isTerminal, liveProgress, type LiveProgress } from "../pages/studies/live";
import { topoptIdOf, TopoptRunCard } from "../topopt/TopoptCard";
import { loadStudyRecord, shownBuildStudies, studyRecords, type BuildStudy } from "./buildStudies";
import { Item } from "./Conversation";
import { effectiveRunId, labelOf, treeFrame } from "./derived";
import { isRocketCallName } from "./rocketcard/model";
import { RocketCardSlot } from "./rocketcard/RocketCard";
import { simRunning } from "./streams";
import { StudyCardSlot } from "./studycard";
import { studyCallView, type StudyCallView } from "./studycard/cardModel";
import { StudyCard } from "./studycard/StudyCard";
import type { DockEntry } from "./treeStreams";

/** Memoized: re-renders only when the entries change (a streamed token never changes them). */
export const SimStream = memo(SimStreamList);

/** The study / review / generative ids a tree entry stands for. */
function idsOf(e: DockEntry): string[] {
  const it = e.item;
  if (it.kind !== "activity" || !it.call) return [];
  const out: string[] = [];
  const v = studyCallView(it.call);
  if (v?.kind === "study" && v.simId) out.push(v.simId);
  const d = resultData(it.call.result?.payload);
  for (const k of ["sim_id", "topopt_id"] as const) if (typeof d?.[k] === "string") out.push(d[k] as string);
  const t = topoptIdOf(it.call);
  if (t) out.push(t);
  return out;
}

/** Is a record's study or generative run still going? */
export function recordRunning(r: BuildStudy, finished: ReadonlyMap<string, string>, live: ReadonlyMap<string, LiveProgress>): boolean {
  if (r.status !== "running" || finished.has(r.id)) return false;
  if (r.kind === "assembly") return true;
  const p = r.kind === "sim" ? live.get(r.id) : undefined;
  return !p || !isTerminal(p);
}

/** The build's records the tree doesn't already show (by id, or because their run is in the dock's
 *  tree, whose steps bring them), pure for tests. */
export function extraStudies(entries: readonly DockEntry[], records: readonly BuildStudy[], treeRuns: readonly string[]): BuildStudy[] {
  const shown = new Set(entries.flatMap(idsOf));
  const runs = new Set(treeRuns);
  return records.filter((r) => !shown.has(r.id) && !(r.runId !== null && runs.has(r.runId)));
}

/** The Simulations stream's records for the shown build (the Dock counts them too). */
export const simExtras: ReadonlySignal<readonly BuildStudy[]> = computed(() => {
  const t = treeFrame.value;
  return extraStudies(t.sim, shownBuildStudies.value, t.runIds ?? []);
});

/** Do the View and Simulations streams hold more than one run's work, or another run's than the
 *  conversation shows? Only then does each card name its run (FW-app M6: the one agent's tag
 *  repeated on every card is a wasted line). */
export const manyRuns: ReadonlySignal<boolean> = computed(() => {
  const t = treeFrame.value;
  const ids = new Set([...t.view, ...t.sim].map((e) => e.runId));
  for (const r of simExtras.value) ids.add(r.runId ?? `rec/${r.kind}`);
  return ids.size > 1 || (ids.size === 1 && !ids.has(effectiveRunId.value ?? ""));
});

/** How many of them run now. */
export const simExtrasRunning: ReadonlySignal<number> = computed(() =>
  simExtras.value.filter((r) => recordRunning(r, finishedSims.value, liveProgress.value)).length);

interface Row { key: string; running: boolean; at: number; idx: number; node: JSX.Element }

function SimStreamList({ entries }: { entries: readonly DockEntry[] }): JSX.Element {
  const extras = simExtras.value;
  if (entries.length === 0 && extras.length === 0) return <p class="dock-empty faint" data-stream-empty>Studies the agents run show here, live.</p>;
  const finished = finishedSims.value;
  const live = liveProgress.value;
  const tags = manyRuns.value;
  const rows: Row[] = [
    ...entries.map(({ key, item: it, runId, agent, order }, idx) => ({
      key, idx, running: simRunning(it, finished), at: order === Infinity ? Number.MAX_VALUE : order,
      node: (
        <div key={key} class="ss-item" data-key={key} data-run={runId}>
          <div class="ss-head">
            {tags ? <RunTag runId={runId} /> : <span />}
            {it.kind === "activity" && it.call && <JobStop data={resultData(it.call.result?.payload)} />}
          </div>
          {it.kind === "activity" && it.call && studyCallView(it.call)
            ? <StudyCardSlot call={it.call} runId={runId} />
            : it.kind === "activity" && it.call && isRocketCallName(it.call.name)
              ? <RocketCardSlot call={it.call} />
              : <Item item={it} agent={agent} runId={runId} />}
        </div>
      ),
    })),
    ...extras.map((r, i) => {
      const running = recordRunning(r, finished, live);
      const key = `rec/${r.id}`;
      return {
        key, idx: entries.length + i, running, at: r.at,
        node: (
          <div key={key} class="ss-item" data-key={key} data-run={r.runId ?? undefined} data-record={r.kind}>
            <div class="ss-head">
              {!tags ? <span /> : r.runId ? <RunTag runId={r.runId} /> : <div class="dock-run faint text">{r.kind === "topopt" ? "Generative design" : "Study"}</div>}
              <JobStop data={r.kind === "sim" ? { sim_id: r.id } : r.kind === "assembly" ? { study_id: r.id } : { topopt_id: r.id }} />
            </div>
            {r.kind === "sim" ? <RecordStudyCard rec={r} running={running} />
              : r.kind === "assembly" ? <div class="scard-slot" data-assembly-slot><AssemblyStudyCard s={r.asm} running={running} /></div>
                : <div class="scard-slot" data-topopt-slot><TopoptRunCard id={r.id} partId={r.partId} /></div>}
          </div>
        ),
      };
    }),
  ];
  rows.sort((a, b) => (a.running !== b.running ? (a.running ? -1 : 1) : a.at !== b.at ? b.at - a.at : b.idx - a.idx));
  return <div class="ss" data-sim-stream>{rows.map((r) => r.node)}</div>;
}

/** A study from the core's record (no agent step behind it on screen): the same card as the
 *  thread's, its live phase from sim_progress, its results from GET /sims/{id} once done. */
function RecordStudyCard({ rec, running }: { rec: Extract<BuildStudy, { kind: "sim" }>; running: boolean }): JSX.Element {
  const full = studyRecords.value.get(rec.id);
  const done = !running;
  useEffect(() => { if (done) void loadStudyRecord(rec.id); }, [rec.id, done, rec.status]);
  return <div class="scard-slot" data-study-slot><StudyCard view={recordView(rec, full, running)} /></div>;
}

/** A record as a study card's view (pure, for tests). */
export function recordView(rec: Extract<BuildStudy, { kind: "sim" }>, full: { card?: StudyCallView["card"] | null } | undefined, running: boolean): StudyCallView {
  const s = rec.sim;
  const card = running ? null : full?.card ?? null;
  return {
    kind: "study", callId: `rec:${s.sim_id}`, tool: "sim_run", runId: null, type: s.type ?? card?.type ?? "study",
    partId: s.part_id, reason: s.reason ?? null, settings: null, card, checks: card?.checks ?? [], running,
    failed: !running && (s.status === "failed" || s.status === "interrupted"),
    summary: s.error ?? null, hint: s.hint ?? null, startedTs: s.created_at, simId: s.sim_id,
  };
}

/** The tool result's data (`{sim_id | review_id | study_id, status, …}`) of a call's step. */
function resultData(payload: Record<string, unknown> | undefined): Record<string, unknown> | null {
  const out = payload?.output;
  if (typeof out !== "object" || out === null) return null;
  const data = (out as { data?: unknown }).data;
  return typeof data === "object" && data !== null ? data as Record<string, unknown> : null;
}

/** Stop for a card whose job still runs in the background; nothing once it has ended. */
export function JobStop({ data }: { data: Record<string, unknown> | null }): JSX.Element | null {
  const id = jobIdOf(data);
  if (!id || !isLiveJob(id)) return null;
  const stopping = stoppingJobs.value.has(id);
  return (
    <button type="button" class="btn ss-stop" data-job-stop={id} disabled={stopping}
      title="Stop this study (the agent keeps working on the rest)" onClick={() => { void stopJob(id); }}>
      {stopping ? "Stopping…" : "Stop"}
    </button>
  );
}

/** Which agent a card came from ("designer · motor mount"). */
export function RunTag({ runId }: { runId: string }): JSX.Element {
  return <div class="dock-run faint text" data-run-tag={runId}>{labelOf(runId)}</div>;
}
