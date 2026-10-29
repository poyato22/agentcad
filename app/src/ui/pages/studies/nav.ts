// "Open in Studies" from anywhere (a study card in the thread, the immersive view): switch to the
// Studies page, select the study's part and show that study. The page takes the request when it
// mounts or when the request changes.
import { batch, signal, type Signal } from "@preact/signals";
import { actions } from "../../../state/store";
import { setPage } from "../router";

export const showStudyRequest: Signal<{ partId: string; simId: string } | null> = signal(null);

export function showInStudies(partId: string, simId: string): void {
  batch(() => {
    actions.selectPart(partId);
    showStudyRequest.value = { partId, simId };
    setPage("studies");
  });
}
