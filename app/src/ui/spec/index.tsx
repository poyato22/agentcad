// The spec panel in the console and the design-study section of the Studies page. Both read
// one SpecController (module state), so a study started by the agent shows live in both. The
// console strip stays small (spec card plus a one-line study summary, at most ~35% of the
// console); the scatter, variants and table live on the Studies page.
import { signal } from "@preact/signals";
import type { JSX } from "preact";
import { useEffect } from "preact/hooks";
import { onCoreEvent } from "../../api/events";
import { partLabel } from "../../lib/names";
import { activePart } from "../../state/selection";
import { actions, conn } from "../../state/store";
import { Button } from "../common/Button";
import { setPage } from "../pages/router";
import type { Spec, Study } from "../../api/spec";
import { SpecController } from "./controller";
import { fmtNum, meetsSpec, reqViews, tally, tallyText } from "./model";
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
  const at = c.updatedAt.value;
  useEffect(() => { if (open) void c.load(); }, [c, open]);
  // requestCheck debounces and dedupes: both views mounted still check once per change
  useEffect(() => { if (open && spec) c.requestCheck(part?.part_id ?? null, part?.mtime ?? null); }, [c, open, spec, at, part?.part_id, part?.mtime]);
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
          {shown && <StudyLine study={shown} requirements={spec.requirements} onOpen={() => { c.selectStudy(shown.id); setPage("studies"); }} />}
          <SpecCard spec={spec} check={c.check.value} checkError={c.checkError.value} updatedAt={c.updatedAt.value} onSave={(s, b) => c.save(s, b)} compact />
        </div>
      )}
    </div>
  );
}

/** The console's one-line study summary: the best variant still meeting the spec now. */
function StudyLine({ study, requirements, onOpen }: { study: Study; requirements: Spec["requirements"]; onOpen: () => void }): JSX.Element {
  const ok = study.iterations.filter((i) => meetsSpec(requirements, i, study.geometry_only));
  const best = ok.find((i) => i.n === study.best_id) ?? [...ok].sort((a, b) => (a.metrics.mass_g ?? Infinity) - (b.metrics.mass_g ?? Infinity))[0];
  return (
    <div class="spp-line" data-study-line={study.id}>
      <span class="sp-title text">{partLabel(study.part_id)}</span>
      <span class="dim">{objectiveWords(study.objective)}</span>
      <span class="faint">{`${ok.length}/${study.iterations.length} meet`}</span>
      <span>{best?.metrics.mass_g != null ? `#${best.n} ${fmtNum(best.metrics.mass_g)} g${best.metrics.min_sf != null ? ` · SF ${best.metrics.min_sf.toFixed(2)}` : ""}` : "no variant meets the spec"}</span>
      <Button variant="ghost" onClick={onOpen}>Open in Studies</Button>
    </div>
  );
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
      {spec && <SpecCard spec={spec} check={c.check.value} checkError={c.checkError.value} updatedAt={c.updatedAt.value} onSave={(s, b) => c.save(s, b)} />}
      {studies.length === 0
        ? <p class="faint">No design studies in this build yet. Ask the engineer agent to design something; it searches the variables against the spec.</p>
        : (
          <div class="spd-grid">
            <ul class="spd-list" aria-label="Design study history">
              {studies.map((s) => {
                const best = s.iterations.find((i) => i.n === s.best_id);
                const sel = shown?.id === s.id;
                return (
                  <li key={s.id}>
                    <button type="button" class="spd-row" data-study-row={s.id} aria-current={sel} onClick={() => c.selectStudy(s.id)}>
                      <span class="spd-row-1">
                        <span class="sp-title text">{partLabel(s.part_id)}</span>
                        <span class="dim">{objectiveWords(s.objective)}</span>
                        <StudyStatus study={s} total={c.progress.value.get(s.id)?.total} />
                      </span>
                      <span class="spd-row-2 faint">
                        <span>{new Date(s.created_at * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
                        <span class="sp-num">{best?.metrics.mass_g != null ? `best #${best.n} · ${fmtNum(best.metrics.mass_g)} g${best.metrics.min_sf != null ? ` · SF ${best.metrics.min_sf.toFixed(2)}` : ""}` : "no feasible variant"}</span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
            {shown && <StudyView c={c} study={shown} requirements={spec?.requirements ?? []} />}
          </div>
        )}
    </section>
  );
}
