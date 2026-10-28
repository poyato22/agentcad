// A study's result: the headline safety factor (pass/warn/fail at 2.0 / 1.2) with the
// fixture-including safety factor and the peak-excludes note right next to it, the peaks, the
// error band, the solver / mesh / material label and "guide only". Failed studies show a red band.
// Every string from the core renders as text.
import type { JSX } from "preact";
import type { SimRecord } from "../../../api/sims";
import { Button } from "../../common/Button";
import { Led } from "../../common/Led";
import { BAND_WARN, fmtBand, fmtSf, SF_PASS, SF_WARN, sfVerdict, sig3, verdictTone } from "./model";

const VERDICT_TEXT = { pass: "pass", warn: "warn", fail: "fail" } as const;

export function SolverLabel({ rec }: { rec: SimRecord }): JSX.Element {
  const m = rec.material;
  const parts = [
    rec.solver ? `${rec.solver.name} ${rec.solver.version} · ${rec.solver.element}` : "CalculiX",
    rec.mesh ? `${rec.mesh.mesher} ${rec.mesh.mesher_version} mesh ${sig3(rec.mesh.fine.size_mm)} mm (coarse ${sig3(rec.mesh.coarse.size_mm)} mm, ${sig3(rec.mesh.fine.nodes)} nodes)` : null,
    `${m.name} ${m.model.split(";")[0]}, strength ×${m.knockdown} FDM knock-down`,
    `fidelity ${rec.fidelity_class}`,
  ].filter(Boolean);
  return <p class="st-solver text" data-testid="solver-label">{parts.join(" · ")}</p>;
}

export function GuideLabel({ rec }: { rec: SimRecord }): JSX.Element {
  return (
    <p class="st-guide" data-testid="guide-label">
      <span class="st-guide-tag">GUIDE ONLY</span>
      <span class="text">{rec.label}</span>
    </p>
  );
}

export function Results({ rec, onAsk, asked }: {
  rec: SimRecord; onAsk: () => void;
  asked: { runId: string | null; error: string | null } | null;
}): JSX.Element {
  const r = rec.results;
  if (rec.status !== "done" || !r) {
    const tone = rec.status === "failed" || rec.status === "interrupted" ? "red" : "faint";
    return (
      <div class="st-results">
        <GuideLabel rec={rec} />
        <div class={`st-band st-band-${tone}`} data-testid="study-failed" role={tone === "red" ? "alert" : undefined}>
          <Led tone={tone} />
          <div>
            <div class="st-band-title">
              {rec.status === "failed" ? "Study failed" : rec.status === "cancelled" ? "Study cancelled" : rec.status === "interrupted" ? "Study interrupted (Mises restarted while it ran)" : "Study running"}
            </div>
            {rec.error && <div class="text">{rec.error}</div>}
            {rec.hint && <div class="text dim">{rec.hint}</div>}
          </div>
        </div>
      </div>
    );
  }
  const v = sfVerdict(r.safety_factor);
  const vFix = sfVerdict(r.safety_factor_incl_fixture);
  const bandWarn = r.error_band !== null && r.error_band > BAND_WARN;
  return (
    <div class="st-results">
      <GuideLabel rec={rec} />
      {rec.stale && (
        <div class="st-band st-band-amber" data-testid="stale">
          <Led tone="amber" /><span>The part changed since this study: run it again for the current geometry.</span>
        </div>
      )}
      <div class="st-head">
        <div class="st-sf-top">
          <div class="st-sf" data-testid="sf" data-verdict={v ?? "none"}>
            <span class="label">Safety factor</span>
            <span class="st-sf-row">
              <Led tone={verdictTone(v)} />
              <span class={`st-sf-value tone-${verdictTone(v)}`}>{fmtSf(r.safety_factor)}</span>
              {v && <span class={`st-verdict tone-${verdictTone(v)}`}>{VERDICT_TEXT[v]}</span>}
            </span>
          </div>
          <div class="st-sf-side">
            <div data-testid="sf-incl-fixture" data-verdict={vFix ?? "none"}>
              <span class="label">incl. fixed face</span>{" "}
              <span class={`st-num tone-${verdictTone(vFix)}`}>{fmtSf(r.safety_factor_incl_fixture)}</span>
              {r.max_von_mises_at_fixture_mpa !== null && <span class="dim"> · {sig3(r.max_von_mises_at_fixture_mpa)} MPa at the fixed face</span>}
            </div>
            <div data-testid="peak-excludes" class="dim text">
              {r.peak_excludes ? `peak excludes the fixed face (${r.peak_excludes})` : "peak includes the fixed face (every stressed node is on it)"}
            </div>
          </div>
        </div>
        <p class="faint st-note">pass ≥ {SF_PASS.toFixed(1)} · warn ≥ {SF_WARN.toFixed(1)} · allowable {sig3(r.allowable_mpa)} MPa = yield {sig3(rec.material.yield_mpa)} MPa × FDM knock-down {rec.material.knockdown}</p>
        <dl class="st-nums">
          <div data-testid="max-vm"><dt class="label">max von Mises</dt><dd class="st-num">{sig3(r.max_von_mises_mpa)} MPa</dd></div>
          <div data-testid="max-disp"><dt class="label">max displacement</dt><dd class="st-num">{sig3(r.max_displacement_mm)} mm</dd></div>
          <div data-testid="error-band" data-warn={bandWarn ? "true" : "false"}>
            <dt class="label">error band</dt>
            <dd class={`st-num ${bandWarn ? "tone-amber" : ""}`}>{fmtBand(r)}</dd>
            <dd class="faint">coarse {sig3(r.coarse_max_von_mises_mpa)} → fine {sig3(r.max_von_mises_mpa)} MPa</dd>
          </div>
        </dl>
      </div>
      <SolverLabel rec={rec} />
      {rec.warnings.length > 0 && (
        <ul class="st-warnings" data-testid="warnings">
          {rec.warnings.map((w, i) => <li key={i}><Led tone="amber" /><span class="text">{w}</span></li>)}
        </ul>
      )}
      <div class="st-actions">
        <Button onClick={onAsk} disabled={asked !== null && asked.runId === null && asked.error === null}>Ask agent to fix</Button>
        {asked?.runId && <span class="dim" data-testid="asked">Sent to designer with this result: follow it in the console.</span>}
        {asked?.error && <span class="tone-red text" data-testid="asked">Could not start the designer: {asked.error}</span>}
      </div>
      <details class="st-caveats">
        <summary class="label">What this does not cover</summary>
        <ul>{rec.caveats.map((c, i) => <li key={i} class="text dim">{c}</li>)}</ul>
        {rec.environment && <p class="text dim">{rec.environment.name}: {rec.environment.notes}</p>}
      </details>
    </div>
  );
}
