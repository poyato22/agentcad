// Studies page: every study type with full control. Left: the part, the study type (Static,
// Modal, Buckling, Thermal, Drop, Wind), templates, the settings form generated from the type's
// schema (load cases, supports, materials, mesh, solver, an Advanced fold), Run with progress by
// phase and Cancel, and the part's history with compare. Right: the viewport (the setup with its
// load markers and face picking, or the result with channels, legend range, deformation, mode
// shapes, airflow, section and probe), the result (metrics, checks, charts, "guide only" and the
// fidelity note always visible), compare and the material editor. Every result is a guide only.
import { computed, signal } from "@preact/signals";
import type { JSX } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import { onCoreEvent } from "../../../api/events";
import type { StudyType } from "../../../api/studies";
import { partLabel } from "../../../lib/names";
import { activePart } from "../../../state/selection";
import { actions, parts } from "../../../state/store";
import { Button } from "../../common/Button";
import { PhaseBar } from "../../console/studycard/StudyCard";
import { PageShell } from "../PageShell";
import { setConsoleCollapsed } from "../router";
import { Compare } from "./Compare";
import { StudiesController } from "./controller";
import { History } from "./History";
import { openImmersive } from "./immersive/state";
import { InstallCard } from "./InstallCard";
import { MaterialEditor, Templates } from "./Library";
import { phaseText, settingsMarkers, STARTERS, TYPE_NAMES, TYPE_ORDER } from "./model";
import { showStudyRequest } from "./nav";
import { ResultPanel } from "./ResultPanel";
import { SchemaForm } from "./SchemaForm";
import { setAt, withDefaults, type Path } from "./formSchema";
import { StudyView, type StudyViewDeps } from "./StudyView";
import "./studies.css";
import "./studies-a8.css";

/** One per app: leaving and re-opening the page keeps following a running study. */
export const studies = new StudiesController(() => actions.client());
onCoreEvent((ev) => studies.onEvent(ev));

// the setup survives page switches (module state, like the controller)
const typeSig = signal<StudyType>("static");
const settingsSig = signal<Partial<Record<StudyType, Record<string, unknown>>>>({});
const caseSig = signal<number | null>(0);
const viewSig = signal<"setup" | "result">("result");

/** Tests: back to a fresh setup. */
export function resetStudiesPage(): void {
  typeSig.value = "static"; settingsSig.value = {}; caseSig.value = 0; viewSig.value = "result";
}

export interface StudiesDeps { view?: StudyViewDeps }

const builtParts = computed(() => parts.value.filter((p) => p.metrics !== null));

export function StudiesPage({ controller = studies, deps = {} }: { controller?: StudiesController; deps?: StudiesDeps }): JSX.Element {
  const c = controller;
  const part = activePart.value;
  const partId = part?.part_id ?? null;
  const built = part !== null && part.metrics !== null;
  const [checking, setChecking] = useState(false);
  const [picking, setPicking] = useState<Path | null>(null);
  const [panel, setPanel] = useState<"none" | "materials" | "compare">("none");

  useEffect(() => { void c.loadTypes(); void c.loadSolvers(); void c.loadLibrary(); }, [c]);
  useEffect(() => { c.resume(); return () => c.pause(); }, [c]);
  useEffect(() => { if (partId) void c.loadHistory(partId); }, [c, partId, part?.mtime]);
  // "Open in Studies" from a card or the immersive view
  const req = showStudyRequest.value;
  useEffect(() => {
    if (!req || req.partId !== partId) return;
    showStudyRequest.value = null;
    viewSig.value = "result";
    void c.loadHistory(req.partId).then(() => c.select(req.simId));
  }, [req, partId]);

  const type = typeSig.value;
  const info = c.types.value.find((t) => t.id === type) ?? null;
  const settings = useMemo(
    () => (info ? settingsSig.value[type] ?? withDefaults(info.schema, STARTERS[type]) : null),
    [info, settingsSig.value, type],
  );
  const setSettings = (s: Record<string, unknown>) => { settingsSig.value = { ...settingsSig.peek(), [type]: s }; viewSig.value = "setup"; };
  const markers = useMemo(() => (settings ? settingsMarkers(type, settings) : []), [type, settings]);
  const faces = part?.metrics?.face_list ?? [];
  const materialNames = useMemo(() => [...(c.materials.value?.builtin ?? []), ...(c.materials.value?.custom ?? [])].map((m) => m.name), [c.materials.value]);

  const solvers = c.solvers.value;
  const active = c.active.value;
  const rec = c.record.value;
  const recShown = rec && rec.part_id === partId ? rec : null;
  const needsCcx = type !== "wind";
  const why = !partId ? "No part. Build one on the Design page."
    : !built ? "This part has no built model yet."
      : info && !info.available ? (info.detail ?? `${info.name} is not available on this Mac.`)
        : needsCcx && c.ccxMissing.value ? "Install CalculiX to run FEM studies (wind studies still run)." : null;
  const canRun = why === null && info !== null && settings !== null && active === null;
  const showResult = viewSig.value === "result" && recShown !== null;

  const openRun = (runId: string) => { actions.selectRun(runId); setConsoleCollapsed(false); };
  const onAsk = async () => { if (!recShown) return; const runId = await c.ask(recShown); if (runId) openRun(runId); };
  const asked = c.asked.value && recShown && c.asked.value.simId === recShown.sim_id ? c.asked.value : null;
  const doneIds = c.history.value.filter((s) => s.status === "done").map((s) => s.sim_id);
  const immersive = (simId: string) => openImmersive({ simId, ids: doneIds });
  const activeHere = active && active.partId === partId ? active : null;

  return (
    <PageShell title="Studies">
      <div class="st" data-testid="studies">
        <aside class="st-side">
          <div class="st-field">
            <label class="label" for="st-part">Part</label>
            {parts.value.length === 0
              ? <p class="faint" id="st-part">No part. Build one on the Design page.</p>
              : (
                <select id="st-part" value={partId ?? ""} onChange={(e) => actions.selectPart((e.target as HTMLSelectElement).value)}>
                  {parts.value.map((p) => (
                    <option key={p.part_id} value={p.part_id} disabled={p.metrics === null}>{partLabel(p.part_id)}{p.metrics === null ? " (not built)" : ""}</option>
                  ))}
                </select>
              )}
          </div>
          <div class="st-types" role="tablist" aria-label="Study type">
            {TYPE_ORDER.map((t) => {
              const ti = c.types.value.find((x) => x.id === t);
              return (
                <button key={t} type="button" role="tab" class="st-type" aria-selected={t === type} data-testid={`type-${t}`}
                  title={ti ? `${ti.name}: ${ti.doc}${ti.available ? "" : ` · ${ti.detail ?? "not available"}`}` : TYPE_NAMES[t]}
                  data-available={ti ? String(ti.available) : undefined}
                  onClick={() => { typeSig.value = t; c.fieldErrors.value = []; setPicking(null); viewSig.value = "setup"; caseSig.value = 0; }}>
                  {TYPE_NAMES[t]}
                </button>
              );
            })}
          </div>
          {info && <p class="faint st-note text" data-testid="type-doc">{info.doc} · {info.solver} · <b class="st-fid">{info.fidelity_class === "cfd_lbm" ? "low fidelity" : info.fidelity_class === "fem_estimate" ? "estimate" : "FEM"}</b></p>}
          {c.typesError.value && <p class="tone-red text">Could not read the study types: {c.typesError.value}</p>}
          {c.solversError.value && <p class="tone-red text">Could not read the solver status: {c.solversError.value}</p>}
          {solvers && needsCcx && <InstallCard solvers={solvers} checking={checking} onRecheck={() => { setChecking(true); void c.loadSolvers().finally(() => setChecking(false)); }} />}
          <Templates templates={c.templates.value} type={type} disabled={active !== null || !settings}
            onLoad={(t) => {
              const ti = c.types.peek().find((x) => x.id === t.type);
              typeSig.value = t.type;
              if (ti) settingsSig.value = { ...settingsSig.peek(), [t.type]: withDefaults(ti.schema, t.settings) };
              viewSig.value = "setup"; caseSig.value = 0;
            }}
            onSave={(name) => c.saveTemplate(name, type, settings ?? {})}
            onDelete={(id) => c.deleteTemplate(id)} />
          {info && settings && (
            <form class="st-setup" aria-label={`${TYPE_NAMES[type]} study setup`} onSubmit={(e) => { e.preventDefault(); if (canRun && partId) void c.start(partId, type, settings); }}>
              <SchemaForm schema={info.schema} value={settings} onChange={setSettings} errors={c.fieldErrors.value} disabled={active !== null}
                faces={faces} materials={materialNames} onEditMaterials={() => setPanel("materials")}
                onPickFace={(p) => { setPicking(p); viewSig.value = "setup"; }} picking={picking}
                selectedCase={caseSig.value} onSelectCase={(i) => { caseSig.value = i; viewSig.value = "setup"; }} />
              {activeHere ? (
                <div class="st-progress" data-testid="study-progress" data-phase={activeHere.phase ?? "starting"} role="status">
                  <div class="st-progress-row"><span>{activeHere.cancelling ? "cancelling…" : `${TYPE_NAMES[activeHere.type]} · ${phaseText(activeHere.phase)}`}</span></div>
                  <PhaseBar phase={activeHere.phase} pct={activeHere.pct} detail={activeHere.detail} />
                  <div class="st-actions">
                    <Button variant="danger" onClick={() => { void c.cancel(); }} disabled={activeHere.cancelling} aria-label="Cancel study" data-testid="cancel-study">Cancel</Button>
                    <Button onClick={() => openImmersive({ simId: activeHere.simId, ids: doneIds, pending: activeHere.simId ? null : { runId: activeHere.runId, type: activeHere.type, partId: activeHere.partId, sinceTs: activeHere.startedAt } })}
                      data-testid="watch-immersive">Watch</Button>
                  </div>
                </div>
              ) : (
                <Button variant="ink" type="submit" disabled={!canRun} data-testid="run-study">Run {TYPE_NAMES[type].toLowerCase()} study</Button>
              )}
              {!activeHere && active && <p class="faint st-note">A study of {partLabel(active.partId)} is running.</p>}
              {!active && why && <p class="faint st-note" data-testid="why">{why}</p>}
            </form>
          )}
          {c.runError.value && (
            <div class="st-band st-band-red" data-testid="run-error" role="alert">
              <div>
                <div class="text">{c.runError.value.message}</div>
                {c.runError.value.hint && <div class="text dim">{c.runError.value.hint}</div>}
              </div>
            </div>
          )}
          {partId && (
            <History list={c.history.value} selected={c.selectedSimId.value} error={c.historyError.value}
              onSelect={(id) => { viewSig.value = "result"; void c.select(id); }}
              compare={c.compareIds.value} onToggleCompare={(id) => c.toggleCompare(id)}
              onCompare={() => { setPanel("compare"); void c.runCompare(); }} />
          )}
        </aside>
        <section class="st-main" aria-label="Study">
          {builtParts.value.length === 0 && <p class="faint">No built parts. Build one on the Design page.</p>}
          {partId && built && (
            <>
              <div class="st-viewtabs" role="tablist" aria-label="Viewport">
                <button type="button" role="tab" class="st-type" aria-selected={!showResult} onClick={() => { viewSig.value = "setup"; }} data-testid="view-setup">Setup</button>
                <button type="button" role="tab" class="st-type" aria-selected={showResult} disabled={!recShown} onClick={() => { setPicking(null); viewSig.value = "result"; }} data-testid="view-result">Result</button>
                {!showResult && markers.length > 0 && <span class="faint st-note">{markers.filter((m) => m.role === "load").length} loads · {markers.filter((m) => m.role !== "load").length} held or thermal faces</span>}
              </div>
              <StudyView partId={partId} mtime={part.mtime} record={showResult ? recShown : null} markers={markers} faces={faces}
                selectedCase={caseSig.value} picking={showResult ? null : picking}
                onPicked={(face) => { if (picking && settings) setSettings(setAt(settings, picking, face)); setPicking(null); }}
                onCancelPick={() => setPicking(null)}
                liveCaption={activeHere ? `${TYPE_NAMES[activeHere.type]} · ${phaseText(activeHere.phase)}${activeHere.detail ? ` · ${activeHere.detail}` : ""}` : null}
                deps={deps.view} />
            </>
          )}
          {panel === "materials" && (
            <MaterialEditor materials={c.materials.value} onClose={() => setPanel("none")}
              onSave={(m) => c.saveMaterial(m)} onDelete={(n) => c.deleteMaterial(n)} />
          )}
          {panel === "compare" && c.compare.value && (
            <Compare data={c.compare.value} onClose={() => setPanel("none")} onOpen={(id) => { viewSig.value = "result"; void c.select(id); }} />
          )}
          {panel === "compare" && c.compareError.value && <p class="tone-red text">Could not compare: {c.compareError.value}</p>}
          {c.recordError.value && <p class="tone-red text">Could not open the study: {c.recordError.value}</p>}
          {recShown && <ResultPanel rec={recShown} charts={c.charts.value} onAsk={() => { void onAsk(); }} onOpenRun={openRun} asked={asked} onImmersive={() => immersive(recShown.sim_id)} />}
          {partId && built && !recShown && !c.recordError.value && c.history.value.length === 0 && (
            <p class="faint">Pick a study type, set it up on the left and run it. Results are a guide only.</p>
          )}
        </section>
      </div>
    </PageShell>
  );
}
