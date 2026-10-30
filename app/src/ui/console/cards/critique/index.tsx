// Plan 8 (G4) thread cards: a render-and-critique round, or a view an agent presents. The
// conversation's one hook into this folder.
import type { JSX } from "preact";
import type { ThreadItem } from "../../threadModel";
import { CritiqueCard } from "./CritiqueCard";
import { ViewCard } from "./ViewCard";

export { ViewImmersiveRoot } from "./ViewImmersive";
export { ModelWarning } from "./ModelWarning";

export function G4Card({ item }: { item: Extract<ThreadItem, { kind: "critique" | "view" }> }): JSX.Element {
  return (
    <div class="cv-card cv-g4" data-row data-kind={item.kind}>
      {item.kind === "critique" ? <CritiqueCard round={item.round} /> : <ViewCard view={item.view} group={item.group} />}
    </div>
  );
}
