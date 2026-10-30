// A study's result on the Studies page, for every type: the verdict, "GUIDE ONLY" with the
// fidelity note up front (wind: "low fidelity · ±X% (validated)" and the skin-friction note),
// the key metrics (wind: the Cd / Cl readout), the requirement checks, every chart (the yaw sweep,
// the excitation band, the stress histogram …), the solver / mesh / material label, warnings,
// caveats, and "Immersive" / "Ask agent to fix". Failed studies show a red band. Text only.
import type { JSX } from "preact";
import { useMemo, useState } from "preact/hooks";
import { provenance } from "../../../api/accuracy";
import type { StudyCharts, StudyRecord } from "../../../api/studies";
import { FatigueSection, LayersSection, NonlinearSection } from "./accuracy/results";
import { Button } from "../../common/Button";
import { Led } from "../../common/Led";
import { CheckRows } from "../../console/studycard/StudyCard";
import { StudyChart } from "./charts/StudyChart";
import { fidelityText, fmtStudyMetric, loadCases, metricLabel, sig3, summaryMetrics, typeName, VERDICT_TEXT, verdictTone } from "./model";

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

/** "CalculiX 2.23 · C3D10 · gmsh mesh 1.5 mm (6598 nodes) · PLA isotropic …" / the lattice. */
export function solverText(rec: StudyRecord): string {
  const sv = isObj(rec.solver) ? rec.solver : {};
  const mesh = isObj(rec.mesh) ? rec.mesh : {};
  const bits: string[] = [];
  const name = [sv.name, sv.version].filter((x) => typeof x === "string").join(" ");
  if (name) bits.push(name);
  for (const k of ["procedure", "element", "method"]) if (typeof sv[k] === "string") bits.push(sv[k] as string);
  const fine = isObj(mesh.fine) ? mesh.fine : null;
  if (fine && typeof fine.size_mm === "number") bits.push(`mesh ${sig3(fine.size_mm)} mm${typeof fine.nodes === "number" ? ` (${fine.nodes} nodes)` : ""}`);
  const lat = isObj(mesh.lattice) ? mesh.lattice : null;
  if (lat) bits.push(`lattice ${String(lat.cells_across ?? "?")} cells across, Re_eff ${sig3(Number(lat.reynolds_effective))}, blockage ${sig3(Number(lat.blockage_pct))}%`);
  const m = rec.material;
  if (m) bits.push(`${m.name} (${provenance(m).label})${typeof m.model === "string" ? ` ${m.model}` : ""}`);
  bits.push(`fidelity ${rec.fidelity_class ?? "?"}`);
  return bits.join(" · ");
}

/** Several load cases (B1-3): which one governs the metrics above, and each case's own peak
 *  stress, safety factor and displacement. Picking a case shows its figures; the contour in the
 *  viewer is the governing case's (the core stores the field for that case only). */
export function LoadCases({ rec }: { rec: StudyRecord }): JSX.Element | null {
  const rows = useMemo(() => loadCases(rec as StudyRecord & Record<string, unknown>), [rec]);
  const [picked, setPicked] = useState<string | null>(null);
  if (!rows) return null;
  const gov = rows.find((x) => x.governing) ?? null;
  const sel = rows.find((x) => x.id === picked) ?? gov ?? rows[0];
  return (
    <div class="st-cases" data-testid="result-cases" data-governing={gov?.id}>
      <p class="text" data-testid="governing-case">
        {gov ? <>Governing case: <b>{gov.name}</b> <span class="faint">· the figures above and the contour are this case</span></> : "No governing case reported."}
      </p>
      <table class="st-cases-table" aria-label="Load cases">
        <thead><tr><th>case</th><th>peak von Mises</th><th>SF</th><th>max disp</th><th>mesh band</th></tr></thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.id} data-case={c.id} data-selected={c.id === sel.id ? "" : undefined} data-governing={c.governing ? "" : undefined}>
              <td>
                <button type="button" class="st-case-pick" aria-pressed={c.id === sel.id} onClick={() => setPicked(c.id)}>
                  {c.name}{c.governing && <span class="faint"> · governs</span>}
                </button>
              </td>
              <td class="st-num">{c.maxVm !== null ? `${sig3(c.maxVm)} MPa` : "n/a"}</td>
              <td class="st-num">{c.sf !== null ? sig3(c.sf) : "n/a"}</td>
              <td class="st-num">{c.dispMm !== null ? `${sig3(c.dispMm)} mm` : "n/a"}</td>
              <td class="st-num">{c.bandPct !== null ? `±${sig3(c.bandPct)}%` : "n/a"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!sel.governing && (
        <p class="faint text" data-testid="case-note">{`${sel.name}: SF ${sel.sf !== null ? sig3(sel.sf) : "n/a"}, peak ${sel.maxVm !== null ? `${sig3(sel.maxVm)} MPa` : "n/a"}. The viewer shows the governing case's contour.`}</p>
      )}
    </div>
  );
}

export function ResultPanel({ rec, charts, onAsk, onOpenRun, asked, onImmersive }: {
  rec: StudyRecord; charts: StudyCharts | null; onAsk: () => void; onOpenRun: (runId: string) => void; onImmersive: () => void;
  asked: { runId: string | null; error: string | null } | null;
}): JSX.Element {
  const card = rec.card;
  const type = rec.type ?? "static";
  const done = rec.status === "done";
  // a study you cancelled has no verdict (the core's card still says "fail"): it reads "cancelled" (B1-10)
  const cancelled = rec.status === "cancelled";
  const verdict = cancelled ? null : card?.verdict ?? (done ? null : "fail");
  const r = (rec.results ?? {}) as Record<string, unknown>;
  return (
    <div class="st-results" data-testid="study-result" data-type={type} data-status={rec.status}>
      <div class="st-rhead">
        <span class="st-rtype">{typeName(type)}</span>
        <span class="faint text">{rec.part_id}</span>
        {rec.reason && <span class="text dim">· {rec.reason}</span>}
        <span class="st-rverdict">
          <Led tone={verdictTone(verdict)} />
          <span class={`st-verdict tone-${verdictTone(verdict)}`} data-testid="verdict">{verdict ? VERDICT_TEXT[verdict] : rec.status}</span>
        </span>
      </div>
      <p class="st-guide" data-testid="guide-label">
        <span class="st-guide-tag">GUIDE ONLY</span>
        <span class="st-fidelity text" data-testid="fidelity-note">{card ? fidelityText(card) : rec.fidelity_class}</span>
        <span class="faint text">{rec.label}</span>
      </p>
      {card?.notes.map((n, i) => <p key={i} class="st-note dim text" data-testid="result-note">{n}</p>)}
      {rec.stale && (
        <div class="st-band st-band-amber" data-testid="stale"><Led tone="amber" /><span>The part changed since this study. Run it again.</span></div>
      )}
      {!done && (
        <div class={`st-band st-band-${rec.status === "cancelled" ? "faint" : "red"}`} data-testid="study-failed" role={rec.status === "failed" ? "alert" : undefined}>
          <Led tone={rec.status === "cancelled" ? "faint" : "red"} />
          <div>
            <div class="st-band-title">{rec.status === "failed" ? "Study failed" : rec.status === "cancelled" ? "Study cancelled" : rec.status === "interrupted" ? "Study interrupted (Mises restarted while it ran)" : "Study running"}</div>
            {rec.error && rec.error !== "cancelled" && <div class="text">{rec.error}</div>}
            {rec.hint && <div class="text dim">{rec.hint}</div>}
          </div>
        </div>
      )}
      {done && card && (
        <dl class="st-nums st-metrics" data-testid="metrics">
          {summaryMetrics(card.metrics, rec.results).map((m) => (
            <div key={m.key} data-testid="metric" data-key={m.key}>
              <dt class="label">{metricLabel(m, type)}</dt>
              <dd class={`st-num${m.key === "cd" || m.key === "safety_factor" || m.key === "first_freq_hz" || m.key === "first_factor" || m.key === "max_temp_c" ? " st-num-big" : ""}`}>{fmtStudyMetric(m, rec.warnings)}</dd>
            </div>
          ))}
          {type === "wind" && typeof r.convergence_band_pct === "number" && (
            <div data-testid="convergence-band"><dt class="label">wake unsteadiness</dt><dd class="st-num">±{sig3(r.convergence_band_pct)}%</dd><dd class="faint">not the accuracy</dd></div>
          )}
          {type === "wind" && typeof r.reynolds === "number" && <div><dt class="label">Reynolds</dt><dd class="st-num">{sig3(r.reynolds)}</dd></div>}
        </dl>
      )}
      {done && type === "static" && <LoadCases rec={rec} />}
      {done && <NonlinearSection rec={rec} />}
      {done && <LayersSection rec={rec} />}
      {done && <FatigueSection rec={rec} />}
      <CheckRows checks={card?.checks ?? []} />
      {done && charts && charts.charts.length > 0 && (
        <div class="st-charts" data-testid="charts">
          {charts.charts.map((c) => <div key={c.id} class="st-chart"><StudyChart chart={c} onOpen={onImmersive} /></div>)}
        </div>
      )}
      {done && <p class="st-solver text" data-testid="solver-label">{solverText(rec)}</p>}
      {rec.warnings.length > 0 && (
        <ul class="st-warnings" data-testid="warnings">
          {rec.warnings.map((w, i) => <li key={i}><Led tone="amber" /><span class="text">{w}</span></li>)}
        </ul>
      )}
      <div class="st-actions">
        <Button variant="ink" onClick={onImmersive} data-testid="open-immersive">Immersive</Button>
        {!cancelled && <Button onClick={onAsk} disabled={asked !== null && asked.error === null} data-testid="ask-agent">Ask agent to fix</Button>}
        {asked?.runId && (
          <>
            <span class="dim" data-testid="asked">Sent to designer with the full settings and result.</span>
            <Button variant="ghost" onClick={() => { if (asked.runId) onOpenRun(asked.runId); }}>Open run</Button>
          </>
        )}
        {asked?.error && <span class="tone-red text" data-testid="asked">Could not start the designer: {asked.error}</span>}
      </div>
      {rec.caveats.length > 0 && (
        <details class="st-caveats">
          <summary class="label">What this does not cover</summary>
          <ul>{rec.caveats.map((c, i) => <li key={i} class="text dim">{c}</li>)}</ul>
        </details>
      )}
    </div>
  );
}
