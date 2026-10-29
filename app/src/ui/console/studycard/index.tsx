// The hook the console's tool-call rows use: a study call (sim_run, sim_static_run) renders a
// study card under its line, a design_review a review card; any other call renders nothing.
// Kept in its own folder so the console's own files only gain one import and one line.
import type { JSX } from "preact";
import type { LogItem } from "../logModel";
import { studyCallView } from "./cardModel";
import { ReviewCard } from "./ReviewCard";
import { StudyCard } from "./StudyCard";

export { isStudyCallName } from "./cardModel";

type Call = Extract<LogItem, { kind: "call" }>;

export function StudyCardSlot({ call, runId }: { call: Call; runId?: string | null }): JSX.Element | null {
  const v = studyCallView(call, runId ?? call.runId ?? null);
  if (!v) return null;
  return <div class="scard-slot" data-study-slot>{v.kind === "review" ? <ReviewCard view={v} /> : <StudyCard view={v} />}</div>;
}
