// Stub: Print page. A later task replaces this with the real slice/print screen.
import type { JSX } from "preact";
import { ComingNext, PageShell } from "../PageShell";

export function PrintPage(): JSX.Element {
  return (
    <PageShell title="Print">
      <ComingNext what="Slice and print (material, time, grams, cost) with the print-safety card" />
    </PageShell>
  );
}
