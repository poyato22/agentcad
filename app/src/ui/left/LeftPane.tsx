// The left surface of the build view (Plan 9 D9): the build's parts tree, every row with its mass
// (groups their sum) and the total at the bottom, then the BOM summary (item count, cost, a link to
// the BOM page) — in place of History. A click on a part opens it alone (D7); a delete leaves an
// Undo toast here for a few seconds (D29).
import type { JSX } from "preact";
import { useEffect } from "preact/hooks";
import { parts } from "../../state/store";
import { BomSummary } from "./BomSummary";
import "./left.css";
import { fmtMass, totalMass } from "./mass";
import { PartTree } from "./PartTree";
import { assemblyOf, lastDeleted, undoDelete } from "./treeActions";
import { assemblyMassTitle } from "../../lib/mass";

/** How long the Undo toast stays. */
export const UNDO_MS = 8000;

function UndoToast(): JSX.Element | null {
  const d = lastDeleted.value;
  useEffect(() => {
    if (!d) return;
    const t = setTimeout(() => { if (lastDeleted.peek() === d) lastDeleted.value = null; }, UNDO_MS);
    return () => clearTimeout(t);
  }, [d]);
  if (!d) return null;
  return (
    <div class="left-toast" role="status" data-testid="undo-toast">
      <span class="left-toast-text">{d.text}</span>
      <button type="button" class="left-link" onClick={() => { void undoDelete(); }}>Undo</button>
    </div>
  );
}

export function LeftPane(): JSX.Element {
  const list = parts.value;
  const counted = list.some((p) => p.kind !== "skeleton");
  // one total for the build (FW-app M7: the viewer no longer repeats it): its assembly's, every
  // instance x its part's mass, once loaded with something in it; else the tree's sum
  const asm = assemblyOf.value;
  const asmG = asm && asm.instances.length > 0 && typeof asm.total_mass_g === "number" ? asm.total_mass_g : null;
  const tree = totalMass(list);
  const total = asmG !== null ? { g: asmG, unknown: (asm?.mass_unknown ?? []).reduce((n, u) => n + u.count, 0) } : tree;
  const title = asmG !== null ? assemblyMassTitle(asm?.mass_unknown ?? [])
    : tree.unknown ? `${tree.unknown} part${tree.unknown === 1 ? "" : "s"} without a mass yet` : undefined;
  return (
    <div class="left-pane" data-testid="left-pane">
      <section class="left-parts" aria-label="Parts">
        <header class="left-sec-head"><span class="left-sec-title">Parts</span></header>
        <div class="left-parts-body pane-body"><PartTree /></div>
        {counted && (
          <div class="left-total" data-testid="parts-total" title={title}>
            <span>Total</span>
            <span class="num">{fmtMass(total.g)}{total.unknown ? <span class="dim"> +{total.unknown} unknown</span> : null}</span>
          </div>
        )}
      </section>
      <BomSummary />
      <UndoToast />
    </div>
  );
}
