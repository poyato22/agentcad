// The left surface's BOM section (Plan 9 D9, in place of History): how many items the build's
// parts list holds and what they cost (per currency, never summed across), with a link to the BOM
// page. Re-read when the parts list or the parts change, and on a build switch.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import type { Bom } from "../../api/browser";
import { onCoreEvent } from "../../api/events";
import { activeBuildId } from "../../state/builds";
import { actions, parts } from "../../state/store";
import { perCurrency } from "../pages/bom/model";
import { setPage } from "../pages/router";

/** "14 items · $212.00", "No items yet". */
export function bomLine(bom: Bom | null): { items: string; cost: string | null } {
  if (!bom) return { items: "—", cost: null };
  const n = bom.totals.qty;
  if (n === 0) return { items: "No items yet", cost: null };
  const cost = Object.keys(bom.totals.by_currency).length ? perCurrency(bom.totals.by_currency) : null;
  return { items: `${n} item${n === 1 ? "" : "s"}`, cost };
}

export function BomSummary(): JSX.Element {
  const build = activeBuildId.value;
  const [bom, setBom] = useState<Bom | null>(null);
  const [tick, setTick] = useState(0);
  const partsNow = parts.value;
  useEffect(() => {
    if (!build) { setBom(null); return; }
    let live = true;
    Promise.resolve().then(() => actions.client().bom(build))
      .then((b) => { if (live) setBom(b); }, () => { if (live) setBom(null); });
    return () => { live = false; };
  }, [build, tick, partsNow]);
  useEffect(() => onCoreEvent((ev) => {
    if (ev.type === "bom_changed" && ev.data.project_id === activeBuildId.peek()) setTick((t) => t + 1);
  }), []);
  const line = bomLine(bom);
  return (
    <section class="left-bom" aria-label="BOM" data-testid="bom-summary">
      <header class="left-sec-head">
        <span class="left-sec-title">BOM</span>
        <button type="button" class="left-link" onClick={() => setPage("bom")} title="The build's parts list (BOM page)">Open</button>
      </header>
      <button type="button" class="left-bom-line" onClick={() => setPage("bom")} aria-label={`BOM: ${line.items}${line.cost ? `, ${line.cost}` : ""}`}>
        <span>{line.items}</span>
        {line.cost && <span class="num" data-testid="bom-cost">{line.cost}</span>}
      </button>
    </section>
  );
}
