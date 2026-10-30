// Two or more studies side by side (POST /sims/compare): one column per study, one row per metric
// any of them has; the best value of a row (by the core's "better" rule) is marked. Guide only.
import type { JSX } from "preact";
import type { StudyCompare } from "../../../api/studies";
import { fmtClock } from "../../../lib/format";
import { Button } from "../../common/Button";
import { fmtValue, typeName } from "./model";

export function Compare({ data, onClose, onOpen }: { data: StudyCompare; onClose: () => void; onOpen: (simId: string) => void }): JSX.Element {
  return (
    <section class="st-compare" aria-label="Compare studies" data-testid="compare-view">
      <header class="st-compare-head">
        <h2 class="label">Compare</h2>
        <span class="st-guide-tag">GUIDE ONLY</span>
        <span class="faint text">{data.label}</span>
        <Button variant="ghost" onClick={onClose}>Close</Button>
      </header>
      <div class="st-compare-scroll">
        <table class="st-ctable">
          <thead>
            <tr>
              <th scope="col"><span class="faint">metric</span></th>
              {data.columns.map((c) => (
                <th key={c.sim_id} scope="col">
                  <button type="button" class="scard-link" onClick={() => onOpen(c.sim_id)}>{typeName(c.type)}</button>
                  <div class="faint">{c.part_id} · {c.material ?? "—"}{c.created_at ? ` · ${fmtClock(c.created_at)}` : ""}</div>
                  <div class="faint">{c.fidelity_class}{c.stale ? " · stale" : ""}</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r) => (
              <tr key={r.key} data-testid="compare-row" data-key={r.key}>
                <th scope="row" class="text">{r.label}</th>
                {r.values.map((v, i) => (
                  <td key={i} data-best={r.best === i ? "true" : undefined}>
                    {fmtValue(v, r.key === "error_band_pct" ? "%" : r.unit)}{r.best === i && <span class="st-best" aria-label="best"> ◆</span>}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p class="faint st-note">◆ the best value of the row. Different study types answer different rows; n/a is a metric that study does not report.</p>
    </section>
  );
}
