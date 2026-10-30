// Material calibration (core sim/calibration.py): the user's own coupons and load tests for one
// material on one printer profile. Enter tensile bars (break force, cross-section, in-plane or
// across the layers), three-point bends (span, load, mid-span deflection) and part load tests (a
// known load on a studied part, the deflection measured at a point); each row shows what it
// implies on its own as you type. "Fit" asks the core for a preview (POST /sims/calibrations/fit,
// nothing saved): fitted E, strength and knock-down with n, scatter and confidence, and dot plots
// of every test against the fit and the datasheet. "Apply…" opens a confirmation that lists what
// changes (before → after, n, confidence, which studies); only "Confirm" saves the record (POST
// /sims/calibrations), and from then on every study of that material on that printer uses it.
import type { JSX } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import {
  CAL_KEY_RE, CAL_MAX_TESTS, calibrationFor, DEFAULT_PRINTER, provenance, type Calibration, type CalibrationFit, type CalibrationPreview,
  type CalibrationTestKind, type Orientation, type PartTest,
} from "../../../../api/accuracy";
import { CoreError, type CoreClient } from "../../../../api/client";
import type { StudyMaterial, StudySummary } from "../../../../api/studies";
import { Button } from "../../../common/Button";
import { Led } from "../../../common/Led";
import { isTrustedActivation } from "../../../console/approvals/approvalKeys";
import { sig3 } from "../model";
import { CalScatter, type CalPoint } from "./CalScatter";
import { bodyKey, bodyOf, CONFIDENCE_TEXT, eFromResidual, eRows, implied, rowProblems, rowsOf, TEST_TEXT, type SheetRow } from "./model";

export type CalibrationClient = Pick<CoreClient, "calibrations" | "fitCalibration" | "saveCalibration" | "deleteCalibration" | "printers">;

const FRESH: Record<CalibrationTestKind, () => SheetRow> = {
  tensile: () => ({ kind: "tensile", t: { force_n: Number.NaN, width_mm: 10, thickness_mm: 4, orientation: "xy" } }),
  bend: () => ({ kind: "bend", t: { span_mm: 64, width_mm: 10, thickness_mm: 4, load_n: Number.NaN, deflection_mm: Number.NaN, orientation: "xy" } }),
  part: () => ({ kind: "part", t: { sim_id: "", point: [Number.NaN, Number.NaN, Number.NaN], load_n: Number.NaN, deflection_mm: Number.NaN } }),
};

/** Each kind's number inputs: key, label, unit. */
const INPUTS: Record<CalibrationTestKind, { key: string; label: string; unit: string; optional?: boolean }[]> = {
  tensile: [
    { key: "force_n", label: "break force", unit: "N" }, { key: "width_mm", label: "width", unit: "mm" },
    { key: "thickness_mm", label: "thickness", unit: "mm" }, { key: "elongation_pct", label: "elongation", unit: "%", optional: true },
  ],
  bend: [
    { key: "span_mm", label: "span", unit: "mm" }, { key: "width_mm", label: "width", unit: "mm" }, { key: "thickness_mm", label: "thickness", unit: "mm" },
    { key: "load_n", label: "load", unit: "N" }, { key: "deflection_mm", label: "deflection", unit: "mm" },
    { key: "break_load_n", label: "break load", unit: "N", optional: true },
  ],
  part: [{ key: "load_n", label: "load", unit: "N" }, { key: "deflection_mm", label: "measured deflection", unit: "mm" }],
};

const messageOf = (e: unknown): string => (e instanceof CoreError ? `${e.message}${e.hint ? ` · ${e.hint}` : ""}` : e instanceof Error ? e.message : String(e));
const fmtPct = (x: number | null | undefined) => (typeof x === "number" ? `±${sig3(x * 100)}%` : "n/a");
const diffPct = (a: number, b: number) => `${b >= a ? "+" : ""}${sig3(((b - a) / a) * 100)}%`;

/** What applying changes, one row per value the fit replaces (before = what studies use now). */
function changes(fit: CalibrationFit, before: { e_mpa: number; yield_mpa: number; knockdown: number; z_strength_mpa?: number | null }, after: CalibrationPreview["material"]) {
  const rows: { what: string; before: string; after: string }[] = [];
  if (fit.e) rows.push({ what: "Young's modulus", before: `${sig3(before.e_mpa)} MPa`, after: `${sig3(after.e_mpa)} MPa` });
  if (fit.strength) rows.push({ what: "strength", before: `${sig3(before.yield_mpa)} MPa`, after: `${sig3(after.yield_mpa)} MPa` });
  if (typeof fit.knockdown === "number") rows.push({ what: "knock-down", before: sig3(before.knockdown), after: sig3(after.knockdown) });
  if (fit.z_strength) rows.push({ what: "strength across layers", before: typeof before.z_strength_mpa === "number" ? `${sig3(before.z_strength_mpa)} MPa` : "typical ratio (estimate)", after: `${sig3(fit.z_strength.value_mpa)} MPa` });
  if (fit.e_z) rows.push({ what: "E across layers", before: "typical ratio (estimate)", after: `${sig3(fit.e_z.value_mpa)} MPa` });
  return rows;
}

export function CalibrationPanel({ client, materials, studies, initialMaterial, initialPrinter, onApplied, onClose }: {
  client: () => CalibrationClient;
  materials: readonly StudyMaterial[];
  /** Finished static studies a part load test can compare against. */
  studies: readonly StudySummary[];
  initialMaterial?: string | null;
  initialPrinter?: string | null;
  /** The record is saved: the page points its study at this printer and re-reads the calibrations. */
  onApplied: (material: string, printer: string, cal: Calibration) => void;
  onClose: () => void;
}): JSX.Element {
  const [material, setMaterial] = useState(initialMaterial ?? materials[0]?.name ?? "");
  const [printer, setPrinter] = useState(initialPrinter || DEFAULT_PRINTER);
  const [printers, setPrinters] = useState<string[]>([]);
  const [all, setAll] = useState<Calibration[]>([]);
  const [rows, setRows] = useState<SheetRow[]>([]);
  // the user typed since the sheet was last loaded from a saved record
  const [edited, setEdited] = useState(false);
  const [preview, setPreview] = useState<{ key: string; p: CalibrationPreview } | null>(null);
  const [confirm, setConfirm] = useState<"apply" | "remove" | null>(null);
  const [busy, setBusy] = useState<"fit" | "apply" | "remove" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    void client().calibrations().then(setAll, (e: unknown) => setProblem(`Could not read the calibrations: ${messageOf(e)}`));
    void client().printers().then((ps) => setPrinters(ps.map((p) => p.name)), () => setPrinters([]));
  }, []);

  const printerOk = CAL_KEY_RE.test(printer.trim());
  const saved = printerOk ? calibrationFor(all, material, printer) : null;
  // a material + printer with a saved record shows its tests, unless the user is typing a new sheet
  useEffect(() => {
    if (edited) return;
    setRows(saved ? rowsOf(saved) : []);
    setConfirm(null);
  }, [material, printer, saved?.id, saved?.updated_at]);

  const body = bodyOf(material, printer, rows);
  const key = bodyKey(body);
  const savedKey = saved ? bodyKey(bodyOf(saved.material, saved.printer, rowsOf(saved))) : null;
  const isSaved = savedKey !== null && savedKey === key;
  const shown = preview && preview.key === key ? preview.p : null;
  const fit: CalibrationFit | null = shown?.fit ?? (isSaved ? saved?.fit ?? null : null);
  const row = materials.find((m) => m.name === material) ?? null;
  const datasheet = shown?.datasheet ?? (row ? { name: row.name, e_mpa: row.e_mpa, yield_mpa: row.yield_mpa, knockdown: row.knockdown ?? 0.7 } : null);
  const prov = row ? provenance(row, saved) : null;
  const problems = rows.map(rowProblems);
  const counts = { tensile: rows.filter((r) => r.kind === "tensile").length, bend: rows.filter((r) => r.kind === "bend").length, part: rows.filter((r) => r.kind === "part").length };
  const canFit = !!material && printerOk && rows.length > 0 && problems.every((p) => p.length === 0) && busy === null;
  const doneStudies = studies.filter((s) => s.status === "done" && (s.type ?? "static") === "static");

  const edit = (next: SheetRow[]) => { setRows(next); setEdited(true); setConfirm(null); setNote(null); };
  const setField = (i: number, k: string, v: unknown) => edit(rows.map((r, j) => (j === i ? { ...r, t: { ...r.t, [k]: v } } as SheetRow : r)));

  const runFit = async () => {
    setBusy("fit"); setProblem(null); setNote(null);
    try {
      const p = await client().fitCalibration(body);
      setPreview({ key, p });
    } catch (e) { setProblem(messageOf(e)); } finally { setBusy(null); }
  };
  const apply = async (e: MouseEvent) => {
    if (!isTrustedActivation(e) || !shown) return;
    setBusy("apply"); setProblem(null);
    try {
      const c = await client().saveCalibration(body);
      setAll((cur) => [...cur.filter((x) => x.id !== c.id), c]);
      setEdited(false); setConfirm(null);
      setNote(`${c.material} on ${c.printer} is now ${c.basis}: new studies of it on this printer use the fitted values.`);
      onApplied(c.material, c.printer, c);
    } catch (err) { setProblem(messageOf(err)); } finally { setBusy(null); }
  };
  const remove = async (e: MouseEvent) => {
    if (!isTrustedActivation(e) || !saved) return;
    setBusy("remove"); setProblem(null);
    try {
      await client().deleteCalibration(saved.id);
      setAll((cur) => cur.filter((x) => x.id !== saved.id));
      setConfirm(null); setEdited(false);
      setNote(`${saved.material} on ${saved.printer} is back to its datasheet values.`);
    } catch (err) { setProblem(messageOf(err)); } finally { setBusy(null); }
  };

  const points = useMemo(() => {
    if (!fit) return { e: [] as CalPoint[], s: [] as CalPoint[] };
    const e: CalPoint[] = [];
    if (fit.e) {
      eRows(rows, fit).forEach((ri, k) => {
        const v = eFromResidual(fit.e!.value_mpa, fit.e!.residuals[k] ?? 0);
        if (v !== null) e.push({ index: ri, label: TEST_TEXT[rows[ri].kind], value: v });
      });
    }
    const s: CalPoint[] = [];
    rows.forEach((r, i) => {
      if (r.kind !== "tensile" || (r.t.orientation ?? "xy") !== "xy") return;
      const v = implied(r).strength_mpa;
      if (v !== null) s.push({ index: i, label: TEST_TEXT.tensile, value: v });
    });
    return { e, s };
  }, [fit, rows]);

  const before = saved?.material_preview ?? (row ? { e_mpa: row.e_mpa, yield_mpa: row.yield_mpa, knockdown: row.knockdown ?? 0.7, z_strength_mpa: (row as Record<string, unknown>).z_strength_mpa as number | null } : null);

  return (
    <section class="st-mat acc-cal" aria-label="Calibrate a material" data-testid="calibration">
      <header class="st-compare-head">
        <h2 class="label">Calibrate a material</h2>
        <Button variant="ghost" onClick={onClose}>Close</Button>
      </header>
      <p class="faint text acc-cal-lede">Print test coupons on your printer, break or bend them, and enter what you measured. Applied, the fit replaces the datasheet values for this material on this printer.</p>
      <div class="acc-cal-pick">
        <label class="sf-label" for="cal-mat">Material</label>
        <select id="cal-mat" value={material} data-testid="cal-material" onChange={(e) => { setMaterial((e.target as HTMLSelectElement).value); setEdited(false); }}>
          {materials.map((m) => <option key={m.name} value={m.name}>{m.name} · {provenance(m, calibrationFor(all, m.name, printer)).label}</option>)}
        </select>
        <label class="sf-label" for="cal-printer">Printer</label>
        <input id="cal-printer" list="cal-printers" value={printer} maxLength={40} placeholder={DEFAULT_PRINTER} data-testid="cal-printer"
          aria-invalid={printerOk ? undefined : "true"} aria-describedby={printerOk ? undefined : "cal-printer-err"}
          onInput={(e) => { setPrinter((e.target as HTMLInputElement).value); setEdited(false); }} />
        <datalist id="cal-printers">
          {[...new Set([DEFAULT_PRINTER, ...printers.filter((p) => CAL_KEY_RE.test(p)), ...all.map((c) => c.printer)])].map((p) => <option key={p} value={p} />)}
        </datalist>
        {prov && <span class={`acc-badge${prov.calibrated ? " is-cal" : ""}`} data-testid="cal-badge">{prov.label}</span>}
        {!printerOk && <span class="sf-err" id="cal-printer-err" role="alert">1–40 letters, digits, spaces or ._+()- (starting with a letter or digit)</span>}
      </div>

      <table class="st-ctable acc-tests" aria-label="Tests">
        <thead><tr><th>#</th><th>test</th><th>measured</th><th>on its own</th><th /></tr></thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan={5} class="faint">No tests yet. Add a tensile bar for strength and a 3-point bend for stiffness.</td></tr>}
          {rows.map((r, i) => {
            const imp = implied(r);
            const t = r.t as Record<string, unknown>;
            const ek = fit?.e ? eRows(rows, fit).indexOf(i) : -1;
            const resid = ek >= 0 ? fit?.e?.residuals[ek] : undefined;
            return (
              <tr key={i} data-testid="cal-test" data-kind={r.kind}>
                <td class="st-num">{i + 1}</td>
                <td class="text">
                  {TEST_TEXT[r.kind]}
                  {r.kind !== "part" && (
                    <select class="acc-orient" aria-label={`Test ${i + 1} printed`} value={(t.orientation as Orientation | undefined) ?? "xy"}
                      onChange={(e) => setField(i, "orientation", (e.target as HTMLSelectElement).value)}>
                      <option value="xy">flat (along layers)</option>
                      <option value="z">upright (across layers)</option>
                    </select>
                  )}
                </td>
                <td>
                  <span class="acc-test-inputs">
                    {r.kind === "part" && <PartInputs i={i} t={r.t} studies={doneStudies} set={(k, v) => setField(i, k, v)} />}
                    {INPUTS[r.kind].map((f) => {
                      const v = t[f.key];
                      return (
                        <label key={f.key} class="acc-in">
                          <span class="faint">{f.label}</span>
                          <input type="number" step="any" min={0} aria-label={`Test ${i + 1} ${f.label} (${f.unit})`}
                            value={typeof v === "number" && Number.isFinite(v) ? String(v) : ""} placeholder={f.optional ? "optional" : ""}
                            onInput={(e) => { const s = (e.target as HTMLInputElement).value; setField(i, f.key, s === "" && f.optional ? null : Number.parseFloat(s)); }} />
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
                  {imp.e_mpa === null && imp.strength_mpa === null && <span class="faint">{r.kind === "part" ? "after the fit" : "—"}</span>}
                  {typeof resid === "number" && <div class="faint">{`${resid >= 0 ? "+" : ""}${sig3(resid * 100)}% deflection vs the fit`}</div>}
                </td>
                <td><button type="button" class="sf-tool" aria-label={`Remove test ${i + 1}`} onClick={() => edit(rows.filter((_, j) => j !== i))}>✕</button></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div class="acc-cal-add">
        <span class="sf-label">Add</span>
        {(Object.keys(FRESH) as CalibrationTestKind[]).map((k) => (
          <Button key={k} disabled={counts[k] >= CAL_MAX_TESTS} data-testid={`cal-add-${k}`} onClick={() => edit([...rows, FRESH[k]()])}>{TEST_TEXT[k]}</Button>
        ))}
      </div>
      <div class="st-actions">
        <Button variant="ink" disabled={!canFit} onClick={() => { void runFit(); }} data-testid="cal-fit">{busy === "fit" ? "Fitting…" : "Fit"}</Button>
        <span class="faint">Fitting saves nothing: you see the result first.</span>
      </div>
      {problem && <p class="tone-red text" role="alert">{problem}</p>}

      {fit && datasheet && (
        <div class="acc-fitbox" data-testid="cal-result" data-state={isSaved && !shown ? "saved" : "preview"}>
          <dl class="st-nums">
            <div><dt class="label">Young's modulus</dt><dd class="st-num st-num-big" data-testid="cal-e">{fit.e ? `${sig3(fit.e.value_mpa)} MPa` : "not fitted"}</dd>
              {fit.e && <dd class="faint">{`datasheet ${sig3(datasheet.e_mpa)} · ${diffPct(datasheet.e_mpa, fit.e.value_mpa)} · n ${fit.e.n}`}</dd>}</div>
            <div><dt class="label">strength</dt><dd class="st-num st-num-big" data-testid="cal-strength">{fit.strength ? `${sig3(fit.strength.value_mpa)} MPa` : "not fitted"}</dd>
              {fit.strength && <dd class="faint">{`datasheet ${sig3(datasheet.yield_mpa)}`}{typeof fit.knockdown === "number" ? ` · knock-down ${sig3(fit.knockdown)}` : ""}</dd>}</div>
            <div><dt class="label">tests · scatter</dt><dd class="st-num">{fit.n_tests} · {fmtPct(fit.e?.cv ?? fit.strength?.cv)}</dd>
              <dd class={fit.confidence === "low" ? "tone-amber" : "faint"} data-testid="cal-confidence">{CONFIDENCE_TEXT[fit.confidence]}</dd></div>
            {(fit.z_strength || fit.e_z) && (
              <div><dt class="label">across layers</dt>
                <dd class="st-num">{[fit.e_z && `E ${sig3(fit.e_z.value_mpa)}`, fit.z_strength && `strength ${sig3(fit.z_strength.value_mpa)}`].filter(Boolean).join(" · ")} MPa</dd></div>
            )}
          </dl>
          <div class="acc-cal-charts">
            <CalScatter title="Stiffness E" unit="MPa" points={points.e} fit={fit.e?.value_mpa ?? null} scatterPct={typeof fit.e?.cv === "number" ? fit.e.cv * 100 : null} datasheet={datasheet.e_mpa} />
            <CalScatter title="Strength" unit="MPa" points={points.s} fit={fit.strength?.value_mpa ?? null} scatterPct={typeof fit.strength?.cv === "number" ? fit.strength.cv * 100 : null} datasheet={datasheet.yield_mpa} />
          </div>
          {fit.part_tests && fit.part_tests.length > 0 && (
            <p class="faint text">{fit.part_tests.map((p) => `${p.sim_id}: E ${sig3(p.e_mpa)} MPa (measured ${sig3(p.measured_deflection_mm)} vs predicted ${sig3(p.predicted_deflection_mm)} mm)`).join(" · ")}</p>
          )}
          {fit.confidence === "low" && <ul class="st-warnings"><li><Led tone="amber" /><span class="text">Few tests or a wide scatter: the fit may not hold for your next print. Add tests before relying on it.</span></li></ul>}

          {isSaved ? (
            <div class="st-actions">
              <span class="text" data-testid="cal-applied">{`Applied: ${saved?.basis} on ${saved?.printer}.`}</span>
              {confirm === "remove" ? (
                <>
                  <Button variant="danger" disabled={busy !== null} onClick={(e) => { void remove(e as unknown as MouseEvent); }} data-testid="cal-remove-confirm">Remove the calibration</Button>
                  <Button variant="ghost" onClick={() => setConfirm(null)}>Keep it</Button>
                </>
              ) : <Button variant="ghost" onClick={() => setConfirm("remove")} data-testid="cal-remove">Remove…</Button>}
            </div>
          ) : shown && (confirm === "apply" && before ? (
            <div class="acc-confirm" role="group" aria-label="Confirm the calibration" data-testid="cal-confirm">
              <p class="text"><b>{`Apply to ${material} on ${printer.trim()}?`}</b>{` Every new study of ${material} on this printer will use these values.`}</p>
              <table class="st-ctable" aria-label="What changes">
                <thead><tr><th /><th>now</th><th>after</th></tr></thead>
                <tbody>{changes(shown.fit, before, shown.material).map((c) => <tr key={c.what}><td>{c.what}</td><td>{c.before}</td><td><b>{c.after}</b></td></tr>)}</tbody>
              </table>
              <p class={shown.fit.confidence === "low" ? "tone-amber text" : "text"} data-testid="cal-confirm-basis">{`${shown.basis} · ${CONFIDENCE_TEXT[shown.fit.confidence]}`}</p>
              <div class="st-actions">
                <Button variant="ink" disabled={busy !== null} onClick={(e) => { void apply(e as unknown as MouseEvent); }} data-testid="cal-confirm-apply">{busy === "apply" ? "Applying…" : "Confirm"}</Button>
                <Button variant="ghost" onClick={() => setConfirm(null)} data-testid="cal-confirm-cancel">Cancel</Button>
              </div>
            </div>
          ) : (
            <div class="st-actions">
              <Button variant="ink" disabled={busy !== null} onClick={() => setConfirm("apply")} data-testid="cal-apply">{`Apply to ${material}…`}</Button>
              <span class="faint">{saved ? `Replaces the saved calibration (${saved.basis}).` : "Studies keep the datasheet values until you confirm."}</span>
            </div>
          ))}
        </div>
      )}
      {note && <p class="text" role="status" data-testid="cal-note">{note}</p>}
    </section>
  );
}

/** A part load test: which study, where the deflection was measured (a point on the part). */
function PartInputs({ i, t, studies, set }: { i: number; t: PartTest; studies: readonly StudySummary[]; set: (k: string, v: unknown) => void }): JSX.Element {
  const s = studies.find((x) => x.sim_id === t.sim_id);
  const maxAt = (s?.results as { max_displacement_at?: unknown } | null | undefined)?.max_displacement_at;
  const pt = Array.isArray(t.point) ? t.point : [Number.NaN, Number.NaN, Number.NaN];
  return (
    <>
      <select aria-label={`Test ${i + 1} study`} value={t.sim_id} onChange={(e) => set("sim_id", (e.target as HTMLSelectElement).value)}>
        <option value="">study of the part…</option>
        {studies.map((x) => <option key={x.sim_id} value={x.sim_id}>{x.part_id} · {x.label} · {x.sim_id.slice(4, 10)}</option>)}
      </select>
      <span class="acc-in" role="group" aria-label={`Test ${i + 1} measured at (mm)`}>
        <span class="faint">at</span>
        {(["x", "y", "z"] as const).map((ax, k) => (
          <input key={ax} type="number" step="any" class="acc-pt" aria-label={`Test ${i + 1} point ${ax} (mm)`} placeholder={ax}
            value={Number.isFinite(pt[k]) ? String(pt[k]) : ""}
            onInput={(e) => { const next = [...pt] as [number, number, number]; next[k] = Number.parseFloat((e.target as HTMLInputElement).value); set("point", next); }} />
        ))}
        <span class="faint">mm</span>
      </span>
      {Array.isArray(maxAt) && maxAt.length === 3 && (
        <button type="button" class="scard-link" data-testid={`cal-point-max-${i}`} onClick={() => set("point", maxAt.map(Number))}>
          the study's point of max deflection
        </button>
      )}
    </>
  );
}
