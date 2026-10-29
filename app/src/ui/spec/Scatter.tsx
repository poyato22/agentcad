// Mass vs safety factor of a design study's variants, with the Pareto front (core flags: feasible
// and non-dominated on mass ↓ / SF ↑) drawn as a 2px line through its points.
// Encoding (dataviz skill, with Mises' fixed tokens; no new hues): identity by shape and fill,
// not color — Pareto = filled ink dot on the front line, feasible = filled dim dot, infeasible =
// hollow ring; the previewed variant gets the blue "selected" ring. Every dot has a 2px surface
// ring, a 24px hit area, a hover/focus tooltip and a keyboard path (Tab to the chart, ←/→ walk
// the variants by mass, Enter previews). The iterations table below is the table view.
import type { JSX } from "preact";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "preact/hooks";
import type { Requirement, StudyIteration } from "../../api/spec";
import { fmtNum, niceTicks, paretoLine, plotPoints, type Point } from "./model";

const H = 220;
const M = { l: 40, r: 14, t: 12, b: 30 };
const HIT_R = 12;

export interface ScatterProps {
  iterations: readonly StudyIteration[];
  requirements: readonly Requirement[];
  bestId: number | null;
  selected: number | null;
  onSelect: (n: number) => void;
  /** Meets the spec as it is now (default: the study's stored flag). */
  meets?: (it: StudyIteration) => boolean;
  /** Width when the container can't be measured (tests). */
  width?: number;
}

/** SF and mass lines to draw: the strictest SF target and the mass budget. */
function thresholds(reqs: readonly Requirement[]): { sf: number | null; mass: number | null } {
  const sfs = reqs.filter((r) => r.kind === "min_safety_factor" && typeof r.target === "number").map((r) => r.target as number);
  const ms = reqs.filter((r) => r.kind === "max_mass_g" && typeof r.target === "number").map((r) => r.target as number);
  return { sf: sfs.length ? Math.max(...sfs) : null, mass: ms.length ? Math.min(...ms) : null };
}

export function pointLabel(p: Point, best: number | null, meets: boolean = p.it.feasible): string {
  const tags = [meets ? "meets the spec" : "fails the spec"];
  if (p.it.pareto) tags.push("on the Pareto front");
  if (p.it.n === best) tags.push("best");
  const vars = Object.entries(p.it.vars).map(([k, v]) => `${k} ${fmtNum(v)}`).join(", ");
  return `Variant #${p.it.n}: ${fmtNum(p.mass)} g, SF ${p.sf.toFixed(2)}; ${vars}; ${tags.join(", ")}`;
}

export function Scatter(props: ScatterProps): JSX.Element {
  const wrap = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(props.width ?? 360);
  const [hover, setHover] = useState<number | null>(null);
  const [focusN, setFocusN] = useState<number | null>(null);

  useLayoutEffect(() => {
    const el = wrap.current;
    if (!el || props.width !== undefined || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => { if (el.clientWidth > 0) setW(el.clientWidth); });
    ro.observe(el);
    if (el.clientWidth > 0) setW(el.clientWidth);
    return () => ro.disconnect();
  }, [props.width]);

  const pts = useMemo(() => plotPoints(props.iterations), [props.iterations]);
  const byMass = useMemo(() => [...pts].sort((a, b) => a.mass - b.mass || a.it.n - b.it.n), [pts]);
  const meets = props.meets ?? ((it: StudyIteration) => it.feasible);
  // the front is the core's flagged points that still meet the spec
  const front = paretoLine(pts.filter((p) => meets(p.it)));
  const lim = thresholds(props.requirements);
  const missing = props.iterations.length - pts.length;

  // domains: mass padded a little, SF from 0 (SF 1 = yield) up past the target
  const masses = pts.map((p) => p.mass);
  const sfs = pts.map((p) => p.sf);
  let x0 = Math.min(...masses, Infinity);
  let x1 = Math.max(...masses, -Infinity);
  if (lim.mass !== null && Number.isFinite(x0) && lim.mass < x1 * 1.6 && lim.mass > x0 * 0.6) { x0 = Math.min(x0, lim.mass); x1 = Math.max(x1, lim.mass); }
  const padX = (x1 - x0) * 0.08 || Math.max(1, x0 * 0.1);
  x0 -= padX;
  x1 += padX;
  const y1 = Math.max(...sfs, lim.sf ?? 0, 1) * 1.1;
  const xt = niceTicks(x0, x1, Math.max(3, Math.floor(w / 80)));
  const yt = niceTicks(0, y1, 4);
  const iw = Math.max(40, w - M.l - M.r);
  const ih = H - M.t - M.b;
  const sx = (v: number) => M.l + ((v - x0) / (x1 - x0)) * iw;
  const sy = (v: number) => M.t + ih - (v / y1) * ih;

  const current = focusN ?? props.selected ?? props.bestId ?? byMass[0]?.it.n ?? null;
  const tabTarget = byMass.some((p) => p.it.n === current) ? current : byMass[0]?.it.n ?? null;
  const tip = hover ?? focusN;
  const tipPt = tip !== null ? pts.find((p) => p.it.n === tip) ?? null : null;

  useEffect(() => { if (focusN !== null && !pts.some((p) => p.it.n === focusN)) setFocusN(null); }, [pts, focusN]);

  const onKey = (e: KeyboardEvent) => {
    const i = byMass.findIndex((p) => p.it.n === current);
    let next: number;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = Math.min(byMass.length - 1, i + 1);
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = Math.max(0, i - 1);
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = byMass.length - 1;
    else if ((e.key === "Enter" || e.key === " ") && current !== null) { e.preventDefault(); props.onSelect(current); return; }
    else return;
    e.preventDefault();
    const n = byMass[next]?.it.n ?? null;
    setFocusN(n);
    if (n !== null) wrap.current?.querySelector<SVGGElement>(`[data-point="${n}"]`)?.focus();
  };

  if (pts.length === 0) {
    return (
      <div class="sps-empty faint" data-scatter>
        {props.iterations.length === 0 ? "No variants evaluated yet." : "No safety factors to plot (no stress solver: this study ranked variants on mass and geometry only)."}
      </div>
    );
  }

  const ink = "var(--ink)";
  return (
    <div class="sps" ref={wrap} data-scatter>
      <ul class="sps-legend" aria-label="Legend">
        <li><svg width="22" height="10" aria-hidden="true"><line x1="1" y1="5" x2="21" y2="5" stroke={ink} stroke-width="2" stroke-linecap="round" /><circle cx="11" cy="5" r="4" fill={ink} /></svg>Pareto front</li>
        <li><svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="4" fill="var(--dim)" /></svg>meets spec</li>
        <li><svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="3.25" fill="none" stroke="var(--dim)" stroke-width="1.5" /></svg>fails spec</li>
        <li><svg width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="6" fill="none" stroke="var(--blue)" stroke-width="2" /></svg>previewed</li>
      </ul>
      <svg class="sps-plot" width={w} height={H} role="group" aria-label={`Mass versus safety factor of ${pts.length} variants; ${front.length} on the Pareto front`} onKeyDown={onKey}>
        {yt.map((v) => (
          <g key={`y${v}`}>
            <line x1={M.l} x2={M.l + iw} y1={sy(v)} y2={sy(v)} stroke="var(--line)" stroke-width="1" />
            <text x={M.l - 6} y={sy(v)} class="sps-tick" text-anchor="end" dominant-baseline="middle">{fmtNum(v)}</text>
          </g>
        ))}
        {xt.map((v) => <text key={`x${v}`} x={sx(v)} y={M.t + ih + 14} class="sps-tick" text-anchor="middle">{fmtNum(v)}</text>)}
        <line x1={M.l} x2={M.l + iw} y1={M.t + ih} y2={M.t + ih} stroke="var(--line2)" stroke-width="1" />
        <text x={M.l + iw} y={H - 2} class="sps-axis" text-anchor="end">mass (g) →</text>
        <text x={4} y={M.t - 2} class="sps-axis">↑ min SF</text>
        {lim.sf !== null && lim.sf <= y1 && (
          <g data-threshold="sf">
            <line x1={M.l} x2={M.l + iw} y1={sy(lim.sf)} y2={sy(lim.sf)} stroke="var(--dim)" stroke-width="1" />
            <text x={M.l + iw - 2} y={sy(lim.sf) - 4} class="sps-thr" text-anchor="end">{`SF ≥ ${lim.sf.toFixed(2)}`}</text>
          </g>
        )}
        {lim.mass !== null && lim.mass >= x0 && lim.mass <= x1 && (
          <g data-threshold="mass">
            <line x1={sx(lim.mass)} x2={sx(lim.mass)} y1={M.t} y2={M.t + ih} stroke="var(--dim)" stroke-width="1" />
            <text x={sx(lim.mass) + 4} y={M.t + 8} class="sps-thr">{`≤ ${fmtNum(lim.mass)} g`}</text>
          </g>
        )}
        {front.length > 1 && (
          <polyline data-front points={front.map((p) => `${sx(p.mass)},${sy(p.sf)}`).join(" ")}
            fill="none" stroke={ink} stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
        )}
        {byMass.map((p) => {
          const n = p.it.n;
          const cx = sx(p.mass);
          const cy = sy(p.sf);
          const ok = meets(p.it);
          const kind = !ok ? "infeasible" : p.it.pareto ? "pareto" : "feasible";
          const sel = props.selected === n;
          return (
            <g key={n} class="sps-pt" data-point={n} data-kind={kind} data-selected={sel ? "" : undefined}
              tabindex={n === tabTarget ? 0 : -1} role="button" aria-pressed={sel} aria-label={pointLabel(p, props.bestId, ok)}
              onMouseEnter={() => setHover(n)} onMouseLeave={() => setHover((h) => (h === n ? null : h))}
              onFocus={() => setFocusN(n)} onBlur={() => setFocusN((f) => (f === n ? null : f))}
              onClick={() => props.onSelect(n)}>
              <circle cx={cx} cy={cy} r={HIT_R} fill="transparent" />
              {sel && <circle cx={cx} cy={cy} r={8} fill="none" stroke="var(--blue)" stroke-width="2" />}
              {kind === "infeasible"
                ? <circle cx={cx} cy={cy} r={4} fill="var(--paper)" stroke="var(--dim)" stroke-width="1.5" />
                : <circle cx={cx} cy={cy} r={kind === "pareto" ? 4.5 : 4} fill={kind === "pareto" ? ink : "var(--dim)"} stroke="var(--paper)" stroke-width="2" paint-order="stroke" />}
              {n === props.bestId && <text x={cx + 9} y={cy + 15} class="sps-best">{`best #${n}`}</text>}
            </g>
          );
        })}
      </svg>
      {tipPt && (
        <div class="sps-tip" role="tooltip" data-tip={tipPt.it.n}
          style={{ left: `${Math.min(Math.max(sx(tipPt.mass) + 12, 0), Math.max(0, w - 190))}px`, top: `${Math.max(0, sy(tipPt.sf) + 22)}px` }}>
          <div class="sps-tip-h">{`#${tipPt.it.n} ${tipPt.it.pareto ? "· Pareto" : ""}${tipPt.it.n === props.bestId ? " · best" : ""}`}</div>
          <div>{`${fmtNum(tipPt.mass)} g · SF ${tipPt.sf.toFixed(2)}`}{tipPt.it.metrics.max_disp_mm !== null ? ` · ${fmtNum(tipPt.it.metrics.max_disp_mm)} mm` : ""}</div>
          <div class="dim">{Object.entries(tipPt.it.vars).map(([k, v]) => `${k} ${fmtNum(v)}`).join(" · ")}</div>
          <div class={meets(tipPt.it) ? "tone-green" : "tone-red"}>{meets(tipPt.it) ? "meets the spec" : "fails the spec"}</div>
        </div>
      )}
      {missing > 0 && <p class="sps-note faint">{`${missing} variant${missing === 1 ? "" : "s"} not plotted (build or solve failed, or no SF).`}</p>}
    </div>
  );
}
