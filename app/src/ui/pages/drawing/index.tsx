// Stub: Drawing page. Task 8 replaces this with the real drawing screen.
import type { JSX } from "preact";
import { ComingNext, PageShell } from "../PageShell";

export function DrawingPage(): JSX.Element {
  return (
    <PageShell title="Drawing">
      <ComingNext what="Dimensioned drawings with a title block" />
    </PageShell>
  );
}
