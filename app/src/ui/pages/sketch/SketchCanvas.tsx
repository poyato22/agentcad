// The drawing area: an SVG whose viewBox is the sheet in mm. Pointer events cover pen, mouse and
// trackpad alike (pen strokes use coalesced events for full fidelity). Geometry lives in model.ts;
// this component only turns pointer positions into mm and previews the stroke being drawn.
import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import {
  circleStroke, clientToMm, closeIfNear, endpoints, freehandStroke, hitStroke, labelAnchor, lineStroke, pxToMm, rectStroke, SHEET,
  snapPoint, toSvg, type Pt, type SnapContext, type SnapKind, type Stroke,
} from "./model";
import { strokeCaption, strokePoints } from "./render";
import { doc, gridStep, selectedId, sketchActions, snapEnds, snapGrid, tool, type Tool } from "./state";

const SNAP_PX = 8;
const CLOSE_PX = 10;
const HIT_PX = 6;
const MIN_MM = 0.5;

interface Drawing { pointerId: number; tool: Exclude<Tool, "select">; start: Pt; raw: Pt[]; current: Pt }

function round1(v: number): string {
  return (Math.round(v * 10) / 10).toFixed(1);
}

export function SketchCanvas(): JSX.Element {
  const svg = useRef<SVGSVGElement>(null);
  const drawing = useRef<Drawing | null>(null);
  const [draft, setDraft] = useState<Stroke | null>(null);
  const [marker, setMarker] = useState<{ point: Pt; kind: SnapKind } | null>(null);

  useEffect(() => {
    // Scoped to the sketch region (A5), not the whole document: Escape only cancels an
    // in-progress draw when the keystroke comes from inside the sketch.
    const region = svg.current?.closest("[data-sketch-root]") ?? svg.current;
    if (!region) return;
    const onKey = (e: Event) => {
      if ((e as KeyboardEvent).key === "Escape" && drawing.current) {
        drawing.current = null;
        setDraft(null);
        setMarker(null);
      }
    };
    region.addEventListener("keydown", onKey);
    return () => region.removeEventListener("keydown", onKey);
  }, []);

  function rect(): DOMRect {
    return (svg.current as SVGSVGElement).getBoundingClientRect();
  }

  function ctx(from?: Pt): SnapContext {
    const r = rect();
    return {
      grid: snapGrid.value ? gridStep.value : null,
      endpoints: snapEnds.value ? endpoints(doc.value.strokes) : [],
      radius: pxToMm(SNAP_PX, r),
      from, angles: snapEnds.value,
    };
  }

  function at(e: PointerEvent): Pt {
    return clientToMm(e.clientX, e.clientY, rect());
  }

  /** Where the pointer lands for the current tool (snapped), and why. */
  function snapped(p: Pt, d: Drawing | null): { point: Pt; kind: SnapKind } {
    const t = d?.tool ?? tool.value;
    if (t === "pen") return snapPoint(p, { ...ctx(), grid: null });         // freehand ends snap to ends only
    if (t === "line" && d) return snapPoint(p, ctx(d.start));
    return snapPoint(p, ctx());
  }

  function shape(d: Drawing, end: Pt): Stroke | null {
    const id = "draft";
    const [sx, sy] = d.start;
    switch (d.tool) {
      case "line": return Math.hypot(end[0] - sx, end[1] - sy) >= MIN_MM ? lineStroke(id, d.start, end) : null;
      case "rect": return Math.abs(end[0] - sx) >= MIN_MM && Math.abs(end[1] - sy) >= MIN_MM ? rectStroke(id, d.start, end) : null;
      case "circle": {
        let r = Math.hypot(end[0] - sx, end[1] - sy);
        if (snapGrid.value) r = Math.round(r / gridStep.value) * gridStep.value;
        return r >= MIN_MM ? circleStroke(id, d.start, r) : null;
      }
      case "pen": return d.raw.length >= 2 ? freehandStroke(id, d.raw, 0.05) : null;
    }
  }

  function onPointerDown(e: PointerEvent): void {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    if (drawing.current) return;
    e.preventDefault();
    const p = at(e);
    if (tool.value === "select") {
      const hit = hitStroke(doc.value.strokes, p, pxToMm(HIT_PX, rect()));
      sketchActions.select(hit?.id ?? null);
      return;
    }
    try { (e.currentTarget as Element).setPointerCapture(e.pointerId); } catch { /* synthetic pointer */ }
    const s = snapped(p, null);
    const d: Drawing = { pointerId: e.pointerId, tool: tool.value, start: s.point, raw: [s.point], current: s.point };
    drawing.current = d;
    setMarker(s.kind ? s : null);
    setDraft(shape(d, s.point));
  }

  function onPointerMove(e: PointerEvent): void {
    const d = drawing.current;
    if (!d) {
      if (tool.value !== "select" && e.pointerType !== "touch") {
        const s = snapped(at(e), null);
        setMarker(s.kind ? s : null);
      }
      return;
    }
    if (e.pointerId !== d.pointerId) return;
    if (d.tool === "pen") {
      const events = typeof e.getCoalescedEvents === "function" ? e.getCoalescedEvents() : [];
      for (const ev of events.length ? events : [e]) {
        const p = at(ev);
        const last = d.raw[d.raw.length - 1];
        if (Math.hypot(p[0] - last[0], p[1] - last[1]) >= 0.2) d.raw.push(p);
      }
      d.current = d.raw[d.raw.length - 1];
      setMarker(null);
    } else {
      const s = snapped(at(e), d);
      d.current = s.point;
      setMarker(s.kind ? s : null);
    }
    setDraft(shape(d, d.current));
  }

  function finish(e: PointerEvent, keep: boolean): void {
    const d = drawing.current;
    if (!d || e.pointerId !== d.pointerId) return;
    drawing.current = null;
    setDraft(null);
    setMarker(null);
    if (!keep) return;
    if (d.tool === "pen") {
      const end = snapPoint(at(e), { ...ctx(), grid: null });
      if (end.kind === "endpoint") d.raw.push(end.point);
      else if (d.raw.length < 2) d.raw.push(at(e));
    }
    const end = d.tool === "pen" ? d.raw[d.raw.length - 1] : d.current;
    let s = shape(d, end);
    if (!s) return;
    if (s.kind === "freehand") s = closeIfNear(freehandStroke("draft", d.raw), pxToMm(CLOSE_PX, rect()));
    if (s.kind === "freehand" && s.points.length < 2) return;
    sketchActions.addStroke({ ...s, id: sketchActions.newStrokeId() });
  }

  const W = SHEET.width_mm;
  const H = SHEET.height_mm;
  const step = Math.max(gridStep.value, 5);
  const minor: JSX.Element[] = [];
  const major: JSX.Element[] = [];
  for (let x = step; x < W; x += step) (x % 10 === 0 ? major : minor).push(<line key={`x${x}`} x1={x} y1={0} x2={x} y2={H} />);
  for (let y = step; y < H; y += step) (y % 10 === 0 ? major : minor).push(<line key={`y${y}`} x1={0} y1={H - y} x2={W} y2={H - y} />);
  const d = doc.value;
  const sel = selectedId.value;

  const readout = draft && drawing.current ? draftReadout(draft) : null;
  const readoutAt = drawing.current ? toSvg(drawing.current.current) : null;

  return (
    <svg ref={svg} class={`sk-svg tool-${tool.value}`} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMin meet"
      role="img" aria-label="Sketch drawing area" data-sketch-canvas
      onPointerDown={onPointerDown} onPointerMove={onPointerMove}
      onPointerUp={(e) => finish(e, true)} onPointerCancel={(e) => finish(e, false)}
      onPointerLeave={() => { if (!drawing.current) setMarker(null); }}>
      <rect class="sk-sheet" x={0} y={0} width={W} height={H} />
      <g class="sk-grid-minor">{minor}</g>
      <g class="sk-grid-major">{major}</g>
      <g class="sk-strokes">
        {d.strokes.map((s) => {
          const cls = `sk-stroke${s.id === sel ? " selected" : ""}`;
          return s.closed
            ? <polygon key={s.id} class={cls} points={strokePoints(s)} data-stroke-id={s.id} data-kind={s.kind} />
            : <polyline key={s.id} class={cls} points={strokePoints(s)} data-stroke-id={s.id} data-kind={s.kind} />;
        })}
      </g>
      <g class="sk-labels">
        {d.strokes.map((s) => {
          const text = strokeCaption(s, d.dimensions);
          if (!text) return null;
          const [x, y] = toSvg(labelAnchor(s));
          return <text key={s.id} class={`sk-label${s.id === sel ? " selected" : ""}`} x={x} y={y - 2} data-label-for={s.id}>{text}</text>;
        })}
      </g>
      {draft && (draft.closed
        ? <polygon class="sk-draft" points={strokePoints(draft)} />
        : <polyline class="sk-draft" points={strokePoints(draft)} />)}
      {readout && readoutAt && <text class="sk-readout" x={readoutAt[0] + 3} y={readoutAt[1] - 3} data-readout>{readout}</text>}
      {marker && marker.kind === "endpoint" && (() => {
        const [x, y] = toSvg(marker.point);
        return <rect class="sk-snap" x={x - 1.2} y={y - 1.2} width={2.4} height={2.4} data-snap={marker.kind} />;
      })()}
    </svg>
  );
}

function draftReadout(s: Stroke): string | null {
  if (s.kind === "line") {
    const [a, b] = s.points;
    return `${round1(Math.hypot(b[0] - a[0], b[1] - a[1]))} mm`;
  }
  if (s.kind === "rect") {
    const [a, , c] = s.points;
    return `${round1(Math.abs(c[0] - a[0]))} × ${round1(Math.abs(c[1] - a[1]))} mm`;
  }
  if (s.kind === "circle" && s.radius_mm) return `Ø${round1(s.radius_mm * 2)} mm`;
  return null;
}
