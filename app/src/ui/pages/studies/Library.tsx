// Saved study templates (GET/POST/DELETE /sims/templates) and the custom material editor
// (GET/POST/DELETE /sims/materials: E, ν, density, yield, thermal conductivity, expansion, HDT,
// saved in the Mises home). Loading a template switches to its type and replaces the settings.
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import type { Calibration } from "../../../api/accuracy";
import type { CustomMaterialBody, StudyMaterials, StudyTemplate, StudyType } from "../../../api/studies";
import { Button } from "../../common/Button";
import type { Problem } from "./controller";
import { typeName } from "./model";

export function Templates({ templates, type, onLoad, onSave, onDelete, disabled }: {
  templates: readonly StudyTemplate[]; type: StudyType; disabled?: boolean;
  onLoad: (t: StudyTemplate) => void; onSave: (name: string) => Promise<Problem | null>; onDelete: (id: string) => Promise<Problem | null>;
}): JSX.Element {
  const [name, setName] = useState("");
  const [problem, setProblem] = useState<Problem | null>(null);
  const [pick, setPick] = useState("");
  const chosen = templates.find((t) => t.id === pick) ?? null;
  return (
    <section class="st-tpl" aria-label="Study templates">
      <div class="st-tpl-row">
        <label class="label" for="st-tpl-pick">Template</label>
        <select id="st-tpl-pick" value={pick} disabled={disabled} onChange={(e) => setPick((e.target as HTMLSelectElement).value)} data-testid="template-pick">
          <option value="">choose…</option>
          {templates.map((t) => <option key={t.id} value={t.id}>{t.name} · {typeName(t.type)}</option>)}
        </select>
        <Button disabled={disabled || !chosen} onClick={() => { if (chosen) onLoad(chosen); }} data-testid="template-load">Load</Button>
        <Button variant="ghost" disabled={disabled || !chosen} aria-label={chosen ? `Delete the template ${chosen.name}` : "Delete the template"}
          onClick={() => { if (chosen) void onDelete(chosen.id).then((p) => { setProblem(p); if (!p) setPick(""); }); }}>Delete</Button>
      </div>
      <form class="st-tpl-row" onSubmit={(e) => {
        e.preventDefault();
        const n = name.trim();
        if (!n) return;
        void onSave(n).then((p) => { setProblem(p); if (!p) setName(""); });
      }}>
        <label class="label" for="st-tpl-name">Save as</label>
        <input id="st-tpl-name" value={name} maxLength={60} placeholder={`${typeName(type)} setup name`} disabled={disabled}
          onInput={(e) => setName((e.target as HTMLInputElement).value)} data-testid="template-name" />
        <Button type="submit" disabled={disabled || !name.trim()} data-testid="template-save">Save</Button>
      </form>
      {problem && <p class="tone-red text" role="alert">{problem.message}{problem.hint ? ` · ${problem.hint}` : ""}</p>}
    </section>
  );
}

const FIELDS: { key: keyof CustomMaterialBody; label: string; unit: string; step: string; def: number }[] = [
  { key: "e_mpa", label: "Young's modulus", unit: "MPa", step: "any", def: 2000 },
  { key: "nu", label: "Poisson's ratio", unit: "", step: "0.01", def: 0.35 },
  { key: "density_g_cm3", label: "Density", unit: "g/cm³", step: "any", def: 1.2 },
  { key: "yield_mpa", label: "Yield", unit: "MPa", step: "any", def: 40 },
  { key: "k_w_mk", label: "Conductivity", unit: "W/m·K", step: "any", def: 0.2 },
  { key: "alpha_per_k", label: "Expansion", unit: "1/K", step: "any", def: 7e-5 },
  { key: "hdt_c", label: "HDT", unit: "°C", step: "any", def: 60 },
];

/** Same ranges as core settings.CustomMaterial (said before saving; the core checks again). */
export function materialProblems(m: CustomMaterialBody): string[] {
  const out: string[] = [];
  const bad = (v: unknown) => typeof v !== "number" || !Number.isFinite(v);
  if (!m.name.trim() || m.name.length > 40) out.push("a name of 1–40 characters");
  if (bad(m.e_mpa) || m.e_mpa <= 1 || m.e_mpa > 5e5) out.push("Young's modulus above 1 and at most 500,000 MPa");
  if (bad(m.nu) || m.nu < 0 || m.nu >= 0.5) out.push("Poisson's ratio from 0 to below 0.5");
  if (bad(m.yield_mpa) || m.yield_mpa <= 0 || m.yield_mpa > 1e4) out.push("yield above 0 and at most 10,000 MPa");
  if (bad(m.density_g_cm3) || m.density_g_cm3 <= 0.01 || m.density_g_cm3 > 25) out.push("density above 0.01 and at most 25 g/cm³");
  if (m.k_w_mk !== undefined && (bad(m.k_w_mk) || m.k_w_mk <= 0 || m.k_w_mk > 500)) out.push("conductivity above 0 and at most 500 W/m·K");
  if (m.alpha_per_k !== undefined && (bad(m.alpha_per_k) || m.alpha_per_k < 0 || m.alpha_per_k > 1e-3)) out.push("expansion from 0 to 0.001 1/K");
  if (m.hdt_c !== undefined && (bad(m.hdt_c) || m.hdt_c < -50 || m.hdt_c > 1000)) out.push("HDT from −50 to 1000 °C");
  return out;
}

export function MaterialEditor({ materials, onSave, onDelete, onClose, onCalibrate, calibrations = [] }: {
  materials: StudyMaterials | null; onClose: () => void; onCalibrate?: (name: string) => void; calibrations?: readonly Calibration[];
  onSave: (m: CustomMaterialBody) => Promise<Problem | null>; onDelete: (name: string) => Promise<Problem | null>;
}): JSX.Element {
  const blank = (): CustomMaterialBody => ({ name: "", source: "", ...Object.fromEntries(FIELDS.map((f) => [f.key, f.def])) } as CustomMaterialBody);
  const [m, setM] = useState<CustomMaterialBody>(blank);
  const [problem, setProblem] = useState<string | null>(null);
  const problems = materialProblems(m);
  const custom = materials?.custom ?? [];
  return (
    <section class="st-mat" aria-label="Materials" data-testid="material-editor">
      <header class="st-compare-head">
        <h2 class="label">Materials</h2>
        <Button variant="ghost" onClick={onClose}>Close</Button>
      </header>
      <table class="st-ctable">
        <thead><tr><th>name</th><th>E MPa</th><th>ν</th><th>yield MPa</th><th>ρ g/cm³</th><th>k W/m·K</th><th>HDT °C</th><th /></tr></thead>
        <tbody>
          {[...(materials?.builtin ?? []), ...custom].map((x) => (
            <tr key={x.name} data-testid="material-row" data-custom={x.custom ? "true" : "false"}>
              <td class="text">
                {x.name}{x.custom ? <span class="faint"> · saved</span> : null}
                {/* Plan 6: calibrated (n tests) or datasheet / estimate, on every material */}
                {(() => {
                  // S3: a calibration applies per printer; name each printer that has one
                  const cals = calibrations.filter((c) => c.material.toLowerCase() === x.name.toLowerCase());
                  const label = cals.length ? cals.map((c) => `${c.basis} on ${c.printer}`).join(" · ") : "datasheet / estimate";
                  return <span class={`acc-badge${cals.length ? " is-cal" : ""}`} data-testid="material-badge">{label}</span>;
                })()}
              </td>
              <td>{x.e_mpa}</td><td>{x.nu}</td><td>{x.yield_mpa}</td><td>{x.density_g_cm3}</td><td>{x.k_w_mk}</td><td>{x.hdt_c}</td>
              <td>
                {x.custom
                  ? <Button variant="ghost" aria-label={`Delete the material ${x.name}`} onClick={() => { void onDelete(x.name).then((p) => setProblem(p ? p.message : null)); }}>Delete</Button>
                  : <Button variant="ghost" aria-label={`Start a material from ${x.name}`} onClick={() => setM({ name: "", source: `based on ${x.name}`, e_mpa: x.e_mpa, nu: x.nu, yield_mpa: x.yield_mpa, density_g_cm3: x.density_g_cm3, k_w_mk: x.k_w_mk, alpha_per_k: x.alpha_per_k, hdt_c: x.hdt_c })}>Copy</Button>}
                {onCalibrate && <Button variant="ghost" aria-label={`Calibrate ${x.name} from your own tests`} onClick={() => onCalibrate(x.name)}>Calibrate</Button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <form class="st-mat-form" onSubmit={(e) => {
        e.preventDefault();
        if (problems.length) return;
        void onSave({ ...m, name: m.name.trim() }).then((p) => { setProblem(p ? `${p.message}${p.hint ? ` · ${p.hint}` : ""}` : null); if (!p) setM(blank()); });
      }}>
        <div class="sf-row"><label class="sf-label" for="mat-name">Name</label><input id="mat-name" value={m.name} maxLength={40} onInput={(e) => setM({ ...m, name: (e.target as HTMLInputElement).value })} data-testid="material-name" /></div>
        {FIELDS.map((f) => (
          <div key={f.key} class="sf-row">
            <label class="sf-label" for={`mat-${f.key}`}>{f.label}{f.unit && <span class="faint"> {f.unit}</span>}</label>
            <input id={`mat-${f.key}`} type="number" step={f.step} value={String(m[f.key] ?? "")}
              onInput={(e) => setM({ ...m, [f.key]: Number.parseFloat((e.target as HTMLInputElement).value) })} />
          </div>
        ))}
        <div class="sf-row"><label class="sf-label" for="mat-src">Source</label><input id="mat-src" value={m.source ?? ""} maxLength={200} placeholder="datasheet, test…" onInput={(e) => setM({ ...m, source: (e.target as HTMLInputElement).value })} /></div>
        <p class="faint st-note">Printed parts get the FDM knock-down on top of these values (approximate). Saved in this Mac's Mises home.</p>
        {problems.length > 0 && m.name && <ul class="st-problems">{problems.map((p) => <li key={p} class="tone-red">{p}</li>)}</ul>}
        {problem && <p class="tone-red text" role="alert">{problem}</p>}
        <Button type="submit" variant="ink" disabled={problems.length > 0} data-testid="material-save">Save material</Button>
      </form>
    </section>
  );
}
