// Studies page: static stress studies of a local part (gmsh + CalculiX in the core).
// Left: part picker, solver install card, setup (presets, faces, force, direction, material) with
// Run / progress / Cancel, and the part's study history. Right: the shown study — design | stress
// split with a legend in MPa, the safety factor (with the fixture-including one and the
// peak-excludes note next to it), peaks, error band, solver / mesh / material label, "guide only",
// and "Ask agent to fix". Every result is a guide, not a guarantee.
import { computed, signal } from "@preact/signals";
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import { partLabel } from "../../../lib/names";
import { activePart } from "../../../state/selection";
import { actions, parts } from "../../../state/store";
import { PageShell } from "../PageShell";
import { setConsoleCollapsed } from "../router";
import { StudiesController } from "./controller";
import { History } from "./History";
import { InstallCard } from "./InstallCard";
import { DEFAULT_SETUP, MATERIALS, type Setup } from "./model";
import { Results } from "./Results";
import { SetupForm } from "./SetupForm";
import { coreGlb, SplitViewer, type GlbSource } from "./SplitViewer";
import "./studies.css";

/** One per app: leaving and re-opening the page keeps following a running study. */
export const studies = new StudiesController(() => actions.client());

/** The setup survives page switches (module state, like the controller). */
const setupSig = signal<Setup>(DEFAULT_SETUP);
let setupTouched = false;

/** Tests: back to the default setup. */
export function resetStudiesPage(): void {
  setupSig.value = DEFAULT_SETUP;
  setupTouched = false;
}

export interface StudiesDeps {
  glb?: GlbSource;
  /** The profile's default material (pre-selects the material once). */
  profileMaterial?: () => Promise<string | null>;
}

const defaultProfileMaterial = async (): Promise<string | null> => {
  try { return (await actions.client().getProfile()).default_material; } catch { return null; }
};

const builtParts = computed(() => parts.value.filter((p) => p.metrics !== null));

export function StudiesPage({ controller = studies, deps = {} }: { controller?: StudiesController; deps?: StudiesDeps }): JSX.Element {
  const c = controller;
  const part = activePart.value;
  const partId = part?.part_id ?? null;
  const built = part !== null && part.metrics !== null;
  const [checking, setChecking] = useState(false);

  useEffect(() => { void c.loadSolvers(); }, [c]);
  useEffect(() => { if (partId) void c.loadHistory(partId); }, [c, partId, part?.mtime]);
  useEffect(() => {
    if (setupTouched) return;
    void (deps.profileMaterial ?? defaultProfileMaterial)().then((m) => {
      if (!setupTouched && m && (MATERIALS as readonly string[]).includes(m)) setupSig.value = { ...setupSig.peek(), material: m };
    });
  }, []);

  const solvers = c.solvers.value;
  const active = c.active.value;
  const rec = c.record.value;
  const recShown = rec && rec.part_id === partId ? rec : null;
  const why = !partId ? "Build a part first: ask an agent on the Design page."
    : !built ? "This part has no built model yet."
      : c.ccxMissing.value ? "Install CalculiX to run studies." : null;
  const canRun = why === null && solvers !== null;

  const onAsk = async () => {
    if (!recShown) return;
    const runId = await c.ask(recShown);
    if (runId) { actions.selectRun(runId); setConsoleCollapsed(false); }
  };
  const asked = c.asked.value && recShown && c.asked.value.simId === recShown.sim_id ? c.asked.value : null;

  return (
    <PageShell title="Studies">
      <div class="st" data-testid="studies">
        <aside class="st-side">
          <div class="st-field">
            <label class="label" for="st-part">Part</label>
            {parts.value.length === 0
              ? <p class="faint" id="st-part">No part yet: ask an agent to create one on the Design page.</p>
              : (
                <select id="st-part" value={partId ?? ""} onChange={(e) => actions.selectPart((e.target as HTMLSelectElement).value)}>
                  {parts.value.map((p) => (
                    <option key={p.part_id} value={p.part_id} disabled={p.metrics === null}>
                      {partLabel(p.part_id)}{p.metrics === null ? " (not built)" : ""}
                    </option>
                  ))}
                </select>
              )}
          </div>
          {c.solversError.value && <p class="tone-red text">Could not read the solver status: {c.solversError.value}</p>}
          {solvers && (
            <InstallCard solvers={solvers} checking={checking} onRecheck={() => {
              setChecking(true);
              void c.loadSolvers().finally(() => setChecking(false));
            }} />
          )}
          <SetupForm
            setup={setupSig.value}
            onChange={(s) => { setupTouched = true; setupSig.value = s; }}
            onRun={() => { if (partId) void c.start(partId, setupSig.peek()); }}
            onCancel={() => { void c.cancel(); }}
            active={active && active.partId === partId ? active : null}
            canRun={canRun && (active === null)}
            why={active && active.partId !== partId ? `A study of ${partLabel(active.partId)} is running.` : why}
          />
          {c.runError.value && (
            <div class="st-band st-band-red" data-testid="run-error" role="alert">
              <div>
                <div class="text">{c.runError.value.message}</div>
                {c.runError.value.hint && <div class="text dim">{c.runError.value.hint}</div>}
              </div>
            </div>
          )}
          {partId && <History list={c.history.value} selected={c.selectedSimId.value} onSelect={(id) => { void c.select(id); }} error={c.historyError.value} />}
        </aside>
        <section class="st-main" aria-label="Study result">
          {builtParts.value.length === 0 && <p class="faint">No part yet. Studies run on parts built on the Design page.</p>}
          {c.recordError.value && <p class="tone-red text">Could not open the study: {c.recordError.value}</p>}
          {partId && built && recShown && recShown.status === "done" && (
            <SplitViewer partId={partId} mtime={part.mtime} simId={recShown.sim_id} range={recShown.color_range_mpa ?? null} glb={deps.glb ?? coreGlb} />
          )}
          {recShown && <Results rec={recShown} onAsk={() => { void onAsk(); }} asked={asked} />}
          {partId && built && !recShown && !c.recordError.value && c.history.value.length === 0 && (
            <p class="faint">Set up a study on the left and run it. Results are a guide, not a guarantee.</p>
          )}
        </section>
      </div>
    </PageShell>
  );
}
