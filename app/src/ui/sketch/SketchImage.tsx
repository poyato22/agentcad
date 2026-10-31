// One concept sketch image on its paper sheet, with the agent's callouts and (optionally) the
// user's numbered marks drawn over it as SVG. Callout and comment text is untrusted: SVG <text>
// children, never markup.
import type { ComponentChildren, JSX } from "preact";
import type { Callout, Mark, SketchImage as Img } from "../../api/concept";
import { useImageUrl } from "../console/cards/critique/images";
import { lassoPath, markAnchor } from "./marks";

export interface NumberedMark { n: number; mark: Mark; id?: string; selected?: boolean }

export function MarkShapes({ marks, w, h }: { marks: readonly NumberedMark[]; w: number; h: number }): JSX.Element {
  const badge = Math.max(10, w / 70);
  return (
    <g class="sk-marks">
      {marks.map(({ n, mark, id, selected }) => {
        const a = markAnchor(mark, w, h);
        const cls = `sk-mark${selected ? " is-selected" : ""}`;
        return (
          <g key={id ?? n} class={cls} data-mark={mark.kind} data-n={n}>
            {mark.kind === "circle" && <circle cx={mark.cx * w} cy={mark.cy * h} r={mark.r * w} class="sk-mark-line" />}
            {mark.kind === "lasso" && <path d={lassoPath(mark.points as [number, number][], w, h)} class="sk-mark-line" />}
            {mark.kind === "pin" && <circle cx={mark.x * w} cy={mark.y * h} r={badge * 0.35} class="sk-mark-dot" />}
            <circle cx={a.x * w} cy={a.y * h} r={badge} class="sk-mark-badge" />
            <text x={a.x * w} y={a.y * h} class="sk-mark-num" font-size={badge * 1.15} text-anchor="middle" dominant-baseline="central">{n}</text>
          </g>
        );
      })}
    </g>
  );
}

function Callouts({ callouts, w, h }: { callouts: readonly Callout[]; w: number; h: number }): JSX.Element {
  const fs = w / 48;
  return (
    <g class="sk-callouts" aria-hidden="true">
      {callouts.map((c, i) => (
        <g key={i}>
          {c.to && <line x1={c.x * w} y1={c.y * h} x2={c.to[0] * w} y2={c.to[1] * h} class="sk-callout-line" />}
          {c.to && <circle cx={c.to[0] * w} cy={c.to[1] * h} r={w / 300} class="sk-callout-dot" />}
          <text x={c.x * w} y={c.y * h} font-size={fs} class="sk-callout-text">{c.text}</text>
        </g>
      ))}
    </g>
  );
}

export interface SketchImageProps {
  image: Img;
  callouts?: readonly Callout[];
  marks?: readonly NumberedMark[];
  /** Extra SVG drawn on top (e.g. the stroke being drawn). */
  children?: ComponentChildren;
  alt: string;
  class?: string;
  svgRef?: (el: SVGSVGElement | null) => void;
  onPointerDown?: JSX.PointerEventHandler<SVGSVGElement>;
  onPointerMove?: JSX.PointerEventHandler<SVGSVGElement>;
  onPointerUp?: JSX.PointerEventHandler<SVGSVGElement>;
  cursor?: string;
}

export function SketchImage(p: SketchImageProps): JSX.Element {
  const url = useImageUrl({ id: p.image.render_id });
  const w = p.image.width || 1024;
  const h = p.image.height || 1024;
  const callouts = (p.callouts ?? []).filter((c) => c.view === p.image.view);
  return (
    <div class={`sk-sheet ${p.class ?? ""}`} style={{ aspectRatio: `${w} / ${h}` }} data-view={p.image.view}>
      {url ? <img src={url} alt={p.alt} draggable={false} /> : <div class="sk-sheet-wait" aria-label="Loading the sketch" />}
      <svg viewBox={`0 0 ${w} ${h}`} class="sk-overlay" ref={p.svgRef} style={p.cursor ? { cursor: p.cursor } : undefined}
        onPointerDown={p.onPointerDown} onPointerMove={p.onPointerMove} onPointerUp={p.onPointerUp} data-testid="sketch-overlay">
        {callouts.length > 0 && <Callouts callouts={callouts} w={w} h={h} />}
        {p.marks && p.marks.length > 0 && <MarkShapes marks={p.marks} w={w} h={h} />}
        {p.children}
      </svg>
    </div>
  );
}
