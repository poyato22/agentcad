// Material calibration: the user's own coupons and load tests for one material on one printer.
// Enter tensile bars (break force, cross-section), three-point bends (span, load, deflection) and
// part load tests (a known load on a studied part and its measured deflection); each row shows
// what it implies on its own as you type. "Fit" saves the tests and the core fits E, strength
// and the knock-down (least squares), with the scatter and a confidence; the dot plots show
// every test against the fit and the datasheet value. "Apply" makes the material "calibrated
// (n tests)" everywhere. Nothing is applied without that click.
import type { JSX } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { provenance, type Calibration, type CalibrationTest, type CalibrationTestKind } from "../../../../api/accuracy";
import { CoreError, type CoreClient } from "../../../../api/client";
import type { StudyMaterial, StudySummary } from "../../../../api/studies";
import { Button } from "../../../common/Button";
import { Led } from "../../../common/Led";
import { sig3 } from "../model";
import { CalScatter, type CalPoint } from "./CalScatter";
import { CONFIDENCE_TEXT, implied, TEST_TEXT } from "./model";

export type CalibrationClient = Pick<CoreClient, "calibrations" | "saveCalibration" | "applyCalibration" | "deleteCalibration" | "printers">;

const FRESH: Record<CalibrationTestKind, () => CalibrationTest> = {
  tensile: () => ({ kind: "tensile", break_n: Number.NaN, width_mm: 10, thickness_mm: 4, elongation_pct: null }),
  bend: () => ({ kind: "bend", span_mm: 64, width_mm: 10, thickness_mm: 4, load_n: Number.NaN, deflection_mm: Number.NaN }),
  part_load: () => ({ kind: "part_load", sim_id: "", probe: null, load_n: Number.NaN, deflection_mm: Number.NaN }),
};

/** Each kind's inputs: key, label, unit. */
const INPUTS: Record<CalibrationTestKind, { key: string; label: string; unit: string; optional?: boolean }[]> = {
  tensile: [
    { key: "break_n", label: "break force", unit: "N" }, { key: "width_mm", label: "width", unit: "mm" },
    { key: "thickness_mm", label: "thickness", unit: "mm" }, { key: "elongation_pct", label: "elongation", unit: "%", optional: true },
  ],
  bend: [
    { key: "span_mm", label: "span", unit: "mm" }, { key: "width_mm", label: "width", unit: "mm" }, { key: "thickness_mm", label: "thickness", unit: "mm" },
    { key: "load_n", label: "load", unit: "N" }, { key: "deflection_mm", label: "deflection", unit: "mm" },
  ],
  part_load: [{ key: "load_n", label: "load", unit: "N" }, { key: "deflection_mm", label: "measured deflection", unit: "mm" }],
};

const ok = (v: unknown) => typeof v === "number" && Number.isFinite(v) && v > 0;

/** Problems with one test before it is sent (the core checks again). */
export function testProblems(t: CalibrationTest): string[] {
  const out: string[] = [];
  for (const f of INPUTS[t.kind]) {
    const v = (t as Record<string, unknown>)[f.key];
    if (f.optional && (v === null || v === undefined || (typeof v === "number" && Number.isNaN(v)))) continue;
    if (!ok(v)) out.push(`${f.label} above 0`);
  }
  if (t.kind === "part_load" && !t.sim_id) out.push("the study of the loaded part");
  return out;
}

const messageOf = (e: unknown): string => (e instanceof CoreError ? `${e.message}${e.hint ? ` · ${e.hint}` : ""}` : e instanceof Error ? e.message : String(e));

export function CalibrationPanel({ client, materials, studies, initialMaterial, onApplied, onClose }: {
  client: () => CalibrationClient;
  materials: readonly StudyMaterial[];
  /** Finished static studies a part load test can compare against. */
  studies: readonly StudySummary[];
  initialMaterial?: string | null;
  onApplied: () => void;
  onClose: () => void;
}): JSX.Element {
  const [material, setMaterial] = useState(initialMaterial ?? materials[0]?.name ?? "");
  const [printer, setPrinter] = useState("");
  const [printers, setPrinters] = useState<string[]>([]);
  const [all, setAll] = useState<Calibration[]>([]);
  const [tests, setTests] = useState<CalibrationTest[]>([]);
  const [saved, setSaved] = useState<Calibration | null>(null);
  const [busy, setBusy] = useState<"fit" | "apply" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    void client().calibrations().then(setAll, (e: unknown) => setProblem(`Could not read the calibrations: ${messageOf(e)}`));
    void client().printers().then((ps) => {
      const names = ps.map((p) => p.name);
      setPrinters(names);
      setPrinter((cur) => cur || names[0] || "");
    }, () => setPrinters([]));
  }, []);

  // the material + printer's stored tests (or a fresh sheet); a list that arrives later never
  // throws away tests being typed in
  const shown = useRef<{ key: string; saved: Calibration | null }>({ key: "", saved: null });
  useEffect(() => {
    const c = all.find((x) => x.material === material && x.printer_profile === printer) ?? null;
    const key = `${material}\n${printer}`;
    const prev = shown.current;
    const switched = prev.key !== key;
    shown.current = { key, saved: c };
    setSaved(c);
    const fresh = c ? structuredClone(c.tests) : [];
    // a stored set for the new pair replaces the sheet; otherwise unsaved typing stays
    setTests((cur) => ((switched && c) || JSON.stringify(cur) === JSON.stringify(prev.saved?.tests ?? []) ? fresh : cur));
    if (switched) setNote(null);
  }, [material, printer, all]);

  const row = materials.find((m) => m.name === material) ?? null;
  const prov = row ? provenance(row) : null;
  const dirty = JSON.stringify(tests) !== JSON.stringify(saved?.tests ?? []);
  const problems = tests.map(testProblems);
  const canFit = !!material && !!printer.trim() && tests.length > 0 && problems.every((p) => p.length === 0) && busy === null;
  const fit = saved?.fit && !dirty ? saved.fit : null;
  const doneStudies = studies.filter((s) => s.status === "done" && (s.type ?? "static") === "static");

  const setTest = (i: number, k: string, v: unknown) => setTests(tests.map((t, j) => (j === i ? { ...t, [k]: v } as CalibrationTest : t)));

  const runFit = async () => {
    setBusy("fit"); setProblem(null); setNote(null);
    try {
      const c = await client().saveCalibration({ material, printer_profile: printer.trim(), tests });
      setAll((cur) => [...cur.filter((x) => x.id !== c.id), c]);
    } catch (e) { setProblem(messageOf(e)); } finally { setBusy(null); }
  };
  const apply = async () => {
    if (!saved) return;
    setBusy("apply"); setProblem(null);
    try {
      const r = await client().applyCalibration(saved.id);
      setAll((cur) => cur.map((x) => (x.material === r.calibration.material ? { ...x, applied: x.id === r.calibration.id } : x)));
      setNote(`${material} is now ${provenance(r.material as StudyMaterial).label}: new studies use the fitted values.`);
      onApplied();
    } catch (e) { setProblem(messageOf(e)); } finally { setBusy(null); }
  };

  const points = useMemo(() => {
    const per = fit?.per_test ?? [];
    const label = (i: number) => TEST_TEXT[tests[i]?.kind ?? "tensile"];
    const pick = (k: "e_mpa" | "strength_mpa"): CalPoint[] => per
      .filter((p) => typeof p[k] === "number")
      .map((p) => ({ index: p.index, label: label(p.index), value: p[k] as number }));
    return { e: pick("e_mpa"), s: pick("strength_mpa") };
  }, [fit, tests]);

  return (
    <section class="st-mat acc-cal" aria-label="Calibrate a material" data-testid="calibration">
      <header class="st-compare-head">
        <h2 class="label">Calibrate a material</h2>
        <Button variant="ghost" onClick={onClose}>Close</Button>
      </header>
      <p class="faint text acc-cal-lede">Print test coupons on your printer, break or bend them, and enter what you measured. The fit replaces the datasheet values for this material on this printer.</p>
      <div class="acc-cal-pick">
        <label class="sf-label" for="cal-mat">Material</label>
        <select id="cal-mat" value={material} data-testid="cal-material" onChange={(e) => setMaterial((e.target as HTMLSelectElement).value)}>
          {materials.map((m) => <option key={m.name} value={m.name}>{m.name} · {provenance(m).label}</option>)}
        </select>
        <label class="sf-label" for="cal-printer">Printer</label>
        <input id="cal-printer" list="cal-printers" value={printer} maxLength={60} placeholder="printer profile" data-testid="cal-printer"
          onInput={(e) => setPrinter((e.target as HTMLInputElement).value)} />
        <datalist id="cal-printers">{printers.map((p) => <option key={p} value={p} />)}</datalist>
        {prov && <span class={`acc-badge${prov.calibrated ? " is-cal" : ""}`} data-testid="cal-badge">{prov.label}</span>}
      </div>

      <table class="st-ctable acc-tests" aria-label="Tests">
        <thead><tr><th>#</th><th>test</th><th>measured</th><th>on its own</th><th /></tr></thead>
        <tbody>
          {tests.length === 0 && <tr><td colSpan={5} class="faint">No tests yet. Add a tensile bar for strength and a 3-point bend for stiffness.</td></tr>}
          {tests.map((t, i) => {
            const imp = implied(t);
            const per = fit?.per_test.find((p) => p.index === i);
            return (
              <tr key={i} data-testid="cal-test" data-kind={t.kind}>
                <td class="st-num">{i + 1}</td>
                <td class="text">{TEST_TEXT[t.kind]}</td>
                <td>
                  <span class="acc-test-inputs">
                    {t.kind === "part_load" && (
                      <select aria-label={`Test ${i + 1} study`} value={t.sim_id} onChange={(e) => setTest(i, "sim_id", (e.target as HTMLSelectElement).value)}>
                        <option value="">study of the part…</option>
                        {doneStudies.map((s) => <option key={s.sim_id} value={s.sim_id}>{s.part_id} · {s.sim_id.slice(4, 10)}</option>)}
                      </select>
                    )}
                    {INPUTS[t.kind].map((f) => {
                      const v = (t as Record<string, unknown>)[f.key];
                      return (
                        <label key={f.key} class="acc-in">
                          <span class="faint">{f.label}</span>
                          <input type="number" step="any" min={0} aria-label={`Test ${i + 1} ${f.label} (${f.unit})`}
                            value={typeof v === "number" && Number.isFinite(v) ? String(v) : ""} placeholder={f.optional ? "optional" : ""}
                            onInput={(e) => { const s = (e.target as HTMLInputElement).value; setTest(i, f.key, s === "" && f.optional ? null : Number.parseFloat(s)); }} />
                          <span class="faint">{f.unit}</span>
                        </label>
                      );
                    })}
                  </span>
                  {problems[i].length > 0 && <span class="sf-err">needs {problems[i].join(", ")}</span>}
                </td>
                <td class="st-num" data-testid="cal-implied">
                  {imp.strength_mpa !== null && <div>{sig3(imp.strength_mpa)} MPa strength</div>}
                  {imp.e_mpa !== null && <div>E {sig3(imp.e_mpa)} MPa</div>}
                  {imp.e_mpa === null && imp.strength_mpa === null && <span class="faint">{t.kind === "part_load" ? "after the fit" : "—"}</span>}
                  {per && typeof per.residual_pct === "number" && <div class="faint">{`${per.residual_pct >= 0 ? "+" : ""}${sig3(per.residual_pct)}% from the fit`}</div>}
                </td>
                <td><button type="button" class="sf-tool" aria-label={`Remove test ${i + 1}`} onClick={() => setTests(tests.filter((_, j) => j !== i))}>✕</button></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div class="acc-cal-add">
        <span class="sf-label">Add</span>
        {(Object.keys(FRESH) as CalibrationTestKind[]).map((k) => (
          <Button key={k} disabled={tests.length >= 50} data-testid={`cal-add-${k}`} onClick={() => setTests([...tests, FRESH[k]()])}>{TEST_TEXT[k]}</Button>
        ))}
      </div>
      <div class="st-actions">
        <Button variant="ink" disabled={!canFit} onClick={() => { void runFit(); }} data-testid="cal-fit">{busy === "fit" ? "Fitting…" : dirty || !saved ? "Save and fit" : "Fit again"}</Button>
        {!printer.trim() && <span class="faint">Name the printer the coupons were printed on.</span>}
      </div>
      {problem && <p class="tone-red text" role="alert">{problem}</p>}

      {fit && (
        <div class="acc-fitbox" data-testid="cal-result">
          <dl class="st-nums">
            <div><dt class="label">Young's modulus</dt><dd class="st-num st-num-big" data-testid="cal-e">{fit.e_mpa !== null ? `${sig3(fit.e_mpa)} MPa` : "not fitted"}</dd>
              {fit.datasheet?.e_mpa && fit.e_mpa !== null ? <dd class="faint">{`datasheet ${sig3(fit.datasheet.e_mpa)} · ${fit.e_mpa >= fit.datasheet.e_mpa ? "+" : ""}${sig3(((fit.e_mpa - fit.datasheet.e_mpa) / fit.datasheet.e_mpa) * 100)}%`}</dd> : null}</div>
            <div><dt class="label">strength</dt><dd class="st-num st-num-big" data-testid="cal-strength">{fit.yield_mpa !== null ? `${sig3(fit.yield_mpa)} MPa` : "not fitted"}</dd>
              {fit.datasheet?.yield_mpa && fit.yield_mpa !== null ? <dd class="faint">{`datasheet ${sig3(fit.datasheet.yield_mpa)}`}{typeof fit.knockdown === "number" ? ` · knock-down ${sig3(fit.knockdown)}` : ""}</dd> : null}</div>
            <div><dt class="label">tests · scatter</dt><dd class="st-num">{fit.n} · {fit.scatter_pct !== null ? `±${sig3(fit.scatter_pct)}%` : "n/a"}</dd>
              <dd class={fit.confidence === "low" ? "tone-amber" : "faint"} data-testid="cal-confidence">{CONFIDENCE_TEXT[fit.confidence]}</dd></div>
          </dl>
          <div class="acc-cal-charts">
            <CalScatter title="Stiffness E" unit="MPa" points={points.e} fit={fit.e_mpa} scatterPct={fit.scatter_pct} datasheet={fit.datasheet?.e_mpa ?? null} />
            <CalScatter title="Strength" unit="MPa" points={points.s} fit={fit.yield_mpa} scatterPct={null} datasheet={fit.datasheet?.yield_mpa ?? null} />
          </div>
          {fit.warnings.length > 0 && (
            <ul class="st-warnings">{fit.warnings.map((w, i) => <li key={i}><Led tone="amber" /><span class="text">{w}</span></li>)}</ul>
          )}
          <div class="st-actions">
            <Button variant={saved?.applied ? "default" : "ink"} disabled={busy !== null || saved?.applied === true} onClick={() => { void apply(); }} data-testid="cal-apply">
              {saved?.applied ? `Applied to ${material}` : `Apply to ${material}`}
            </Button>
            <span class="faint">{saved?.applied ? "New studies of this material use these values." : "Studies keep the datasheet values until you apply."}</span>
          </div>
        </div>
      )}
      {note && <p class="text" role="status" data-testid="cal-note">{note}</p>}
    </section>
  );
}
