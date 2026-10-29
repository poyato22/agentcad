// One study chart as SVG: line (convergence, Cd history, Cd/Cl vs yaw), bar (modal frequencies
// against the excitation band, buckling factors) or histogram (stress / temperature with the
// allowable or HDT line). Follows the dataviz rules within the app's tokens: 2px lines in ink
// (second series dim), r4 markers with a surface ring, bars ≤ 24px with a 4px rounded data end and
// a 2px gap, hairline recessive grid, a legend for two series, a crosshair (lines) or per-bar
// tooltip on hover and keyboard focus, a table view, and no color without a label. Status colors
// only where the value means warn/fail (a mode inside the excitation band, a factor below 1).
// Double-click (or Enter) opens the study's immersive view when `onOpen` is given.
import type { JSX } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import type { StudyChart as Chart } from "../../../../api/studies";
import { unitText } from "../model";
import { chartSummary, nearestIndex, niceTicks, prepare, tickText } from "./chartModel";
import "./charts.css";

export interface StudyChartProps {
  chart: Chart;
  /** Fixed size; without a width the chart fills its container's width. */
  width?: number;
  height?: number;
  /** Card size: no axis titles, fewer ticks, no table toggle. */
  compact?: boolean;
  onOpen?: () => void;
}

const SERIES = ["var(--ink)", "var(--dim)"];

function useWidth(fixed: number | undefined): [preact.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(fixed ?? 360);
  useEffect(() => {
    if (fixed !== undefined) return;
    const el = ref.current;
    if (!el) return;
    const measure = () => { const r = el.getBoundingClientRect().width; if (r > 0) setW(Math.round(r)); };
    measure();
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    ro?.observe(el);
    return () => ro?.disconnect();
  }, [fixed]);
  return [ref, fixed ?? w];
}

const axisText = (a: { label: string; unit: string }) => (a.unit ? `${a.label} (${unitText(a.unit)})` : a.label);

export function StudyChart({ chart, width, height, compact = false, onOpen }: StudyChartProps): JSX.Element {
  const [host, W] = useWidth(width);
  const H = height ?? (compact ? 96 : 200);
  const p = useMemo(() => prepare(chart), [chart]);
  const [active, setActive] = useState<number | null>(null);
  const [table, setTable] = useState(false);

  const m = compact ? { l: 30, r: 8, t: 6, b: 16 } : { l: 44, r: 12, t: 10, b: 34 };
  const iw = Math.max(10, W - m.l - m.r);
  const ih = Math.max(10, H - m.t - m.b);
  const yTicks = niceTicks(p.y.lo, p.y.hi, compact ? 2 : 4);
  const yLo = Math.min(p.y.lo, yTicks[0] ?? p.y.lo);
  const yHi = Math.max(p.y.hi, yTicks[yTicks.length - 1] ?? p.y.hi);
  const xLo = p.x.lo; const xHi = p.x.hi === p.x.lo ? p.x.lo + 1 : p.x.hi;
  const sx = (x: number) => m.l + ((x - xLo) / (xHi - xLo)) * iw;
  const sy = (y: number) => m.t + ih - ((y - yLo) / (yHi - yLo || 1)) * ih;
  const xTicks = p.kind === "bar" ? p.bars.map((b) => (b.x0 + b.x1) / 2) : niceTicks(xLo, xHi, compact ? 3 : 5).filter((t) => t >= xLo - 1e-9 && t <= xHi + 1e-9);

  // the hover / focus positions: one per x of the first line, or one per bar
  const xs = p.bars.length ? p.bars.map((b) => (b.x0 + b.x1) / 2) : (p.lines[0]?.points.map((q) => q.x) ?? []);
  const n = xs.length;
  const onMove = (e: PointerEvent) => {
    if (!n) return;
    const svg = e.currentTarget as SVGSVGElement;
    const r = svg.getBoundingClientRect();
    const x = xLo + ((e.clientX - r.left - m.l) / iw) * (xHi - xLo);
    setActive(nearestIndex(xs, x));
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Enter" && onOpen) { e.preventDefault(); onOpen(); return; }
    if (!n) return;
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      const cur = active ?? (e.key === "ArrowRight" ? -1 : n);
      setActive(Math.min(n - 1, Math.max(0, cur + (e.key === "ArrowRight" ? 1 : -1))));
    }
  };

  const tip = active === null ? null : tooltip(chart, p, active);
  const tipX = active === null ? 0 : sx(xs[active] ?? xLo);
  const yUnit = unitText(chart.y.unit);

  return (
    <figure class={`sc${compact ? " sc-compact" : ""}`} data-chart={chart.id} data-kind={chart.kind} ref={host}>
      {!compact && (
        <figcaption class="sc-head">
          <span class="sc-title">{chart.title}</span>
          {p.lines.length > 1 && (
            <span class="sc-legend" data-testid="chart-legend">
              {p.lines.map((l, i) => <span key={l.name} class="sc-key"><i class="sc-key-line" style={{ background: SERIES[i] ?? SERIES[1] }} />{l.name}</span>)}
            </span>
          )}
          <button type="button" class="sc-table-btn" aria-pressed={table} onClick={() => setTable(!table)}>{table ? "Chart" : "Table"}</button>
        </figcaption>
      )}
      {table && !compact ? <ChartTable chart={chart} /> : (
        <div class="sc-plot">
          <svg width={W} height={H} role="img" tabIndex={0} aria-label={`${chartSummary(chart)}${onOpen ? " Press Enter for the immersive view." : ""}`}
            onPointerMove={onMove} onPointerLeave={() => setActive(null)} onBlur={() => setActive(null)} onKeyDown={onKey}
            onDblClick={onOpen ? (e) => { e.preventDefault(); onOpen(); } : undefined} data-testid="study-chart">
            {/* bands under everything */}
            {p.bands.map((b) => {
              const y0 = sy(Math.min(yHi, b.to)); const y1 = sy(Math.max(yLo, b.from));
              const tone = /buckle/i.test(b.label) ? "sc-band-fail" : "sc-band-warn";
              return b.axis === "y"
                ? <g key={b.label}><rect class={`sc-band ${tone}`} x={m.l} y={y0} width={iw} height={Math.max(0, y1 - y0)} />{!compact && <text class="sc-band-label" x={m.l + iw - 4} y={y0 + 11} text-anchor="end">{b.label}</text>}</g>
                : <rect key={b.label} class={`sc-band ${tone}`} x={sx(b.from)} y={m.t} width={Math.max(0, sx(b.to) - sx(b.from))} height={ih} />;
            })}
            {/* recessive grid + y ticks */}
            {yTicks.map((t) => (
              <g key={`y${t}`}>
                <line class="sc-grid" x1={m.l} x2={m.l + iw} y1={sy(t)} y2={sy(t)} />
                <text class="sc-tick" x={m.l - 4} y={sy(t) + 3} text-anchor="end">{tickText(t)}</text>
              </g>
            ))}
            {xTicks.map((t) => <text key={`x${t}`} class="sc-tick" x={sx(t)} y={m.t + ih + (compact ? 12 : 13)} text-anchor="middle">{tickText(t)}</text>)}
            {!compact && <text class="sc-axis" x={m.l + iw / 2} y={H - 4} text-anchor="middle">{axisText(chart.x)}</text>}
            {!compact && <text class="sc-axis" x={10} y={m.t + ih / 2} text-anchor="middle" transform={`rotate(-90 10 ${m.t + ih / 2})`}>{axisText(chart.y)}</text>}
            {/* bars: ≤ 24px, rounded data end, square at the baseline, 2px gap */}
            {p.bars.map((b) => {
              const slot = Math.max(1, sx(b.x1) - sx(b.x0));
              const w = Math.max(1, Math.min(chart.kind === "histogram" ? slot - 2 : 24, slot - 2));
              const cx = (sx(b.x0) + sx(b.x1)) / 2;
              const top = sy(b.y); const base = sy(Math.max(0, yLo));
              const r = Math.min(4, w / 2, Math.max(0, base - top));
              const x0 = cx - w / 2;
              const d = `M${x0},${base}V${top + r}Q${x0},${top} ${x0 + r},${top}H${x0 + w - r}Q${x0 + w},${top} ${x0 + w},${top + r}V${base}Z`;
              const cls = b.flag ? "sc-bar sc-bar-flag" : chart.kind === "histogram" ? "sc-bar sc-bar-hist" : "sc-bar";
              return <path key={b.index} class={cls} d={d} data-active={active === b.index ? "" : undefined} data-flag={b.flag ? "" : undefined} />;
            })}
            {p.markers.map((mk) => (
              <g key={mk.label}>
                <line class="sc-marker" x1={sx(mk.x)} x2={sx(mk.x)} y1={m.t} y2={m.t + ih} />
                {!compact && <text class="sc-marker-label" x={sx(mk.x) - 3} y={m.t + 10} text-anchor="end">{mk.label}</text>}
              </g>
            ))}
            {/* lines: 2px, round joins; the end dot carries the series */}
            {p.lines.map((l, i) => (
              <g key={l.name} data-series={l.name}>
                <polyline class="sc-line" points={l.points.map((q) => `${sx(q.x)},${sy(q.y)}`).join(" ")} style={{ stroke: SERIES[i] ?? SERIES[1] }} />
                {(l.points.length <= 24 ? l.points : l.points.slice(-1)).map((q) => (
                  <circle key={q.index} class="sc-dot" cx={sx(q.x)} cy={sy(q.y)} r={4} style={{ fill: SERIES[i] ?? SERIES[1] }} />
                ))}
              </g>
            ))}
            {active !== null && p.lines.length > 0 && <line class="sc-cross" x1={tipX} x2={tipX} y1={m.t} y2={m.t + ih} />}
          </svg>
          {tip && (
            <div class="sc-tip" role="status" style={{ left: `${Math.min(Math.max(0, tipX + 8), Math.max(0, W - 150))}px` }}>
              <div class="sc-tip-x">{tip.x}</div>
              {tip.rows.map((r) => (
                <div key={r.name} class="sc-tip-row">
                  <i class="sc-key-line" style={{ background: r.color }} />
                  <b>{r.value}{yUnit && r.value !== "n/a" ? ` ${yUnit}` : ""}</b>
                  <span class="faint">{r.name}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </figure>
  );
}

function tooltip(c: Chart, p: ReturnType<typeof prepare>, i: number): { x: string; rows: { name: string; value: string; color: string }[] } | null {
  if (p.bars.length) {
    const b = p.bars[i];
    if (!b) return null;
    const name = c.kind === "histogram" ? "count" : (c.series?.[0]?.name ?? c.y.label);
    return { x: b.label, rows: [{ name: b.flag ? `${name} · inside the band` : name, value: tickText(b.y), color: b.flag ? "var(--amber)" : "var(--dim)" }] };
  }
  const x = p.lines[0]?.points[i]?.x;
  if (x === undefined) return null;
  return {
    x: `${c.x.label} ${tickText(x)}${c.x.unit ? ` ${unitText(c.x.unit)}` : ""}`,
    rows: p.lines.map((l, k) => {
      const q = l.points.find((pt) => pt.x === x) ?? l.points[nearestIndex(l.points.map((pt) => pt.x), x)];
      return { name: l.name, value: q ? tickText(q.y) : "n/a", color: SERIES[k] ?? SERIES[1] };
    }),
  };
}

/** The same data as rows (every value reachable without hovering). */
export function ChartTable({ chart }: { chart: Chart }): JSX.Element {
  const p = prepare(chart);
  if (p.bars.length) {
    return (
      <table class="sc-table" data-testid="chart-table">
        <thead><tr><th>{chart.kind === "histogram" ? axisText(chart.x) : axisText(chart.x)}</th><th>{chart.kind === "histogram" ? "count" : axisText(chart.y)}</th></tr></thead>
        <tbody>{p.bars.map((b) => <tr key={b.index}><td>{b.label}</td><td>{tickText(b.y)}{b.flag ? " · inside the band" : ""}</td></tr>)}</tbody>
      </table>
    );
  }
  const xs = [...new Set(p.lines.flatMap((l) => l.points.map((q) => q.x)))].sort((a, b) => a - b);
  return (
    <table class="sc-table" data-testid="chart-table">
      <thead><tr><th>{axisText(chart.x)}</th>{p.lines.map((l) => <th key={l.name}>{l.name}</th>)}</tr></thead>
      <tbody>
        {xs.slice(0, 200).map((x) => (
          <tr key={x}><td>{tickText(x)}</td>{p.lines.map((l) => { const q = l.points.find((pt) => pt.x === x); return <td key={l.name}>{q ? tickText(q.y) : ""}</td>; })}</tr>
        ))}
      </tbody>
    </table>
  );
}
