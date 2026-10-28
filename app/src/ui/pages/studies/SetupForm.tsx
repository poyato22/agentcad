// Study setup: preset (environment), fixed face, load face + force + direction, material; Run,
// and while running: stage, elapsed time and Cancel.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import { SIM_DIRECTIONS, SIM_FACE_PRESETS, type SimDirection } from "../../../api/sims";
import { fmtElapsed } from "../../../lib/format";
import { Button } from "../../common/Button";
import type { ActiveStudy } from "./controller";
import { applyPreset, ENV_PRESETS, MATERIALS, setupProblems, stageFraction, stageText, type Setup } from "./model";

function Field({ label, children }: { label: string; children: (id: string) => JSX.Element }): JSX.Element {
  const id = `st-${label.toLowerCase().replace(/[^a-z]+/g, "-")}`;
  return (
    <div class="st-field">
      <label class="label" for={id}>{label}</label>
      {children(id)}
    </div>
  );
}

function Progress({ active, onCancel }: { active: ActiveStudy; onCancel: () => void }): JSX.Element {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);
  const frac = stageFraction(active.stage);
  return (
    <div class="st-progress" data-testid="study-progress" data-stage={active.stage ?? "starting"} role="status">
      <div class="st-progress-row">
        <span>{active.cancelling ? "cancelling…" : stageText(active.stage)}</span>
        <span class="faint">{fmtElapsed(Math.max(0, now - active.startedAt))}</span>
      </div>
      <div class="st-bar" aria-hidden="true"><div class="st-bar-fill" style={{ width: `${Math.round(frac * 100)}%` }} /></div>
      <Button variant="danger" onClick={onCancel} disabled={active.cancelling} aria-label="Cancel study">Cancel</Button>
    </div>
  );
}

export function SetupForm({ setup, onChange, onRun, onCancel, active, canRun, why }: {
  setup: Setup; onChange: (s: Setup) => void; onRun: () => void; onCancel: () => void;
  active: ActiveStudy | null; canRun: boolean; why: string | null;
}): JSX.Element {
  const problems = setupProblems(setup);
  const set = (patch: Partial<Setup>) => onChange({ ...setup, ...patch });
  const faceOptions = (current: string) => (
    <>
      {SIM_FACE_PRESETS.map((f) => <option key={f} value={f}>{f}</option>)}
      {!(SIM_FACE_PRESETS as readonly string[]).includes(current) && <option value={current}>{current}</option>}
    </>
  );
  const busy = active !== null;
  return (
    <form class="st-setup" aria-label="Study setup" onSubmit={(e) => { e.preventDefault(); if (canRun && !problems.length && !busy) onRun(); }}>
      <Field label="Preset">{(id) => (
        <select id={id} value={setup.environment ?? ""} disabled={busy}
          onChange={(e) => onChange(applyPreset(setup, (e.target as HTMLSelectElement).value))}>
          <option value="">none (custom)</option>
          {ENV_PRESETS.map((p) => <option key={p.name} value={p.name}>{p.name} · {p.temperature_c} °C</option>)}
        </select>
      )}</Field>
      <Field label="Fixed face">{(id) => (
        <select id={id} value={setup.fix} disabled={busy} onChange={(e) => set({ fix: (e.target as HTMLSelectElement).value })}>{faceOptions(setup.fix)}</select>
      )}</Field>
      <Field label="Load face">{(id) => (
        <select id={id} value={setup.face} disabled={busy} onChange={(e) => set({ face: (e.target as HTMLSelectElement).value })}>{faceOptions(setup.face)}</select>
      )}</Field>
      <div class="st-row">
        <Field label="Force (N)">{(id) => (
          <input id={id} type="number" min="0" step="any" inputMode="decimal" value={Number.isFinite(setup.newtons) ? String(setup.newtons) : ""} disabled={busy}
            onInput={(e) => set({ newtons: Number.parseFloat((e.target as HTMLInputElement).value) })} />
        )}</Field>
        <Field label="Direction">{(id) => (
          <select id={id} value={setup.direction} disabled={busy} onChange={(e) => set({ direction: (e.target as HTMLSelectElement).value as SimDirection })}>
            {SIM_DIRECTIONS.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        )}</Field>
      </div>
      <Field label="Material">{(id) => (
        <select id={id} value={setup.material} disabled={busy} onChange={(e) => set({ material: (e.target as HTMLSelectElement).value })}>
          {MATERIALS.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      )}</Field>
      {setup.environment && <p class="faint text st-note">{ENV_PRESETS.find((p) => p.name === setup.environment)?.notes}</p>}
      {problems.length > 0 && (
        <ul class="st-problems" data-testid="setup-problems">{problems.map((p) => <li key={p} class="tone-red">{p}</li>)}</ul>
      )}
      {active
        ? <Progress active={active} onCancel={onCancel} />
        : <Button variant="ink" type="submit" disabled={!canRun || problems.length > 0}>Run study</Button>}
      {!active && why && <p class="faint st-note">{why}</p>}
    </form>
  );
}
