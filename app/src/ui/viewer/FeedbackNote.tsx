// A feedback pin in the thread (Plan 7 U2a): the user's words, then "Comment 2 · on Mount plate"
// instead of the raw location block; the chip shows the part with its pins.
import type { JSX } from "preact";
import { setDesignMode } from "../assembly/mode";
import { partName } from "../../lib/names";
import { actions, parts } from "../../state/store";
import { buildPins, type Feedback } from "./pins";
import { viewTab } from "./Viewer";
import "./viewer.css";

export function FeedbackNote({ feedback, stepId }: { feedback: Feedback; stepId: number | null }): JSX.Element {
  const loc = feedback.location;
  const n = buildPins.value.find((p) => p.stepId !== null && p.stepId === stepId)?.n ?? null;
  // the part as the app names it (the tree's label), else the name the core wrote down
  const known = parts.value.find((p) => p.part_id === loc.partId);
  const name = known ? partName(known) : loc.part;
  return (
    <>
      <div class="cv-text text">{feedback.text}</div>
      <button type="button" class="vx-thread-pin" data-feedback-pin title="Show this spot on the model"
        onClick={() => { setDesignMode("part"); viewTab.value = "model"; actions.selectPart(loc.partId); }}>
        <span class="vx-thread-flag" aria-hidden="true">{n ?? "•"}</span>
        {`${n === null ? "Comment" : `Comment ${n}`} · on ${name}${loc.nearby ? ` · ${loc.nearby}` : ""}`}
      </button>
    </>
  );
}
