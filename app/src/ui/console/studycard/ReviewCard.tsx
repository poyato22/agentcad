// A design_review result inline in the thread: the overall verdict and a requirement-by-
// requirement table (target, measured, pass / warn / fail, note); a row answered by a study links
// to that study's immersive view, and ⌘[ / ⌘] there step through the review's other studies.
import type { JSX } from "preact";
import { Button } from "../../common/Button";
import { Led } from "../../common/Led";
import { openImmersive } from "../../pages/studies/immersive/state";
import { fmtValue, typeName, VERDICT_TEXT, verdictTone } from "../../pages/studies/model";
import { kindText, type ReviewCallView } from "./cardModel";
import "./studycard.css";

export function ReviewCard({ view }: { view: ReviewCallView }): JSX.Element {
  const r = view.review;
  const ids = r ? [...new Set(r.studies.map((s) => s.sim_id).filter((x): x is string => typeof x === "string"))] : [];
  const open = (simId: string) => openImmersive({ simId, ids, origin: { agent: null, runId: view.runId, reason: "design review" } });
  const verdict = r?.verdict ?? (view.failed ? "fail" : null);
  return (
    <article class="sk sk-review" data-testid="review-card" data-verdict={verdict ?? "none"} aria-label={`Design review${view.partId ? ` of ${view.partId}` : ""}`}>
      <header class="sk-head">
        <span class="sk-type">Design review</span>
        {view.partId && <span class="sk-part text">{view.partId}</span>}
        <span class="sk-status">
          {view.running ? <Led tone="blue" title="running" /> : <Led tone={verdictTone(verdict)} />}
          <span class={`sk-verdict${verdict ? ` tone-${verdictTone(verdict)}` : ""}`}>{view.running ? "running" : verdict ? VERDICT_TEXT[verdict] : "done"}</span>
        </span>
      </header>
      <p class="sk-fidelity"><span class="sk-guide">GUIDE ONLY</span><span class="faint text">{r?.label ?? "guide only, not a guarantee"}</span></p>
      {view.running && <p class="faint text">Running the studies the spec needs…</p>}
      {view.failed && <p class="sk-fail text" role="alert">{view.summary ?? "the review failed"}{view.hint ? <span class="faint"> · {view.hint}</span> : null}</p>}
      {r && (
        <table class="sk-table" data-testid="review-table">
          <thead><tr><th>requirement</th><th>target</th><th>measured</th><th>result</th><th><span class="sr-only">study</span></th></tr></thead>
          <tbody>
            {r.table.map((row, i) => (
              <tr key={`${row.requirement_id ?? i}-${row.case ?? ""}`} data-testid="review-row" data-status={row.status} data-sim={row.sim_id ?? ""}>
                <td class="text">{row.requirement_id ? <span class="faint">{row.requirement_id} </span> : null}{kindText(row.kind)}{row.study ? <span class="faint"> · {typeName(row.study)}{row.case && row.case !== row.study ? ` ${row.case}` : ""}</span> : null}
                  {row.note && <div class="faint text sk-row-note">{row.note}</div>}
                </td>
                <td>{fmtValue(row.target, row.unit)}</td>
                <td>{fmtValue(row.measured, row.unit)}</td>
                <td><Led tone={verdictTone(row.status)} /> <span class={`sk-verdict tone-${verdictTone(row.status)}`}>{VERDICT_TEXT[row.status]}</span></td>
                <td>{row.sim_id && <Button variant="ghost" onClick={() => open(row.sim_id as string)} aria-label={`Immersive view of the study for ${row.requirement_id ?? kindText(row.kind)}`}>view</Button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {r && r.studies.some((s) => s.error) && (
        <ul class="sk-checks">{r.studies.filter((s) => s.error).map((s, i) => <li key={i}><Led tone="red" /><span class="text">{typeName(s.type)} {s.case ?? ""}: {s.error}</span></li>)}</ul>
      )}
      {ids.length > 0 && (
        <footer class="sk-actions">
          <Button onClick={() => open(ids[0])} data-testid="open-immersive">Immersive</Button>
          <span class="faint">{ids.length} stud{ids.length === 1 ? "y" : "ies"} · ⌘[ ⌘] to step through</span>
        </footer>
      )}
    </article>
  );
}
