// The design-study section of the Studies page (P10 E12: the spec strip left the dock; the
// requirements live here). It reads one SpecController (module state), so a study started by the
// agent shows live; the scatter, variants and table are on the Studies page.
import { effect } from "@preact/signals";
import type { JSX } from "preact";
import { useEffect } from "preact/hooks";
import { onCoreEvent } from "../../api/events";
import { partLabel } from "../../lib/names";
import { activeBuildId, onBuildSwitch } from "../../state/builds";
import { activePart } from "../../state/selection";
import { actions, conn } from "../../state/store";
import { SpecController } from "./controller";
import { fmtNum } from "./model";
import { SpecCard } from "./SpecCard";
import { objectiveWords, StudyStatus, StudyView } from "./StudyView";
import "./spec.css";

export { SpecController } from "./controller";

/** One per app. */
export const specs = new SpecController(() => actions.client());
// A3's build tabs: the spec panel follows the active build once it has loaded one
onBuildSwitch((id) => { if (specs.projectId.peek() !== null && specs.projectId.peek() !== id) void specs.load(id); });
// a load that waited for the builds runs once the active build is known
effect(() => { const id = activeBuildId.value; if (id !== null && specs.waitingForBuild) void specs.load(id); });
onCoreEvent((ev) => specs.onEvent(ev));

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
