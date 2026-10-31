// The dock's Simulations stream (Plan 9 D4): one card per study the build's agents run (the lead
// and every sub-agent, treeStreams), running ones on top with their live phase bar, then the
// finished ones, newest first. Each card names the agent that ran it. A study never holds up the
// conversation or the views: they are separate streams. Study tools without a card (B3's
// sim_wait / sim_result_get) read as their one-line row. A card whose study, review or design
// study still runs in the background (B3: GET /jobs) has its own Stop (POST /jobs/{id}/cancel);
// the agent gets no turn for a job you stop.
import type { JSX } from "preact";
import { memo } from "preact/compat";
import { jobIdOf } from "../../api/jobs";
import { finishedSims } from "../../state/activity";
import { isLiveJob, stopJob, stoppingJobs } from "../../state/jobs";
import { Item } from "./Conversation";
import { labelOf } from "./derived";
import { simOrder } from "./streams";
import { StudyCardSlot } from "./studycard";
import { studyCallView } from "./studycard/cardModel";
import type { DockEntry } from "./treeStreams";

/** Memoized: re-renders only when the entries change (a streamed token never changes them). */
export const SimStream = memo(SimStreamList);

function SimStreamList({ entries }: { entries: readonly DockEntry[] }): JSX.Element {
  if (entries.length === 0) return <p class="dock-empty faint" data-stream-empty>Studies the agents run show here, live.</p>;
  return (
    <div class="ss" data-sim-stream>
      {simOrder(entries, finishedSims.value, (e) => e.item).map(({ key, item: it, runId, agent }) => (
        <div key={key} class="ss-item" data-key={key} data-run={runId}>
          <div class="ss-head">
            <RunTag runId={runId} />
            {it.kind === "activity" && it.call && <JobStop data={resultData(it.call.result?.payload)} />}
          </div>
          {it.kind === "activity" && it.call && studyCallView(it.call)
            ? <StudyCardSlot call={it.call} runId={runId} />
            : <Item item={it} agent={agent} runId={runId} />}
        </div>
      ))}
    </div>
  );
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
