// The dock's Simulations stream (Plan 9 D4): one card per study the agents run, running ones on
// top with their live phase bar, then the finished ones, newest first. A study never holds up the
// conversation or the views: they are separate streams over the same thread. Study tools without
// a card (B3's sim_wait / sim_result_get) read as their one-line row.
import type { JSX } from "preact";
import type { AgentRun } from "../../api/types";
import { finishedSims } from "../../state/activity";
import { Item } from "./Conversation";
import { simOrder } from "./streams";
import { StudyCardSlot } from "./studycard";
import { studyCallView } from "./studycard/cardModel";
import type { ThreadItem } from "./threadModel";

export function SimStream({ items, agent, runId }: { items: readonly ThreadItem[]; agent: AgentRun["agent"]; runId: string | null }): JSX.Element {
  if (items.length === 0) return <p class="dock-empty faint" data-stream-empty>Studies the agents run show here, live.</p>;
  return (
    <div class="ss" data-sim-stream>
      {simOrder(items, finishedSims.value).map((it) => (
        <div key={it.key} class="ss-item" data-key={it.key}>
          {it.kind === "activity" && it.call && studyCallView(it.call)
            ? <StudyCardSlot call={it.call} runId={runId} />
            : <Item item={it} agent={agent} runId={runId} />}
        </div>
      ))}
    </div>
  );
}
