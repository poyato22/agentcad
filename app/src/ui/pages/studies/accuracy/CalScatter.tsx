// One calibration quantity per test as a dot plot: each test's own reading (a dot), the fitted
// value (a solid line) with its scatter band (±CV, recessive fill), and the datasheet value it
// replaces (a dashed reference line). One series, so no legend: the title names it, and the two
// reference lines are labelled in place. Hover or arrow keys show a test's value and its distance
// from the fit; the same numbers are in the tests table next to it.
import type { JSX } from "preact";
import { useState } from "preact/hooks";
import { niceTicks, tickText } from "../charts/chartModel";
import { sig3 } from "../model";

export interface CalPoint { index: number; label: string; value: number }

export function CalScatter({ title, unit, points, fit, scatterPct, datasheet, width = 270, height = 150 }: {
  title: string; unit: string; points: readonly CalPoint[]; fit: number | null; scatterPct: number | null; datasheet: number | null;
  width?: number; height?: number;
}): JSX.Element | null {
  const [active, setActive] = useState<number | null>(null);
  if (!points.length) return null;
  const band = fit !== null && scatterPct !== null ? [fit * (1 - scatterPct / 100), fit * (1 + scatterPct / 100)] as const : null;
  const ys = [...points.map((p) => p.value), ...(fit !== null ? [fit] : []), ...(datasheet !== null ? [datasheet] : []), ...(band ?? [])];
  let lo = Math.min(...ys); let hi = Math.max(...ys);
  const pad = (hi - lo) * 0.12 || Math.abs(hi) * 0.1 || 1;
  lo -= pad; hi += pad;
  const ticks = niceTicks(lo, hi, 3);
  lo = Math.min(lo, ticks[0] ?? lo); hi = Math.max(hi, ticks[ticks.length - 1] ?? hi);
  const m = { l: 44, r: 64, t: 8, b: 22 };
  const iw = width - m.l - m.r; const ih = height - m.t - m.b;
  const n = points.length;
  const sx = (i: number) => m.l + ((i + 0.5) / n) * iw;
  const sy = (v: number) => m.t + ih - ((v - lo) / (hi - lo)) * ih;
  const resid = (v: number) => (fit ? ((v - fit) / fit) * 100 : null);
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const cur = active ?? (e.key === "ArrowRight" ? -1 : n);
    setActive(Math.min(n - 1, Math.max(0, cur + (e.key === "ArrowRight" ? 1 : -1))));
  };
  const a = active === null ? null : points[active];
  const summary = `${title}: ${n} test${n === 1 ? "" : "s"}${fit !== null ? `, fitted ${sig3(fit)} ${unit}` : ""}${scatterPct !== null ? `, scatter ±${sig3(scatterPct)}%` : ""}${datasheet !== null ? `, datasheet ${sig3(datasheet)} ${unit}` : ""}.`;
  return (
    <figure class="sc acc-cal-chart" data-testid="cal-scatter" data-quantity={title}>
      <figcaption class="sc-head"><span class="sc-title">{title} <span class="faint">({unit}) per test</span></span></figcaption>
      <div class="sc-plot">
        <svg width={width} height={height} role="img" tabIndex={0} aria-label={summary} onKeyDown={onKey}
          onPointerLeave={() => setActive(null)} onBlur={() => setActive(null)}
          onPointerMove={(e) => {
            const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
            const i = Math.floor(((e.clientX - r.left - m.l) / iw) * n);
            setActive(i >= 0 && i < n ? i : null);
          }}>
          {band && <rect class="acc-band" x={m.l} width={iw} y={sy(band[1])} height={Math.max(0, sy(band[0]) - sy(band[1]))} data-testid="cal-band" />}
          {ticks.map((t) => (
            <g key={t}>
              <line class="sc-grid" x1={m.l} x2={m.l + iw} y1={sy(t)} y2={sy(t)} />
              <text class="sc-tick" x={m.l - 4} y={sy(t) + 3} text-anchor="end">{tickText(t)}</text>
            </g>
          ))}
          {points.map((p, i) => <text key={p.index} class="sc-tick" x={sx(i)} y={m.t + ih + 13} text-anchor="middle">{p.index + 1}</text>)}
          {datasheet !== null && (
            <g data-testid="cal-datasheet">
              <line class="acc-ref" x1={m.l} x2={m.l + iw} y1={sy(datasheet)} y2={sy(datasheet)} />
              <text class="sc-marker-label" x={m.l + iw + 4} y={sy(datasheet) + 3}>datasheet</text>
            </g>
          )}
          {fit !== null && (
            <g data-testid="cal-fit">
              <line class="acc-fit" x1={m.l} x2={m.l + iw} y1={sy(fit)} y2={sy(fit)} />
              <text class="acc-fit-label" x={m.l + iw + 4} y={sy(fit) + 3}>fit {sig3(fit)}</text>
            </g>
          )}
          {points.map((p, i) => (
            <circle key={p.index} class="sc-dot acc-cal-dot" cx={sx(i)} cy={sy(p.value)} r={active === i ? 5.5 : 4} data-active={active === i ? "" : undefined} />
          ))}
        </svg>
        {a && (
          <div class="sc-tip" role="status" style={{ left: `${Math.min(Math.max(0, sx(active ?? 0) + 8), width - 160)}px` }}>
            <div class="sc-tip-x">test {a.index + 1} · {a.label}</div>
            <div class="sc-tip-row"><b>{sig3(a.value)} {unit}</b>
              {resid(a.value) !== null && <span class="faint">{`${(resid(a.value) ?? 0) >= 0 ? "+" : ""}${sig3(resid(a.value))}% from the fit`}</span>}</div>
          </div>
        )}
      </div>
    </figure>
  );
}
