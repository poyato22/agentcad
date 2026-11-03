// Plan 13 H4: the app's one whole-assembly studies controller and the Studies page's target
// (a part, or the whole assembly), apart from the page so the Simulations stream's cards can open
// a study there without importing the page.
import { batch, signal } from "@preact/signals";
import { onCoreEvent } from "../../../../api/events";
import { actions } from "../../../../state/store";
import { setPage } from "../../router";
import { AssemblyStudiesController } from "./controller";

export const assemblyStudies = new AssemblyStudiesController(() => actions.client());
onCoreEvent((ev) => assemblyStudies.onEvent(ev));

/** What the Studies page studies: one part, or the whole assembly as a system. */
export const studyTarget = signal<"part" | "assembly">("part");

/** "Open in Studies" for a whole-assembly study (a Simulations-stream card). */
export function showAssemblyStudy(studyId: string): void {
  batch(() => { studyTarget.value = "assembly"; setPage("studies"); });
  void assemblyStudies.select(studyId);
}
