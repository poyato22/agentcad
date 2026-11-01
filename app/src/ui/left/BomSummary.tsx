// The left surface's BOM section (Plan 9 D9, in place of History): how many items the build's
// parts list holds and what they cost (per currency, never summed across), with a link to the BOM
// page. Read by the left surface (bomData): when the parts list or the parts change, and on a build switch.
import type { JSX } from "preact";
import type { Bom } from "../../api/browser";
import { perCurrency } from "../pages/bom/model";
import { setPage } from "../pages/router";
import { buildBom } from "./bomData";

/** "14 items · $212.00", "No items yet". */
export function bomLine(bom: Bom | null): { items: string; cost: string | null } {
  if (!bom) return { items: "—", cost: null };
  const n = bom.totals.qty;
  if (n === 0) return { items: "No items yet", cost: null };
  const cost = Object.keys(bom.totals.by_currency).length ? perCurrency(bom.totals.by_currency) : null;
  return { items: `${n} item${n === 1 ? "" : "s"}`, cost };
}

export function BomSummary(): JSX.Element {
  // the left surface reads the build's BOM once (bomData; the Bought parts group shares it)
  const line = bomLine(buildBom.value);
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
