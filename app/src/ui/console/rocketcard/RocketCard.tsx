// Plan 11 F6: a rocket_analyze / rocket_flight result in the Simulations stream: the verdict, the
// key numbers (margin, CP / CG, apogee, times, payload g), the charts (margin through the burn,
// altitude / speed / acceleration) with the target band, the per-motor table or the Barrowman
// terms and flutter rows, the warnings, and the accuracy notes behind a toggle. Every string from
// the core renders as text.
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import { Led } from "../../common/Led";
import { StudyChart } from "../../pages/studies/charts/StudyChart";
import { verdictTone } from "../../pages/studies/model";
import type { LogItem } from "../logModel";
import { fmtMetric, rocketCallView, type RocketCallView, type RocketStatus } from "./model";
import "./rocketcard.css";

type Call = Extract<LogItem, { kind: "call" }>;

const tone = (s: RocketStatus | null | undefined) => verdictTone(s === "not_checked" ? null : s ?? null);
const TEXT: Record<RocketStatus, string> = { pass: "pass", warn: "warn", fail: "fail", not_checked: "not checked" };

export function RocketCardSlot({ call }: { call: Call }): JSX.Element | null {
  const v = rocketCallView(call);
  return v ? <div class="scard-slot" data-rocket-slot><RocketCard view={v} /></div> : null;
}

function Status({ s }: { s: RocketStatus | null | undefined }): JSX.Element {
  return <span class="rcard-st"><Led tone={tone(s)} /><span class={`scard-verdict tone-${tone(s)}`}>{s ? TEXT[s] : ""}</span></span>;
}

function FlightTable({ v }: { v: RocketCallView }): JSX.Element | null {
  if (v.flights.length === 0) return null;
  return (
    <table class="rcard-table" data-testid="rocket-flights">
      <thead><tr><th>motor</th><th>apogee</th><th>to apogee</th><th>flight</th><th>max</th><th>peak</th><th>margin</th><th /></tr></thead>
      <tbody>
        {v.flights.map((f) => (
          <tr key={f.motor} data-motor={f.motor} data-status={f.status}>
            <th scope="row" class="text">{f.motor}</th>
            <td>{f.apogee_ft} ft<span class="faint"> · {f.apogee_m} m</span>{f.apogee_error_ft !== undefined && <span class="faint"> ({f.apogee_error_ft > 0 ? "+" : ""}{f.apogee_error_ft})</span>}</td>
            <td>{f.t_apogee_s} s</td>
            <td>{f.flight_time_s} s</td>
            <td>{f.max_v_mps} m/s</td>
            <td>{f.payload_peak_g} g</td>
            <td>{f.margin_cal} cal</td>
            <td><Status s={f.status} /></td>
          </tr>
        ))}
      </tbody>
      {v.flights.some((f) => f.tune || f.chute_for_time_mm) && (
        <tfoot>
          {v.flights.filter((f) => f.tune || f.chute_for_time_mm).map((f) => (
            <tr key={`t-${f.motor}`}><td colSpan={8} class="faint text" data-testid="rocket-tune">
              {f.motor}: {f.tune ? (f.tune.reachable
                ? (f.tune.note ?? `${(f.tune.ballast_g ?? 0) > 0 ? "+" : ""}${f.tune.ballast_g} g ballast hits the target${f.tune.margin_launch_cal !== undefined ? ` (margin ${f.tune.margin_launch_cal} cal)` : ""}`)
                : f.tune.note ?? "the target is out of reach") : ""}
              {f.chute_for_time_mm ? `${f.tune ? "; " : ""}a ${f.chute_for_time_mm} mm chute lands it in the time window` : ""}
            </td></tr>
          ))}
        </tfoot>
      )}
    </table>
  );
}

function StabilityTables({ v }: { v: RocketCallView }): JSX.Element | null {
  if (v.terms.length === 0 && v.flutter.length === 0) return null;
  return (
    <div class="rcard-tables">
      {v.terms.length > 0 && (
        <table class="rcard-table" data-testid="rocket-terms">
          <thead><tr><th>Barrowman term</th><th>CN alpha</th><th>CP from tip</th></tr></thead>
          <tbody>{v.terms.map((t) => <tr key={t.term}><th scope="row" class="text">{t.term}</th><td>{t.cn_alpha}</td><td>{t.x_mm} mm</td></tr>)}</tbody>
        </table>
      )}
      {v.flutter.length > 0 && (
        <table class="rcard-table" data-testid="rocket-flutter">
          <thead><tr><th>fins</th><th>flutter</th><th>max speed</th><th /></tr></thead>
          <tbody>{v.flutter.map((f) => (
            <tr key={f.fins} data-status={f.status}><th scope="row" class="text">{f.fins}</th><td>{f.flutter_mps} m/s</td>
              <td>{f.max_v_mps !== undefined ? `${f.max_v_mps} m/s (${f.ratio}x)` : "n/a"}</td><td><Status s={f.status} /></td></tr>
          ))}</tbody>
        </table>
      )}
    </div>
  );
}

export function RocketCard({ view: v }: { view: RocketCallView }): JSX.Element {
  const [more, setMore] = useState(false);
  const st = v.running ? null : v.failed ? "fail" : v.status;
  return (
    <article class="scard rcard" data-testid="rocket-card" data-rocket-card={v.tool} data-status={v.running ? "running" : st ?? "none"} aria-label={v.title}>
      <header class="scard-head">
        <span class="scard-type">{v.title}</span>
        <span class="scard-status">
          {v.running ? <><Led tone="blue" title="running" /><span class="scard-verdict">running</span></> : <Status s={st} />}
        </span>
      </header>
      <p class="scard-fidelity"><span class="scard-guide">Guide only</span>
        <span class="scard-fidelity-note text">{v.tool === "rocket_analyze" ? "Barrowman CP · NACA TN-4197 flutter" : "3-DOF flight · OpenRocket-checked"}</span></p>
      {v.running && <p class="faint text">{v.tool === "rocket_flight" ? "Flying" : "Analyzing"}{v.motors.length ? ` on ${v.motors.join(", ")}` : ""}…</p>}
      {v.failed && <p class="scard-fail text" role="alert">{v.summary ?? "the analysis failed"}{v.hint ? <span class="faint"> · {v.hint}</span> : null}</p>}
      {!v.running && !v.failed && (
        <>
          <dl class="scard-metrics rcard-metrics">
            {v.metrics.map((m, i) => (
              <div key={`${m.label}${i}`} data-testid="rocket-metric" data-label={m.label} data-status={m.status ?? undefined}>
                <dt class="label">{m.label}</dt><dd>{m.status && <Led tone={tone(m.status)} />}{fmtMetric(m)}</dd>
              </div>
            ))}
          </dl>
          {v.charts.map((c) => (
            <div key={c.id} class="scard-chart" data-testid="rocket-chart" data-chart={c.id}>
              <div class="scard-chart-title faint">{c.title}</div>
              <StudyChart chart={c} compact height={84} />
            </div>
          ))}
          <FlightTable v={v} />
          {more && <StabilityTables v={v} />}
        </>
      )}
      {v.warnings.length > 0 && (
        <ul class="scard-warn" data-testid="rocket-warnings" aria-label="Warnings">
          {v.warnings.map((w, i) => <li key={i}><Led tone="amber" /><span class="text dim">{w}</span></li>)}
        </ul>
      )}
      {more && (v.notes.length > 0 || v.accuracy.length > 0) && (
        <ul class="rcard-notes faint" data-testid="rocket-accuracy">
          {[...v.notes, ...v.accuracy].map((n, i) => <li key={i} class="text">{n}</li>)}
        </ul>
      )}
      {!v.running && !v.failed && (
        <button type="button" class="scard-more faint" aria-expanded={more} onClick={() => setMore((x) => !x)}>
          {more ? "Hide details" : v.tool === "rocket_analyze" ? "Show the Barrowman terms, flutter and accuracy" : "Show the accuracy notes"}
        </button>
      )}
    </article>
  );
}
