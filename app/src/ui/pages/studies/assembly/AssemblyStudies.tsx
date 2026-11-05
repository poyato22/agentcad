// Plan 13 H4: the Studies page's "Whole assembly" target. Side: the study types (Walk for legged
// builds, Launch for rockets, Drop, Run mechanism; the studies agent's pick marked with why), the
// chosen type's settings (simple fields from its schema), Run with live progress and Cancel, and
// the build's assembly studies. Main: the shown study — verdict, pass / fail checks vs its
// targets and the spec, metrics, charts, Watch (the assembly moving in its full window, Section /
// X-ray on its bar) and "Check part stresses" (its peak loads to per-part FEM). A guide only.
import type { JSX } from "preact";
import { signal } from "@preact/signals";
import { useEffect, useMemo } from "preact/hooks";
import type { AssemblyJointRow, AssemblyKind, AssemblyStudyKind, AssemblyStudyRecord } from "../../../../api/assemblyStudies";
import { Button } from "../../../common/Button";
import { Led } from "../../../common/Led";
import { StudyChart } from "../charts/StudyChart";
import { verdictTone } from "../model";
import type { AssemblyStudiesController } from "./controller";
import "./assembly.css";

type Field = { key: string; label: string; kind: "number" | "integer" | "text" | "bool" | "enum" | "list"; options?: string[]; min?: number; max?: number; doc?: string; unit?: string };

const UNITS: Record<string, string> = { _mm: "mm", _m: "m", _s: "s", _mps: "m/s", _g: "g" };
const unitOf = (key: string): string | undefined => Object.entries(UNITS).find(([s]) => key.endsWith(s))?.[1] ?? (key === "max_g" ? "g" : undefined);

/** The schema's simple fields (numbers, enums, booleans, a list of words); maps and objects stay at their defaults. */
export function simpleFields(schema: Record<string, unknown>): Field[] {
  const props = (schema.properties ?? {}) as Record<string, Record<string, unknown>>;
  const out: Field[] = [];
  for (const [key, raw] of Object.entries(props)) {
    const alts = (raw.anyOf as Record<string, unknown>[] | undefined)?.filter((a) => a.type !== "null") ?? [raw];
    const p = { ...raw, ...(alts[0] ?? {}) } as Record<string, unknown>;
    const label = key.replace(/_(mm|m|s|mps|g)$/, "").replace(/_/g, " ");
    const base = { key, label, doc: typeof raw.description === "string" ? raw.description : undefined, unit: unitOf(key) };
    const num = (k: "minimum" | "exclusiveMinimum" | "maximum" | "exclusiveMaximum") => (typeof p[k] === "number" ? (p[k] as number) : undefined);
    if (Array.isArray(p.enum)) out.push({ ...base, kind: "enum", options: p.enum.map(String) });
    else if (p.type === "number" || p.type === "integer") out.push({ ...base, kind: p.type as "number" | "integer", min: num("minimum") ?? num("exclusiveMinimum"), max: num("maximum") ?? num("exclusiveMaximum") });
    else if (p.type === "boolean") out.push({ ...base, kind: "bool" });
    else if (p.type === "string") out.push({ ...base, kind: "text" });
    else if (p.type === "array" && (p.items as { type?: string } | undefined)?.type === "string") out.push({ ...base, kind: "list" });
  }
  return out;
}

function defaults(schema: Record<string, unknown>): Record<string, unknown> {
  const props = (schema.properties ?? {}) as Record<string, Record<string, unknown>>;
  const out: Record<string, unknown> = {};
  for (const [k, p] of Object.entries(props)) if (p.default !== undefined) out[k] = p.default;
  return out;
}

/** The chosen type and its settings survive page switches (module state, like the part setup). */
const kindSig = signal<AssemblyStudyKind | null>(null);
const valuesSig = signal<Partial<Record<AssemblyStudyKind, Record<string, unknown>>>>({});
/** Tests: a fresh setup. */
export function resetAssemblySide(): void { kindSig.value = null; valuesSig.value = {}; }

export function AssemblySide({ c, projectId, initialKind }: { c: AssemblyStudiesController; projectId: string | null; initialKind?: AssemblyStudyKind | null }): JSX.Element {
  useEffect(() => { void c.load(projectId); }, [c, projectId]);
  const kinds = c.kinds.value;
  const rec = kinds.find((k) => k.recommended) ?? null;
  const kind = kindSig.value;
  const setKind = (k: AssemblyStudyKind) => { kindSig.value = k; };
  useEffect(() => { if (initialKind) kindSig.value = initialKind; }, [initialKind]);
  const chosen: AssemblyKind | null = kinds.find((k) => k.kind === (kind ?? rec?.kind)) ?? kinds[0] ?? null;
  const values = valuesSig.value;
  const setValues = (v: typeof values) => { valuesSig.value = v; };
  const fields = useMemo(() => (chosen ? simpleFields(chosen.schema) : []), [chosen?.kind, chosen?.schema]);
  const settings = chosen ? values[chosen.kind] ?? defaults(chosen.schema) : {};
  const set = (k: string, v: unknown) => { if (chosen) setValues({ ...values, [chosen.kind]: { ...settings, [k]: v } }); };
  const active = c.active.value;
  const activeHere = active && active.projectId === projectId ? active : null;
  if (!projectId) return <p class="faint">No build. Open one on the Design page.</p>;
  return (
    <div class="asy" data-testid="assembly-studies">
      {c.kindsError.value && <p class="tone-red text">Could not read the assembly studies: {c.kindsError.value}</p>}
      {rec && rec.applicable && (
        <p class="asy-rec text" data-testid="assembly-recommended"><b>Studies agent:</b> run a {rec.name.toLowerCase()} study. {rec.why}</p>
      )}
      <div class="st-types" role="tablist" aria-label="Assembly study type">
        {kinds.map((k) => (
          <button key={k.kind} type="button" role="tab" class="st-type" aria-selected={chosen?.kind === k.kind} data-testid={`asy-kind-${k.kind}`}
            data-applicable={String(k.applicable)} title={`${k.doc}${k.applicable ? "" : ` · ${k.why}`}`} onClick={() => setKind(k.kind)}>
            {k.name}{k.recommended ? " ★" : ""}
          </button>
        ))}
      </div>
      {chosen && (
        <form class="asy-form" noValidate aria-label={`${chosen.name} study setup`}
          onSubmit={(e) => { e.preventDefault(); if (!activeHere) void c.start(projectId, chosen.kind, settings); }}>
          <p class="faint st-note text">{chosen.doc}{chosen.applicable ? "" : ` · ${chosen.why}`}</p>
          {fields.map((f) => (
            <label key={f.key} class="asy-field" title={f.doc}>
              <span class="label">{f.label}{f.unit ? ` (${f.unit})` : ""}</span>
              {f.kind === "enum" ? (
                <select value={String(settings[f.key] ?? f.options?.[0] ?? "")} onChange={(e) => set(f.key, (e.target as HTMLSelectElement).value)}>
                  {f.options?.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              ) : f.kind === "bool" ? (
                <input type="checkbox" checked={settings[f.key] === true} onChange={(e) => set(f.key, (e.target as HTMLInputElement).checked)} />
              ) : f.kind === "list" ? (
                <input type="text" value={Array.isArray(settings[f.key]) ? (settings[f.key] as string[]).join(", ") : ""}
                  onChange={(e) => set(f.key, (e.target as HTMLInputElement).value.split(",").map((x) => x.trim()).filter(Boolean))} />
              ) : (
                <input type={f.kind === "text" ? "text" : "number"} step={f.kind === "integer" ? 1 : "any"} min={f.min} max={f.max} placeholder="auto"
                  value={settings[f.key] === null || settings[f.key] === undefined ? "" : String(settings[f.key])}
                  data-testid={`asy-field-${f.key}`}
                  onInput={(e) => {
                    const t = (e.target as HTMLInputElement).value;
                    set(f.key, f.kind === "text" ? t : t.trim() === "" ? null : Number(t));
                  }} />
              )}
            </label>
          ))}
          {activeHere ? (
            <div class="asy-progress" role="status" data-testid="asy-progress" data-phase={activeHere.phase ?? "starting"}>
              <div class="text">{activeHere.cancelling ? "cancelling…" : `${chosen && activeHere.kind === chosen.kind ? chosen.name : activeHere.kind} · ${activeHere.phase ?? "starting"}${activeHere.detail ? ` · ${activeHere.detail}` : ""}`}</div>
              <div class="scard-bar"><div class="scard-bar-fill" style={{ width: `${Math.round(activeHere.pct ?? 0)}%` }} /></div>
              <Button variant="danger" onClick={() => { void c.cancel(); }} disabled={activeHere.cancelling} data-testid="asy-cancel">Cancel</Button>
            </div>
          ) : (
            <Button variant="ink" type="submit" disabled={!chosen.applicable && chosen.kind !== "drop"} data-testid="asy-run">Run {chosen.name.toLowerCase()} study</Button>
          )}
        </form>
      )}
      {c.runError.value && <div class="st-band st-band-red text" role="alert" data-testid="asy-error">{c.runError.value}</div>}
      {c.history.value.length > 0 && (
        <ul class="asy-history" aria-label="Assembly studies">
          {c.history.value.map((h) => (
            <li key={h.study_id}>
              <button type="button" class="asy-hist" aria-pressed={c.record.value?.study_id === h.study_id} onClick={() => { void c.select(h.study_id); }}>
                <Led tone={h.status === "running" ? "blue" : verdictTone(h.verdict === "not_checked" ? null : h.verdict)} />
                <span>{h.name}</span><span class="faint">{h.status === "running" ? `${h.phase ?? "running"}…` : h.status === "done" ? h.verdict ?? "" : h.status}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const fmt = (v: number | string | null | undefined, unit?: string): string => {
  if (v === null || v === undefined) return "n/a";
  const s = typeof v === "number" ? String(Math.abs(v) >= 100 ? Math.round(v) : Math.round(v * 100) / 100) : v;
  return unit ? `${s} ${unit}` : s;
};

/** Each joint vs its actuator: the part it moves, the actuator, the peak and the share of stall
 *  as a bar against the 60% margin rule (a study's "which joint is over" at a glance). */
export function JointTable({ rows }: { rows: readonly AssemblyJointRow[] }): JSX.Element {
  const sorted = [...rows].sort((a, b) => (a.label ?? a.joint_id).localeCompare(b.label ?? b.joint_id));
  return (
    <table class="asy-joints" data-testid="asy-joints" aria-label="Joints vs actuators">
      <thead><tr><th>joint (part it moves)</th><th>actuator</th><th class="num">peak</th><th>use of stall</th></tr></thead>
      <tbody>
        {sorted.map((r) => {
          const use = r.use_of_stall;
          return (
            <tr key={r.joint_id} data-status={r.status} data-testid="asy-joint">
              <td><Led tone={verdictTone(r.status === "not_checked" ? null : r.status)} /> {r.label ?? r.joint_id}</td>
              <td class="faint">{r.actuator ?? "none"}</td>
              <td class="num">{r.peak_nm != null ? `${r.peak_nm.toFixed(2)} N m` : "n/a"}</td>
              <td class="asy-use">
                {use != null ? (
                  <>
                    <span class="asy-bar" aria-hidden="true"><i style={{ width: `${Math.min(100, use * 100)}%` }} /><b style={{ left: "60%" }} /></span>
                    <span class="num">{Math.round(use * 100)} %</span>
                  </>
                ) : <span class="faint">not checked</span>}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function AssemblyResult({ c }: { c: AssemblyStudiesController }): JSX.Element | null {
  const rec: AssemblyStudyRecord | null = c.record.value;
  if (c.recordError.value) return <p class="tone-red text">Could not open the study: {c.recordError.value}</p>;
  if (!rec) return <p class="faint">Pick a whole-assembly study on the left and run it. Results are a guide only.</p>;
  const fem = rec.fem;
  const femRunning = fem?.status === "running";
  return (
    <section class="asy-result" data-testid="assembly-result" data-verdict={rec.verdict ?? ""} aria-label={`${rec.name} study result`}>
      <header class="asy-head">
        <Led tone={verdictTone(rec.verdict === "not_checked" ? null : rec.verdict)} />
        <h3>{rec.name} study · {rec.status === "done" ? rec.verdict : rec.status}</h3>
        {(rec.playback?.steps.length ?? 0) > 0 && <Button variant="ink" onClick={() => c.play(rec)} data-testid="asy-watch" title="The assembly moving, full window (Section and X-ray on its bar)">Watch</Button>}
      </header>
      {rec.summary && <p class="text">{rec.summary}</p>}
      {rec.error && <p class="tone-red text">{rec.error}{rec.hint ? ` (${rec.hint})` : ""}</p>}
      {rec.metrics.length > 0 && (
        <dl class="asy-metrics">
          {rec.metrics.map((m, i) => <div key={i} data-status={m.status ?? ""}><dt class="faint">{m.label}</dt><dd>{fmt(m.value, m.unit)}</dd></div>)}
        </dl>
      )}
      {rec.checks.length > 0 && (
        <ul class="scard-checks" aria-label="Checks vs the spec and targets" data-testid="asy-checks">
          {rec.checks.map((ch, i) => (
            <li key={i} data-testid="asy-check" data-status={ch.status}>
              <Led tone={verdictTone(ch.status === "not_checked" ? null : ch.status)} />
              <b>{ch.name}</b> <span class="text">{ch.status === "not_checked" ? "not checked" : ch.status}{ch.reason ? ` · ${ch.reason}` : ""}</span>
            </li>
          ))}
        </ul>
      )}
      {rec.joints.length > 0 && <JointTable rows={rec.joints} />}
      <div class="asy-charts">{rec.charts.slice(0, 4).map((ch) => <StudyChart key={ch.id} chart={ch} height={180} />)}</div>
      {fem && (
        <div class="asy-fem" data-testid="asy-fem" data-status={fem.status ?? ""}>
          {fem.available ? (
            <>
              <Button onClick={() => { void c.checkStresses(rec); }} disabled={femRunning || rec.status !== "done"} data-testid="asy-check-stresses"
                title="Hand this study's peak loads to per-part FEM studies">{femRunning ? "Checking part stresses…" : "Check part stresses"}</Button>
              {fem.summary && !femRunning && <p class="text">{fem.summary}</p>}
              {fem.rows.length > 0 && (
                <ul class="scard-checks">{fem.rows.map((r, i) => <li key={i} data-status={r.status}><Led tone={verdictTone(r.status === "not_checked" ? null : r.status)} /><b>{r.name ?? r.part_id}</b> <span class="text">{r.safety_factor != null ? `SF ${r.safety_factor.toFixed(2)}` : r.reason ?? ""}</span></li>)}</ul>
              )}
            </>
          ) : <p class="faint text">{fem.note ?? "No per-part stress hand-off for this study."}</p>}
          {c.femError.value && <p class="tone-red text">{c.femError.value}</p>}
        </div>
      )}
      {rec.notes.length > 0 && <ul class="faint text asy-notes">{rec.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
      <p class="faint text">Guide only{rec.accuracy.length ? `: ${rec.accuracy[0]}` : ""}.</p>
    </section>
  );
}
