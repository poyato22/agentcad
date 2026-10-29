// The spec panel in the console and the design-study section of the Studies page. Both read
// one SpecController (module state), so a study started by the agent shows live in both.
import { signal } from "@preact/signals";
import type { JSX } from "preact";
import { useEffect } from "preact/hooks";
import { onCoreEvent } from "../../api/events";
import { partLabel } from "../../lib/names";
import { activePart } from "../../state/selection";
import { actions, conn } from "../../state/store";
import { SpecController } from "./controller";
import { fmtNum, reqViews, tally, tallyText } from "./model";
import { SpecCard } from "./SpecCard";
import { objectiveWords, StudyStatus, StudyView } from "./StudyView";
import "./spec.css";

export { SpecController } from "./controller";

/** One per app. */
export const specs = new SpecController(() => actions.client());
onCoreEvent((ev) => specs.onEvent(ev));

/** Whether the console's spec strip is open (survives run and page switches). */
export const specPanelOpen = signal(false);

/** Load when the core connects (and reconnects); re-check when the part, its build or the spec
 *  changes. */
function useSpecData(c: SpecController): void {
  const open = conn.value === "open";
  const part = activePart.value;
  const spec = c.spec.value;
  useEffect(() => { if (open) void c.load(); }, [c, open]);
  useEffect(() => { if (open && spec) void c.runCheck(part?.part_id ?? null); }, [c, open, spec, part?.part_id, part?.mtime]);
}

export function SpecPanel({ c = specs }: { c?: SpecController }): JSX.Element | null {
  useSpecData(c);
  const spec = c.spec.value;
  const studies = c.studies.value;
  if (!spec || (spec.requirements.length === 0 && spec.load_cases.length === 0 && studies.length === 0)) return null;
  const open = specPanelOpen.value;
  const t = tally(reqViews(spec, c.check.value?.requirements ?? null));
  const latest = studies[0] ?? null;
  const shown = studies.find((s) => s.id === c.selectedStudyId.value) ?? latest;
  return (
    <div class="spp" data-spec-panel data-open={open ? "" : undefined}>
      <button type="button" class="spp-bar" aria-expanded={open} onClick={() => { specPanelOpen.value = !open; }}>
        <span aria-hidden="true">{open ? "▾" : "▸"}</span>
        <span class="sp-title">Spec</span>
        <span class="dim">{tallyText(t)}</span>
        {latest && <span class="spp-study"><span class="faint">study</span><StudyStatus study={latest} total={c.progress.value.get(latest.id)?.total} /></span>}
      </button>
      {open && (
        <div class="spp-body">
          <SpecCard spec={spec} check={c.check.value} checkError={c.checkError.value} onSave={(s) => c.save(s)} compact />
          {studies.length > 1 && (
            <select class="spp-pick" aria-label="Design study" value={shown?.id ?? ""} onChange={(e) => c.selectStudy((e.currentTarget as HTMLSelectElement).value)}>
              {studies.map((s) => <option key={s.id} value={s.id}>{studyOption(s)}</option>)}
            </select>
          )}
          {shown && <StudyView c={c} study={shown} requirements={spec.requirements} compact />}
        </div>
      )}
    </div>
  );
}

function studyOption(s: { part_id: string; objective: string; iterations: unknown[]; created_at: number }): string {
  const when = new Date(s.created_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return `${when} · ${partLabel(s.part_id)} · ${objectiveWords(s.objective)} · ${s.iterations.length} evals`;
}

/** Studies page section: the build's spec, its design-study history, and the shown study. */
export function DesignStudies({ c = specs }: { c?: SpecController }): JSX.Element {
  useSpecData(c);
  const spec = c.spec.value;
  const studies = c.studies.value;
  const shown = studies.find((s) => s.id === c.selectedStudyId.value) ?? studies[0] ?? null;
  return (
    <section class="spd" data-design-studies aria-label="Design studies">
      <h2 class="spd-h">Design studies</h2>
      {c.loadError.value && <p class="tone-red text">{`Could not load the spec: ${c.loadError.value}`}</p>}
      {spec && <SpecCard spec={spec} check={c.check.value} checkError={c.checkError.value} onSave={(s) => c.save(s)} />}
      {studies.length === 0
        ? <p class="faint">No design studies in this build yet. Ask the engineer agent to design something; it searches the variables against the spec.</p>
        : (
          <div class="spd-grid">
            <table class="sp-table spd-list" aria-label="Design study history">
              <thead><tr><th>started</th><th>part</th><th>goal</th><th>status</th><th class="sp-num">best</th></tr></thead>
              <tbody>
                {studies.map((s) => {
                  const best = s.iterations.find((i) => i.n === s.best_id);
                  const sel = shown?.id === s.id;
                  return (
                    <tr key={s.id} data-study-row={s.id} aria-selected={sel} class={sel ? "sp-sel" : undefined} tabIndex={0}
                      onClick={() => c.selectStudy(s.id)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); c.selectStudy(s.id); } }}>
                      <td class="faint">{new Date(s.created_at * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</td>
                      <td class="text">{partLabel(s.part_id)}</td>
                      <td class="text">{objectiveWords(s.objective)}</td>
                      <td><StudyStatus study={s} total={c.progress.value.get(s.id)?.total} /></td>
                      <td class="sp-num">{best?.metrics.mass_g != null ? `#${best.n} ${fmtNum(best.metrics.mass_g)} g${best.metrics.min_sf != null ? ` SF ${best.metrics.min_sf.toFixed(2)}` : ""}` : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {shown && <StudyView c={c} study={shown} requirements={spec?.requirements ?? []} />}
          </div>
        )}
    </section>
  );
}
