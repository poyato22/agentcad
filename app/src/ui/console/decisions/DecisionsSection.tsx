// view-tab: the top of the View tab: "Needs your decision" — each item says what is being decided
// and by which agent, with its own action buttons (the same cards as the conversation, sharing
// one request per item: decisions/once). Not shown when nothing waits.
import type { JSX } from "preact";
import { useEffect } from "preact/hooks";
import { activeBuildId } from "../../../state/builds";
import { PitchCards } from "../../sketch/PitchCards";
import { SketchCard } from "../../sketch/SketchCard";
import { loadConcept } from "../../sketch/state";
import { ApprovalCard } from "../approvals/ApprovalCard";
import { QuestionCard } from "../approvals/QuestionCard";
import { labelOf } from "../derived";
import { pendingDecisions, type PendingDecision } from "./pending";
import "./decisions.css";

const KIND_WORD: Readonly<Record<PendingDecision["kind"], string>> = {
  approval: "Approval", question: "Question", pitch: "Design direction", sketch: "Concept sketch",
};

export function DecisionsSection(): JSX.Element | null {
  const pid = activeBuildId.value;
  // the concept stage's decisions come from the build's concept view: read it once per build
  useEffect(() => { if (pid) void loadConcept(pid); }, [pid]);
  const items = pendingDecisions.value;
  if (items.length === 0) return null;
  return (
    <section class="dz" data-decisions={items.length} aria-label={`Needs your decision · ${items.length}`}>
      <header class="dz-h">
        <span class="dz-t">Needs your decision</span>
        <span class="dz-n num" data-decisions-count>{items.length}</span>
      </header>
      <ol class="dz-list">
        {items.map((it) => (
          <li key={it.key} class="dz-item" data-decision-item={it.kind} data-key={it.key}>
            <div class="dz-what">
              <span class="dz-kind">{KIND_WORD[it.kind]}</span>
              <span class="dz-text text">{it.what}</span>
              <span class="dz-who faint text" data-decision-agent>{it.runId ? labelOf(it.runId) : "agent"}</span>
            </div>
            <Body item={it} />
          </li>
        ))}
      </ol>
    </section>
  );
}

function Body({ item }: { item: PendingDecision }): JSX.Element {
  switch (item.kind) {
    case "approval": return <ApprovalCard approval={item.approval} runLabel={labelOf(item.runId)} />;
    case "question": return <QuestionCard question={item.question} runLabel={labelOf(item.runId)} />;
    case "pitch": return <PitchCards projectId={item.projectId} pitchId={item.pitchId} />;
    case "sketch": return <SketchCard projectId={item.projectId} sketchId={item.sketchId} />;
  }
}
