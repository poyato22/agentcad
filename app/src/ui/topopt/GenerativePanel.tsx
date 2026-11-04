// Generative design panel (D14), reachable from a part's context menu and the Studies page's
// manual setup. Setup: the design space (the part, or its bounding box), faces that stay solid,
// held faces and loads (the Studies face picker: sides or face ids), the goal (a share of the
// material, or a largest deflection), how many variants, and manufacturing (3D print / machined
// / none, symmetry). Then: the density converging live, and the variant grid (render, mass,
// deflection, peak stress, verified ✓ / ✗) with Accept. Mounted once at the app root.
import type { JSX } from "preact";
import { useCallback, useEffect, useMemo, useRef, useState } from "preact/hooks";
import { FacePickView } from "./FacePickView";
import type { PartFace } from "../../api/types";
import { partLabel } from "../../lib/names";
import { parts } from "../../state/store";
import { Button } from "../common/Button";
import { useFocusTrap } from "../common/focusTrap";
import { DensityPreview } from "./DensityPreview";
import { topopt } from "./instance";
import {
  DIRECTIONS, MAX_VARIANTS, MAX_VF, MIN_VF, PROCESS_LABEL, defaultForm, isRunning, overallPct, pct, previewVariant,
  statusLine, type Axis, type GdForm, type Process,
} from "./model";
import type { TopoptController } from "./state";
import { VariantGrid } from "./VariantGrid";
import "./topopt.css";

const PRESETS = ["bottom", "top", "x-min", "x-max", "y-min", "y-max"] as const;

// the setup survives closing the panel (per part)
const forms = new Map<string, GdForm>();

/** Tests: forget the remembered setups. */
export function resetGenerativeForms(): void { forms.clear(); }

export function GenerativeRoot({ controller = topopt }: { controller?: TopoptController }): JSX.Element | null {
  const p = controller.panel.value;
  if (!p) return null;
  return <Panel controller={controller} partId={p.partId} topoptId={p.topoptId} />;
}

function FaceSelect({ value, faces, label, onChange }: { value: string; faces: readonly PartFace[]; label: string; onChange: (v: string) => void }): JSX.Element {
  const known = (PRESETS as readonly string[]).includes(value) || faces.some((f) => f.id === value);
  return (
    <select value={value} aria-label={label} onChange={(e) => onChange((e.target as HTMLSelectElement).value)}>
      <optgroup label="Sides">{PRESETS.map((f) => <option key={f} value={f}>{f}</option>)}</optgroup>
      {faces.length > 0 && <optgroup label="Faces">{faces.map((f) => <option key={f.id} value={f.id}>{f.id} · {f.type} · {Math.round(f.area)} mm²</option>)}</optgroup>}
      {!known && value && <option value={value}>{value}</option>}
    </select>
  );
}

function Panel({ controller: c, partId, topoptId }: { controller: TopoptController; partId: string; topoptId: string | null }): JSX.Element {
  const panel = useRef<HTMLDivElement>(null);
  /** The setup's "stop picking" (true when a picker was armed). */
  const disarm = useRef<(() => boolean) | null>(null);
  useFocusTrap(panel);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      if (disarm.current?.()) return;
      c.close();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [c]);
  const part = parts.value.find((p) => p.part_id === partId) ?? null;
  return (
    <div class="gd-scrim" onClick={(e) => { if (e.target === e.currentTarget) c.close(); }}>
      <div class="gd-panel" ref={panel} role="dialog" aria-modal="true" aria-label="Generative design" data-testid="generative-panel">
        <header class="gd-head">
          <h2 class="gd-title">Generative design <span class="dim">· {partLabel(partId).replace(/^Part · /, "")}</span></h2>
          {topoptId && <Button variant="ghost" onClick={() => c.open(partId)}>New setup</Button>}
          <Button variant="ghost" aria-label="Close generative design" onClick={() => c.close()}>✕</Button>
        </header>
        {topoptId
          ? <RunView c={c} topoptId={topoptId} />
          : <Setup c={c} partId={partId} faces={part?.metrics?.face_list ?? []} built={part?.metrics != null} disarm={disarm} />}
      </div>
    </div>
  );
}

type Armed = { list: "fixtures" | "loads" | "keepFaces"; index: number; label: string };

function Setup({ c, partId, faces, built, disarm }: {
  c: TopoptController; partId: string; faces: readonly PartFace[]; built: boolean; disarm: { current: (() => boolean) | null };
}): JSX.Element {
  const [f, setF] = useState<GdForm>(() => forms.get(partId) ?? defaultForm(partId));
  const set = (patch: Partial<GdForm>) => { const next = { ...f, ...patch }; forms.set(partId, next); setF(next); };
  const [armed, setArmed] = useState<Armed | null>(null);
  disarm.current = () => { if (!armed) return false; setArmed(null); return true; };
  const loadPartGlb = useCallback(() => c.client().partGlb(partId), [c, partId]);
  const pickInto = (id: string) => {
    if (!armed) return;
    const { list, index } = armed;
    if (list === "loads") set({ loads: f.loads.map((x, j) => (j === index ? { ...x, face: id } : x)) });
    else set({ [list]: f[list].map((x, j) => (j === index ? id : x)) } as Partial<GdForm>);
    setArmed(null);
  };
  const arm = (list: Armed["list"], index: number, label: string) =>
    setArmed(armed && armed.list === list && armed.index === index ? null : { list, index, label });
  const PickBtn = ({ list, index, label }: { list: Armed["list"]; index: number; label: string }) => {
    const on = armed?.list === list && armed.index === index;
    return (
      <Button variant={on ? "primary" : "ghost"} aria-pressed={on} onClick={() => arm(list, index, label)}
        aria-label={on ? `Picking ${label.toLowerCase()}: click a face on the part, or press Escape` : `Pick ${label.toLowerCase()} on the part`}>
        {on ? "Click a face…" : "Pick"}
      </Button>
    );
  };
  const used = [...f.fixtures, ...f.loads.map((l) => l.face), ...f.keepFaces];
  // a new keep face starts as the largest face no one uses yet (not just the first in the list)
  const freshKeep = (): string => [...faces].filter((x) => !used.includes(x.id)).sort((a, b) => b.area - a.area)[0]?.id ?? "top";
  const err = (field: string) => c.errors.value.find((e) => e.field === field || e.field.startsWith(`${field}.`))?.message;
  const toggleAxis = (a: Axis) => set({ symmetry: f.symmetry.includes(a) ? f.symmetry.filter((x) => x !== a) : [...f.symmetry, a] });
  return (
    <form class="gd-body gd-setup" onSubmit={(e) => { e.preventDefault(); void c.start(f); }}>
      <section class="gd-pick">
        <h3 class="gd-sec-title">The part</h3>
        {built
          ? <FacePickView load={loadPartGlb} faces={faces} used={used} armedLabel={armed?.label ?? null} onPick={pickInto} />
          : <p class="faint">This part has no built model yet.</p>}
        <p class="faint gd-note">Pick a face with “Pick”, then click it here; the lists work too.</p>
      </section>
      <section class="gd-sec">
        <h3 class="gd-sec-title">Design space</h3>
        <div class="gd-seg" role="radiogroup" aria-label="Design space">
          {(["part", "bbox"] as const).map((d) => (
            <label key={d} class="gd-choice"><input type="radio" name="gd-space" checked={f.designSpace === d} onChange={() => set({ designSpace: d })} />
              {d === "part" ? "The part's own shape" : "Its bounding box"}</label>
          ))}
        </div>
        <div class="gd-list">
          <span class="dim">Keep solid</span>
          {f.keepFaces.map((face, i) => (
            <span key={i} class="gd-item">
              <FaceSelect value={face} faces={faces} label={`Keep face ${i + 1}`} onChange={(v) => set({ keepFaces: f.keepFaces.map((x, j) => (j === i ? v : x)) })} />
              <PickBtn list="keepFaces" index={i} label={`Keep face ${i + 1}`} />
              <Button variant="ghost" aria-label={`Remove keep face ${i + 1}`} onClick={() => set({ keepFaces: f.keepFaces.filter((_, j) => j !== i) })}>✕</Button>
            </span>
          ))}
          <Button onClick={() => set({ keepFaces: [...f.keepFaces, freshKeep()] })}>Add face to keep</Button>
          {f.keepFaces.length > 0 && (
            <label class="gd-inline">Band <input type="number" min={0.5} max={20} step={0.5} value={f.keepThickness} class="num"
              onInput={(e) => set({ keepThickness: Number((e.target as HTMLInputElement).value) })} /> mm</label>
          )}
        </div>
      </section>

      <section class="gd-sec">
        <h3 class="gd-sec-title">Held faces</h3>
        {f.fixtures.map((face, i) => (
          <span key={i} class="gd-item">
            <FaceSelect value={face} faces={faces} label={`Held face ${i + 1}`} onChange={(v) => set({ fixtures: f.fixtures.map((x, j) => (j === i ? v : x)) })} />
            <PickBtn list="fixtures" index={i} label={`Held face ${i + 1}`} />
            <Button variant="ghost" aria-label={`Remove held face ${i + 1}`} onClick={() => set({ fixtures: f.fixtures.filter((_, j) => j !== i) })}>✕</Button>
          </span>
        ))}
        <Button onClick={() => set({ fixtures: [...f.fixtures, "bottom"] })}>Add held face</Button>
        {err("fixtures") && <p class="gd-err" role="alert">{err("fixtures")}</p>}
      </section>

      <section class="gd-sec">
        <h3 class="gd-sec-title">Loads</h3>
        {f.loads.map((l, i) => (
          <span key={i} class="gd-item">
            <FaceSelect value={l.face} faces={faces} label={`Load ${i + 1} face`} onChange={(v) => set({ loads: f.loads.map((x, j) => (j === i ? { ...x, face: v } : x)) })} />
            <PickBtn list="loads" index={i} label={`Load ${i + 1} face`} />
            <input type="number" class="num gd-n" min={0} step={1} value={l.newtons} aria-label={`Load ${i + 1} force (N)`}
              onInput={(e) => set({ loads: f.loads.map((x, j) => (j === i ? { ...x, newtons: Number((e.target as HTMLInputElement).value) } : x)) })} />
            <span class="dim">N</span>
            <select value={l.direction} aria-label={`Load ${i + 1} direction`}
              onChange={(e) => set({ loads: f.loads.map((x, j) => (j === i ? { ...x, direction: (e.target as HTMLSelectElement).value as typeof x.direction } : x)) })}>
              {DIRECTIONS.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
            <Button variant="ghost" aria-label={`Remove load ${i + 1}`} onClick={() => set({ loads: f.loads.filter((_, j) => j !== i) })}>✕</Button>
          </span>
        ))}
        <Button onClick={() => set({ loads: [...f.loads, { face: "top", newtons: 100, direction: "-z" }] })}>Add load</Button>
        {err("loads") && <p class="gd-err" role="alert">{err("loads")}</p>}
      </section>

      <section class="gd-sec">
        <h3 class="gd-sec-title">Goal</h3>
        <label class="gd-choice"><input type="radio" name="gd-goal" checked={f.goal === "volume"} onChange={() => set({ goal: "volume" })} />
          Keep <span class="num">{pct(f.volumeFraction)}</span> of the material, as stiff as possible</label>
        {f.goal === "volume" && (
          <input type="range" min={MIN_VF} max={MAX_VF} step={0.01} value={f.volumeFraction} aria-label="Share of the material to keep"
            onInput={(e) => set({ volumeFraction: Number((e.target as HTMLInputElement).value) })} />
        )}
        <label class="gd-choice"><input type="radio" name="gd-goal" checked={f.goal === "deflection"} onChange={() => set({ goal: "deflection", maxDeflection: f.maxDeflection ?? 1 })} />
          As light as possible within a deflection</label>
        {f.goal === "deflection" && <p class="faint gd-note">Tries {Math.max(3, f.variants)} amounts of material and keeps the lightest that stays within it.</p>}
        {f.goal === "deflection" && (
          <label class="gd-inline">Largest deflection <input type="number" min={0.01} step={0.05} class="num" value={f.maxDeflection ?? ""}
            onInput={(e) => set({ maxDeflection: Number((e.target as HTMLInputElement).value) || null })} /> mm</label>
        )}
        {(err("volumeFraction") ?? err("maxDeflection")) && <p class="gd-err" role="alert">{err("volumeFraction") ?? err("maxDeflection")}</p>}
        <label class="gd-inline">Variants <input type="number" min={1} max={MAX_VARIANTS} step={1} class="num" value={f.variants} aria-label="Number of variants"
          onInput={(e) => set({ variants: Math.round(Number((e.target as HTMLInputElement).value)) })} /></label>
      </section>

      <section class="gd-sec">
        <h3 class="gd-sec-title">Manufacturing</h3>
        <select value={f.process} aria-label="Manufacturing" onChange={(e) => set({ process: (e.target as HTMLSelectElement).value as Process })}>
          {(Object.keys(PROCESS_LABEL) as Process[]).map((p) => <option key={p} value={p}>{PROCESS_LABEL[p]}</option>)}
        </select>
        <span class="gd-inline dim">Mirror symmetry
          {(["x", "y", "z"] as const).map((a) => (
            <label key={a} class="gd-check"><input type="checkbox" checked={f.symmetry.includes(a)} onChange={() => toggleAxis(a)} />{a}</label>
          ))}
        </span>
        <p class="faint gd-note">3D print keeps every overhang self-supporting (45°) along +z. Machined makes it 2.5-D along z.</p>
      </section>

      <footer class="gd-foot">
        {c.problem.value && <p class="gd-err" role="alert">{c.problem.value.message}{c.problem.value.hint ? ` (${c.problem.value.hint})` : ""}</p>}
        {!built && <p class="gd-err">This part has no built model yet.</p>}
        <p class="faint">Every variant is checked in CalculiX: the numbers are a guide, not a guarantee.</p>
        <Button type="submit" variant="primary" disabled={c.busy.value || !built}>Generate</Button>
      </footer>
    </form>
  );
}

/** The replay's beat per kept iteration, and how long the last one holds before the result. */
const REPLAY_MS = 900;
const REPLAY_HOLD_MS = 1400;

function RunView({ c, topoptId }: { c: TopoptController; topoptId: string }): JSX.Element {
  const rec = c.view(topoptId);
  const loadRender = useCallback((vid: string) => c.client().topoptRender(topoptId, vid), [c, topoptId]);
  const running = rec !== null && isRunning(rec);
  const pv = rec ? (running ? previewVariant(rec) : rec.variants.find((v) => v.id === rec.recommended) ?? null) : null;
  const which = running ? "preview" : "result";
  // the density's evolution, replayed on a finished run: one kept iteration a beat, then the result
  const evo = !running ? pv?.urls?.evolution ?? [] : [];
  const [frame, setFrame] = useState<number | null>(null);
  useEffect(() => { setFrame(null); }, [pv?.id, running]);
  useEffect(() => {
    if (frame === null) return;
    const t = setTimeout(() => setFrame(frame + 1 < evo.length ? frame + 1 : null), frame + 1 < evo.length ? REPLAY_MS : REPLAY_HOLD_MS);
    return () => clearTimeout(t);
  }, [frame, evo.length]);
  const evoPath = frame !== null ? evo[frame] ?? null : null;
  const load = useMemo(() => (
    evoPath ? (etag?: string) => c.client().topoptEvoGlb(evoPath, etag)
      : pv ? (etag?: string) => c.client().topoptGlb(topoptId, pv.id, which, etag) : null
  ), [c, topoptId, pv?.id, which, evoPath]);
  if (!rec) return <div class="gd-body"><p class="faint">Loading…</p></div>;
  const iteration = evoPath ? Number(/evo_(\d+)\.glb$/.exec(evoPath)?.[1] ?? 0) : null;
  return (
    <div class="gd-body gd-run" data-testid="gd-run" data-status={rec.status}>
      <div class="gd-status">
        <span data-testid="gd-status">{iteration !== null && frame !== null && pv
          ? `Density of ${pv.name} · iteration ${iteration} · frame ${frame + 1} of ${evo.length}`
          : statusLine(rec)}</span>
        {running && <Button variant="danger" onClick={() => void c.cancel(topoptId)}>Cancel</Button>}
        {evo.length > 1 && (
          <Button variant="ghost" onClick={() => setFrame(0)} disabled={frame !== null}>Replay the density</Button>
        )}
      </div>
      {running && <div class="gd-bar" role="progressbar" aria-valuenow={overallPct(rec)} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${overallPct(rec)}%` }} /></div>}
      {load && pv && (
        <DensityPreview load={load} version={evoPath ?? (running ? (pv.preview ?? null) : `${pv.id}-done`)}
          label={evoPath ? `Density of ${pv.name}, iteration ${iteration}` : running ? `Density of ${pv.name} converging` : `Result of ${pv.name}`} />
      )}
      <VariantGrid rec={rec} loadRender={loadRender} busy={c.busy.value}
        onAccept={(vid) => void c.accept(topoptId, { variant: vid, mode: "new" })} />
      {c.accepted.value && <p class="gd-ok" role="status">Added {c.accepted.value.part_id} to the build.</p>}
      {c.problem.value && <p class="gd-err" role="alert">{c.problem.value.message}</p>}
      <p class="faint gd-note">Mass, deflection and peak stress come from CalculiX on each final solid (a guide, not a guarantee). Unverified or failing variants never rank first.</p>
    </div>
  );
}
