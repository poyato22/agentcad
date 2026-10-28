// The part's studies, newest first. Rows read "Study · <setup>" (no counters); the LED is the
// verdict (pass/warn/fail), red for a failed study, faint otherwise. The shown row is selected (blue).
import type { JSX } from "preact";
import type { SimSummary } from "../../../api/sims";
import { fmtClock } from "../../../lib/format";
import { Led, type LedTone } from "../../common/Led";
import { fmtSf, sfVerdict, studyLabel, verdictTone } from "./model";

function tone(s: SimSummary): LedTone {
  if (s.status === "done") return verdictTone(sfVerdict(s.results?.safety_factor));
  if (s.status === "failed" || s.status === "interrupted") return "red";
  return "faint";
}

function right(s: SimSummary): string {
  if (s.status === "done") return `SF ${fmtSf(s.results?.safety_factor)}`;
  return s.status;
}

export function History({ list, selected, onSelect, error }: {
  list: readonly SimSummary[]; selected: string | null; onSelect: (simId: string) => void; error: string | null;
}): JSX.Element {
  return (
    <section class="st-history" aria-label="Studies of this part">
      <h2 class="label">History</h2>
      {error && <p class="tone-red text">{error}</p>}
      {list.length === 0 && !error && <p class="faint">No studies of this part yet.</p>}
      <ul>
        {list.map((s) => (
          <li key={s.sim_id}>
            <button type="button" class="st-row-btn" data-testid="study-row" aria-current={s.sim_id === selected ? "true" : undefined}
              title={`${fmtClock(s.created_at)}${s.stale ? " · the part changed since" : ""}`} onClick={() => onSelect(s.sim_id)}>
              <Led tone={tone(s)} />
              <span class="st-row-label text">{studyLabel(s, list)}</span>
              <span class="st-row-right faint">{right(s)}{s.stale ? " · stale" : ""}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
