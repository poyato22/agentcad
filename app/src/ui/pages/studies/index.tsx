// Stub: Studies page. Task 6 replaces this with the real stress/study screen.
import type { JSX } from "preact";
import { ComingNext, PageShell } from "../PageShell";

export function StudiesPage(): JSX.Element {
  return (
    <PageShell title="Studies">
      <ComingNext what="Simulation studies (stress, modal) with solver, mesh and error band" />
    </PageShell>
  );
}
