// A feedback pin in the thread (Plan 7 U2a): the user's words, then "Comment 2 · on Mount plate ·
// Ø4.2 hole, 1.9 mm away", from the step's structured `feedback`; the chip shows the part.
import type { JSX } from "preact";
import type { FeedbackPin } from "../../api/measure";
import { partName } from "../../lib/names";
import { actions, parts } from "../../state/store";
import { setDesignMode } from "../assembly/mode";
import { pinNumber } from "./pins";
import { viewTab } from "./Viewer";
import "./viewer.css";

export function FeedbackNote({ feedback, runId }: { feedback: FeedbackPin; runId: string | null }): JSX.Element {
  const n = pinNumber(runId, feedback);
  // the part as the app names it (the tree's label), else the label the core wrote down
  const known = parts.value.find((p) => p.part_id === feedback.part_id);
  const name = known ? partName(known) : (feedback.part ?? feedback.part_id);
  const near = feedback.nearby_hole ?? null;
  return (
    <>
      <div class="cv-text text">{feedback.text}</div>
      <button type="button" class="vx-thread-pin" data-feedback-pin title="Show this spot on the model"
        onClick={() => { setDesignMode("part"); viewTab.value = "model"; actions.selectPart(feedback.part_id); }}>
        <span class="vx-thread-flag" aria-hidden="true">{n ?? "•"}</span>
        {`${n === null ? "Comment" : `Comment ${n}`} · on ${name}${near ? ` · ${near}` : ""}`}
      </button>
    </>
  );
}
