// Plan 6 result sections on the Studies page: the nonlinear solve (did it converge, how much of
// the load it reached, peak plastic strain and contact pressure) and the fatigue estimate (cycles
// to failure against the required life, the fatigue safety factor, the stresses at the hotspot)
// with its S-N chart (S3, real shapes), and tension across the layers of a printed (orthotropic)
// material. Each carries its label ("fatigue estimate", the S-N curve's basis) in
// plain sight, like every other result. Values are core data and render as text.
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import { fatigueOf, layersOf, nonlinearOf, type FatigueResult } from "../../../../api/accuracy";
import type { StudyRecord } from "../../../../api/studies";
import { Led } from "../../../common/Led";
import { tickText } from "../charts/chartModel";
import { sig3 } from "../model";
import { cyclesShort, cyclesText, snStress } from "./model";

export function NonlinearSection({ rec }: { rec: StudyRecord }): JSX.Element | null {
  const nl = nonlinearOf(rec.results);
  if (!nl) return null;
  const what = nl.description
    ?? [nl.large_deflection && "large deflection", nl.plasticity && "plasticity", nl.contact_pairs ? "contact" : null].filter(Boolean).join(" + ");
  const pairs = nl.contact_pairs ? `${nl.contact_pairs} contact pair${nl.contact_pairs === 1 ? "" : "s"} / support${nl.contact_pairs === 1 ? "" : "s"}` : null;
  const reached = typeof nl.load_reached_pct === "number" ? nl.load_reached_pct : null;
  const partial = !nl.converged || (reached !== null && reached < 99.5);
  const done = typeof nl.increments_done === "number" ? nl.increments_done : null;
  const at = typeof nl.fields_at_fraction === "number" ? nl.fields_at_fraction : null;
  return (
    <section class="acc-res" data-testid="nonlinear-result" aria-label="Nonlinear solve">
      <h3 class="label">Nonlinear solve</h3>
      <p class="text">
        <Led tone={partial ? "amber" : "green"} />{" "}
        {partial
          ? `stopped at ${reached !== null ? `${sig3(reached)}%` : "part"} of the load: the part may buckle, yield through or lose contact before the full load${at !== null ? `; results are at ${sig3(at * 100)}% of the load` : ""}`
          : `converged at the full load${done !== null ? ` in ${done} increment${done === 1 ? "" : "s"}` : ""}${typeof nl.cutbacks === "number" && nl.cutbacks > 0 ? ` (${nl.cutbacks} cutback${nl.cutbacks === 1 ? "" : "s"})` : ""}`}
      </p>
      {(what || pairs) && <p class="faint text">{[what, pairs].filter(Boolean).join(" · ")}</p>}
      <dl class="st-nums">
        {nl.plasticity && (
          <div><dt class="label">peak plastic strain</dt><dd class="st-num">{typeof nl.max_plastic_strain === "number" ? `${sig3(nl.max_plastic_strain * 100)}%` : "n/a"}</dd>
            <dd class="faint">{nl.max_plastic_strain ? "it yields: permanent set" : "no yielding"}{nl.tangent_estimated ? " · post-yield slope estimated" : ""}</dd></div>
        )}
        {typeof nl.max_contact_pressure_mpa === "number" && (
          <div><dt class="label">peak contact pressure</dt><dd class="st-num">{sig3(nl.max_contact_pressure_mpa)} MPa</dd></div>
        )}
      </dl>
      {nl.unvalidated.length > 0 && <p class="tone-amber text" data-testid="nonlinear-unvalidated">{`Unvalidated configuration: ${nl.unvalidated.join("; ")}`}</p>}
    </section>
  );
}

/** 1-2-5 ticks on a log axis between lo and hi (at most ~5). */
export function logTicks(lo: number, hi: number): number[] {
  if (!(lo > 0) || !(hi > lo)) return [];
  const out: number[] = [];
  for (let e = Math.floor(Math.log10(lo)); e <= Math.ceil(Math.log10(hi)); e++) {
    for (const m of [1, 2, 5]) { const v = m * 10 ** e; if (v >= lo && v <= hi) out.push(Number(v.toPrecision(6))); }
  }
  return out.length > 5 ? out.filter((v) => /^1/.test(String(v)) || /^5/.test(String(v))) : out;
}

/** Log-log S-N chart (core fatigue.py: σa = A·N^b, at most the UTS): the curve, the required
 *  life, and the hotspot's equivalent amplitude at its life (a run-out sits at the right edge). */
export function SnChart({ f, width = 420, height = 180 }: { f: FatigueResult; width?: number; height?: number }): JSX.Element | null {
  const [hover, setHover] = useState<null | "point" | "req">(null);
  const sn = f.sn_curve;
  const amp = f.equivalent_amplitude_mpa ?? f.stress_amplitude_mpa ?? null;
  if (!sn || typeof amp !== "number" || !(amp > 0)) return null;
  const nf = f.cycles_to_failure;
  const req = f.cycles_required;
  const lo = 1e2; const hi = 1e12;
  const sHi = snStress(sn, lo); const sLo = Math.min(snStress(sn, hi), amp * 0.8);
  const yHi = Math.max(sHi, amp * 1.2);
  const m = { l: 44, r: 12, t: 12, b: 30 };
  const iw = width - m.l - m.r; const ih = height - m.t - m.b;
  const lx = (n: number) => m.l + ((Math.log10(n) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo))) * iw;
  const ly = (s: number) => m.t + ih - ((Math.log10(s) - Math.log10(sLo)) / (Math.log10(yHi) - Math.log10(sLo))) * ih;
  const curve: string[] = [];
  for (let e = 2; e <= 12.001; e += 0.25) curve.push(`${lx(10 ** e).toFixed(1)},${ly(snStress(sn, 10 ** e)).toFixed(1)}`);
  const decades = [2, 4, 6, 8, 10, 12];
  const yTicks = logTicks(sLo, yHi);
  const px = lx(Math.min(hi, Math.max(lo, nf)));
  const py = ly(amp);
  const pass = f.runout || nf >= req;
  const life = f.runout ? "no failure on the curve (run-out)" : `${cyclesText(nf)} cycles`;
  return (
    <figure class="sc acc-sn" data-testid="sn-chart">
      <figcaption class="sc-head"><span class="sc-title">S-N curve · {sn.basis ?? "estimate"}</span></figcaption>
      <div class="sc-plot">
        <svg width={width} height={height} role="img" tabIndex={0} data-testid="sn-svg"
          aria-label={`S-N curve. The hotspot's amplitude ${sig3(amp)} MPa lasts ${life}; ${cyclesText(req)} are required.`}
          onMouseLeave={() => setHover(null)} onBlur={() => setHover(null)} onFocus={() => setHover("point")}>
          {yTicks.map((t) => (
            <g key={t}>
              <line class="sc-grid" x1={m.l} x2={m.l + iw} y1={ly(t)} y2={ly(t)} />
              <text class="sc-tick" x={m.l - 4} y={ly(t) + 3} text-anchor="end">{tickText(t)}</text>
            </g>
          ))}
          {decades.map((e) => <text key={e} class="sc-tick" x={lx(10 ** e)} y={m.t + ih + 13} text-anchor="middle">{cyclesShort(10 ** e)}</text>)}
          <text class="sc-axis" x={m.l + iw / 2} y={height - 4} text-anchor="middle">cycles (log)</text>
          <text class="sc-axis" x={10} y={m.t + ih / 2} text-anchor="middle" transform={`rotate(-90 10 ${m.t + ih / 2})`}>amplitude (MPa)</text>
          {/* the required life: a reference line, named */}
          <g onMouseEnter={() => setHover("req")}>
            <line class="sc-marker" x1={lx(req)} x2={lx(req)} y1={m.t} y2={m.t + ih} />
            <rect x={lx(req) - 6} y={m.t} width={12} height={ih} fill="transparent" />
            <text class="sc-marker-label" x={lx(req) - 3} y={m.t + 10} text-anchor="end">required</text>
          </g>
          <polyline class="sc-line" points={curve.join(" ")} style={{ stroke: "var(--ink)" }} />
          {/* the hotspot: its amplitude across to the curve (its life) */}
          <line class="acc-sn-guide" x1={m.l} x2={px} y1={py} y2={py} />
          <g onMouseEnter={() => setHover("point")}>
            <circle cx={px} cy={py} r={10} fill="transparent" />
            <circle class={`sc-dot acc-sn-pt${pass ? "" : " is-fail"}`} cx={px} cy={py} r={4.5} data-testid="sn-point" />
          </g>
          {f.runout && <text class="sc-marker-label" x={px - 6} y={py - 6} text-anchor="end">run-out →</text>}
        </svg>
        {hover && (
          <div class="sc-tip" role="status" style={{ left: `${Math.min(Math.max(0, (hover === "req" ? lx(req) : px) + 8), width - 170)}px` }}>
            {hover === "point"
              ? <><div class="sc-tip-x">hotspot</div><div class="sc-tip-row"><b>{sig3(amp)} MPa</b><span class="faint">amplitude{f.equivalent_amplitude_mpa ? " (Goodman)" : ""}</span></div>
                <div class="sc-tip-row"><b>{f.runout ? "run-out" : cyclesText(nf)}</b><span class="faint">cycles to failure</span></div></>
              : <><div class="sc-tip-x">required life</div><div class="sc-tip-row"><b>{cyclesText(req)}</b><span class="faint">cycles</span></div>
                <div class="sc-tip-row"><b>{sig3(f.allowable_amplitude_mpa ?? snStress(sn, req))} MPa</b><span class="faint">allowed amplitude</span></div></>}
          </div>
        )}
      </div>
    </figure>
  );
}

export function FatigueSection({ rec }: { rec: StudyRecord }): JSX.Element | null {
  const f = fatigueOf(rec.results);
  if (!f) return null;
  const sf = f.safety_factor;
  const tone = sf === null ? "green" : sf < 1 ? "red" : sf < 1.5 ? "amber" : "green";
  const verdict = f.runout || f.cycles_to_failure >= f.cycles_required ? "lasts the required life" : "fails before the required life";
  return (
    <section class="acc-res" data-testid="fatigue-result" aria-label="Fatigue">
      <h3 class="label">Fatigue <span class="acc-tag" data-testid="fatigue-label">{f.label}</span></h3>
      <p class="text"><Led tone={tone} />{" "}<span class={`tone-${tone}`}>{verdict}</span>
        <span class="faint"> · {cyclesText(f.cycles_required)} cycles required{f.governing ? ` · governs: ${f.governing}` : ""}</span></p>
      <dl class="st-nums">
        <div><dt class="label">cycles to failure</dt><dd class="st-num st-num-big" data-testid="fatigue-life">{f.runout ? "run-out" : cyclesText(f.cycles_to_failure)}</dd></div>
        <div><dt class="label">fatigue safety factor</dt><dd class="st-num st-num-big">{sf === null ? "∞" : sig3(sf)}</dd><dd class="faint">on stress amplitude</dd></div>
        {typeof f.stress_amplitude_mpa === "number" && <div><dt class="label">amplitude · mean</dt><dd class="st-num">{sig3(f.stress_amplitude_mpa)} · {sig3(f.mean_stress_mpa ?? 0)} MPa</dd></div>}
      </dl>
      <SnChart f={f} />
      <p class="faint text">
        {f.sn_curve?.source ? `S-N curve (${f.sn_curve.basis ?? "estimate"}): ${f.sn_curve.source}` : "S-N curve: estimate"}
        {` · ${f.mean_stress_correction ?? "Goodman"} mean-stress correction`}
        {f.hotspot_at ? ` · hotspot at ${f.hotspot_at.map((x) => sig3(x)).join(", ")} mm` : ""}
      </p>
    </section>
  );
}

/** An orthotropic (printed) material: tension across the layers against the Z allowable. */
export function LayersSection({ rec }: { rec: StudyRecord }): JSX.Element | null {
  const l = layersOf(rec.results);
  if (!l) return null;
  const across = /across|z/i.test(l.governing_failure ?? "");
  return (
    <section class="acc-res" data-testid="layers-result" aria-label="Across the layers">
      <h3 class="label">Across the layers</h3>
      <p class="text">{across ? "Tension across the layers governs: the print is weakest there." : "In-plane stress governs; the layers hold."}
        <span class="faint">{l.governing_failure ? ` · governs: ${l.governing_failure}` : ""}</span></p>
      <dl class="st-nums">
        <div><dt class="label">peak tension across layers</dt><dd class="st-num">{typeof l.max_layer_tension_mpa === "number" ? `${sig3(l.max_layer_tension_mpa)} MPa` : "n/a"}</dd>
          {typeof l.allowable_z_mpa === "number" && <dd class="faint">Z allowable {sig3(l.allowable_z_mpa)} MPa</dd>}</div>
        <div><dt class="label">SF across layers</dt><dd class="st-num">{l.layer_safety_factor === null ? "n/a" : sig3(l.layer_safety_factor)}</dd></div>
        {typeof l.safety_factor_von_mises === "number" && <div><dt class="label">SF in-plane (von Mises)</dt><dd class="st-num">{sig3(l.safety_factor_von_mises)}</dd></div>}
      </dl>
    </section>
  );
}
