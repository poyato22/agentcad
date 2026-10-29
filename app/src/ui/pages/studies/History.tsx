// The part's studies, newest first, every type. Rows read "<Type> · <setup>" (no counters); the
// LED is the verdict by the study's own numbers (red for a failed study). The shown row is
// selected (blue). Each row has a compare checkbox; two or more ticked compare side by side.
import type { JSX } from "preact";
import type { StudySummary } from "../../../api/studies";
import { fmtClock } from "../../../lib/format";
import { Button } from "../../common/Button";
import { Led } from "../../common/Led";
import { studyLabel, summaryHeadline, summaryTone } from "./model";

export function History({ list, selected, onSelect, error, compare, onToggleCompare, onCompare }: {
  list: readonly StudySummary[]; selected: string | null; onSelect: (simId: string) => void; error: string | null;
  compare: readonly string[]; onToggleCompare: (simId: string) => void; onCompare: () => void;
}): JSX.Element {
  return (
    <section class="st-history" aria-label="Studies of this part">
      <div class="st-history-head">
        <h2 class="label">History</h2>
        {list.length > 1 && (
          <Button variant="ghost" disabled={compare.length < 2} onClick={onCompare} data-testid="compare">
            {compare.length < 2 ? "Tick two to compare" : `Compare ${compare.length}`}
          </Button>
        )}
      </div>
      {error && <p class="tone-red text">{error}</p>}
      {list.length === 0 && !error && <p class="faint">No studies of this part yet.</p>}
      <ul>
        {list.map((s) => {
          const label = studyLabel(s, list);
          return (
            <li key={s.sim_id} class="st-hrow">
              <input type="checkbox" class="st-cmp" checked={compare.includes(s.sim_id)} disabled={s.status !== "done"}
                aria-label={`Compare ${label}`} data-testid="compare-check" onChange={() => onToggleCompare(s.sim_id)} />
              <button type="button" class="st-row-btn" data-testid="study-row" data-type={s.type ?? "static"} aria-current={s.sim_id === selected ? "true" : undefined}
                title={`${fmtClock(s.created_at)}${s.stale ? " · the part changed since" : ""}`} onClick={() => onSelect(s.sim_id)}>
                <Led tone={summaryTone(s)} />
                <span class="st-row-label text">{label}</span>
                <span class="st-row-right faint">{summaryHeadline(s)}{s.stale ? " · stale" : ""}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
