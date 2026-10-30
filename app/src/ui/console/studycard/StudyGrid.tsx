// Several studies of one agent turn as one grid of compact cards (R18), instead of a card under
// each tool-call line. Each card keeps its honesty labels and opens inline (an open card spans
// the row); the immersive view steps through the turn's studies with ⌘[ / ⌘]. The raw tool
// input/output stays behind one "Details" toggle.
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import type { LogItem } from "../logModel";
import { CallDetails } from "../ToolCallRow";
import { studyCallView, type ReviewCallView, type StudyCallView } from "./cardModel";
import { ReviewCard } from "./ReviewCard";
import { StudyCard } from "./StudyCard";
import "./studycard.css";

type Call = Extract<LogItem, { kind: "call" }>;

function summary(views: readonly (StudyCallView | ReviewCallView)[]): string {
  const n = views.length;
  const running = views.filter((v) => v.running).length;
  const failed = views.filter((v) => v.failed).length;
  const verdicts = views.map((v) => (v.kind === "study" ? v.card?.verdict : v.review?.verdict) ?? null);
  const bits = [`${n} stud${n === 1 ? "y" : "ies"}`];
  if (running) bits.push(`${running} running`);
  const pass = verdicts.filter((x) => x === "pass").length;
  const warn = verdicts.filter((x) => x === "warn").length;
  const fail = verdicts.filter((x) => x === "fail").length + failed;
  if (pass) bits.push(`${pass} pass`);
  if (warn) bits.push(`${warn} warn`);
  if (fail) bits.push(`${fail} fail`);
  return bits.join(" · ");
}

export function StudyGrid({ calls, runId }: { calls: readonly Call[]; runId?: string | null }): JSX.Element {
  const [details, setDetails] = useState(false);
  const views = calls.map((c) => ({ call: c, view: studyCallView(c, runId ?? c.runId ?? null) }))
    .filter((x): x is { call: Call; view: StudyCallView | ReviewCallView } => x.view !== null);
  const siblings = views.flatMap((x) => (x.view.kind === "study" && x.view.simId ? [x.view.simId] : []));
  return (
    <div class="scard-group" data-row data-kind="studies" data-study-group={views.length}>
      <div class="scard-group-head">
        <span class="text dim" data-group-summary>{summary(views.map((x) => x.view))}</span>
        <button type="button" class="scard-more faint" aria-expanded={details} onClick={() => setDetails(!details)}>
          {details ? "Hide details" : "Details"}
        </button>
      </div>
      <div class="scard-grid" data-testid="study-grid">
        {views.map(({ call, view }) => (
          <div key={call.callId} class="scard-slot" data-study-slot>
            {view.kind === "review" ? <ReviewCard view={view} /> : <StudyCard view={view} siblings={siblings} />}
          </div>
        ))}
      </div>
      {details && (
        <div class="scard-group-details">
          {views.map(({ call }) => <CallDetails key={call.callId} item={call} class="cv-details" />)}
        </div>
      )}
    </div>
  );
}
